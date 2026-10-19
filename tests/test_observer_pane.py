#!/usr/bin/env python3
"""Observer integration: changed-pane cache and mechanical completion proof."""

import importlib.machinery
import importlib.util
import json
import pathlib
import unittest


SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "bin" / "fleet-observe"
loader = importlib.machinery.SourceFileLoader("fleet_observe", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
observer = importlib.util.module_from_spec(spec)
loader.exec_module(observer)


class ObserverPaneTest(unittest.TestCase):
    def test_one_jev_call_per_changed_pane(self):
        cache, calls = {}, []

        def judge(text):
            calls.append(text)
            return {"state": "working", "confidence": 0.99,
                    "reason": "jev_choice"}

        first = observer.classify_changed("coder", "pane A", cache, judge)
        second = observer.classify_changed("coder", "pane A", cache, judge)
        third = observer.classify_changed("coder", "pane B", cache, judge)
        self.assertEqual(len(calls), 2)
        self.assertEqual(first, second)
        self.assertEqual(third["state"], "working")
        self.assertNotIn("pane A", str(cache))
        self.assertNotIn("pane B", str(cache))

    def test_empty_pane_is_unknown_without_jev(self):
        calls = []
        result = observer.classify_changed("coder", "", {}, lambda text: calls.append(text))
        self.assertEqual(result["state"], "unknown")
        self.assertEqual(calls, [])

    def test_timer_only_change_reuses_cached_decision(self):
        cache, calls = {}, []
        judge = lambda text: calls.append(text) or {"state": "working", "confidence": 0.99,
                                                   "reason": "jev_choice"}
        observer.classify_changed("coder", "Working (40m 03s • esc to interrupt)", cache, judge)
        observer.classify_changed("coder", "Working (40m 09s • esc to interrupt)", cache, judge)
        self.assertEqual(len(calls), 1)

    def test_background_terminal_timer_reuses_cached_decision(self):
        # codex parks a pane in "Waiting for background terminal (5m 19s …)"
        # while a build runs. Only the timer advances, so this is not a new
        # state and must not spend another Jev call.
        cache, calls = {}, []
        judge = lambda text: calls.append(text) or {"state": "working", "confidence": 0.99,
                                                   "reason": "jev_choice"}
        observer.classify_changed("coder", "• Waiting for background terminal (5m 19s • esc to interrupt) · 1 background terminal running\n  └ rtk proxy npm run build", cache, judge)
        observer.classify_changed("coder", "• Waiting for background terminal (5m 27s • esc to interrupt) · 1 background terminal running\n  └ rtk proxy npm run build", cache, judge)
        self.assertEqual(len(calls), 1)

    def test_completed_duration_is_not_normalized_away(self):
        # "Worked for 2h 6m 11s" marks a finished turn. Erasing it would make a
        # finished pane hash-match a working one and suppress the real change.
        # The clock time is held identical on both sides, so only the duration
        # can be doing the work.
        self.assertNotEqual(
            observer.pane_fingerprint("  Worked for 2h 6m 11s · 4:51 PM\n› Ask Codex to do anything"),
            observer.pane_fingerprint("  Worked for 3h 1m 4s · 4:51 PM\n› Ask Codex to do anything"))

    def test_thinking_spinner_timer_reuses_cached_decision(self):
        cache, calls = {}, []
        judge = lambda text: calls.append(text) or {"state": "working", "confidence": 0.99,
                                                   "reason": "jev_choice"}
        observer.classify_changed("coder", "• Thinking (7s)\n› Ask Codex", cache, judge)
        observer.classify_changed("coder", "• Thinking (19s)\n› Ask Codex", cache, judge)
        self.assertEqual(len(calls), 1)

    def test_token_counter_only_change_reuses_cached_decision(self):
        cache, calls = {}, []
        judge = lambda text: calls.append(text) or {"state": "working", "confidence": 0.99,
                                                   "reason": "jev_choice"}
        observer.classify_changed("coder", "⠰⠳ Running 168.87k tokens\n→ Add a follow-up", cache, judge)
        observer.classify_changed("coder", "⠴⠦ Running 168.92k tokens\n→ Add a follow-up", cache, judge)
        self.assertEqual(len(calls), 1)

    def test_stable_working_pane_records_elapsed_time(self):
        from unittest.mock import patch
        cache = {}
        judge = lambda _: {"state": "working", "confidence": 0.99,
                           "reason": "jev_choice"}
        with patch.object(observer, "NOW", 1000):
            observer.classify_changed("coder", "Working (40m 03s • esc to interrupt)", cache, judge)
        with patch.object(observer, "NOW", 3000):
            result = observer.classify_changed("coder", "Working (40m 09s • esc to interrupt)", cache, judge)
        self.assertGreaterEqual(result["pane_unchanged_seconds"], 2000)

    def test_bad_jev_json_fails_to_unknown_not_crash_observer(self):
        from unittest.mock import patch
        with patch.object(observer, "sh", return_value="not JSON"):
            result = observer.call_pane_judge("pane text")
        self.assertEqual(result["state"], "unknown")

    def test_corrupt_cache_entry_does_not_crash_batch(self):
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "cache.json"
            path.write_text('{"coder":"corrupt"}', encoding="utf-8")
            with patch.object(observer, "PANE_CACHE", str(path)), patch.object(observer, "DRY", True), \
                 patch.object(observer, "screen", return_value="Working now"), \
                 patch.object(observer, "call_pane_judge", return_value={"state": "working", "confidence": 1,
                                                                          "reason": "jev_choice"}):
                decisions = observer.pane_states({"coder": {}})
            self.assertEqual(decisions["coder"]["state"], "working")

    def test_report_claim_needs_origin_main_ancestry(self):
        sha = "a" * 40
        report = f"DONE {sha}\nPushed it.\n"
        self.assertEqual(observer.completion_proof(report, lambda _: False), "not_pushed")
        self.assertEqual(observer.completion_proof(report, lambda s: s == sha), "landed")

    def test_jev_push_claim_cannot_complete_task(self):
        # Pane Noul is deliberately not an input to completion_proof.
        self.assertEqual(observer.completion_proof("DONE deadbeef123\n", lambda _: False), "not_pushed")
        self.assertEqual(observer.completion_proof("The pane says pushed\n", lambda _: True), "none")

    def test_a_legacy_cached_push_probability_is_never_handed_back(self):
        # Behaviour, not source text: a cache row written before the push-claim
        # Noul was dropped still holds claims_pushed_probability, and it is
        # returned for every unchanged pane. It must not survive the read, or an
        # observer resumed after the upgrade still carries the retired signal.
        pane = "some pane text"
        legacy = {"state": "finished", "confidence": 0.95,
                  "claims_pushed_probability": 0.99, "reason": "jev_choice"}
        cache = {"coder": {"hash": observer.pane_fingerprint(pane), "result": legacy,
                           "observed_at": observer.NOW - 5}}
        out = observer.classify_changed("coder", pane, cache, lambda _: {"state": "finished"})
        self.assertNotIn("claims_pushed_probability", out)
        self.assertEqual(out["state"], "finished")
        self.assertEqual(out["pane_unchanged_seconds"], 5)

    def test_blocked_report_does_not_need_a_sha(self):
        self.assertEqual(observer.completion_proof("BLOCKED — needs founder choice\n", lambda _: False), "blocked")

    def test_origin_proven_report_releases_even_when_jev_abstains(self):
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            tasks = root / "tasks"
            reports = root / "reports"
            state = root / "state"
            for path in (tasks, reports, state):
                path.mkdir()
            (tasks / "coder").write_text("assignment", encoding="utf-8")
            report = reports / "coder-bean-demo.md"
            report.write_text("DONE " + "a" * 40 + "\n", encoding="utf-8")
            with patch.object(observer, "TASKS", str(tasks)), patch.object(observer, "REPORTS", str(reports)), \
                 patch.object(observer, "STATE", str(state)), patch.object(observer, "agents", return_value={"coder": {"agent": "codex"}}), \
                 patch.object(observer, "ledger", return_value=[("coder", "bean-demo", 0)]), \
                 patch.object(observer, "pane_states", return_value={"coder": {"state": "unknown"}}), \
                 patch.object(observer, "progress", return_value=(0, "fleet/coder-bean-demo")), \
                 patch.object(observer, "on_origin", return_value=True), patch.object(observer, "git", return_value=""), \
                 patch.object(observer, "sh", return_value=""), patch.object(observer, "dispatch"), \
                 patch.object(observer, "alert"), patch.object(observer, "QUEUE", str(state / "queue")), \
                 patch.object(observer, "DRY", False):
                observer.REPORT_OF.clear()
                observer.check()
            self.assertFalse((tasks / "coder").exists())

    def test_failed_dispatch_preserves_named_target(self):
        import tempfile
        from types import SimpleNamespace
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            tasks = root / "tasks"
            reports = root / "reports"
            tasks.mkdir()
            reports.mkdir()
            queue = root / "queue"
            pool = root / "pool"
            queue.write_text("bean-demo\tcoder\tdo the task\n", encoding="utf-8")
            pool.write_text("coder:named\n", encoding="utf-8")
            with patch.object(observer, "TASKS", str(tasks)), patch.object(observer, "REPORTS", str(reports)), \
                 patch.object(observer, "QUEUE", str(queue)), patch.object(observer, "POOL", str(pool)), \
                 patch.object(observer, "STATE", str(root / "state")), \
                 patch.object(observer, "ledger", return_value=[]), patch.object(observer, "DRY", False), \
                 patch.object(observer, "alert"), patch.object(observer.subprocess, "run", return_value=SimpleNamespace(returncode=1)):
                observer.dispatch({"coder": {"agent": "codex", "agent_status": "done"}}, set())
            self.assertEqual(queue.read_text(encoding="utf-8"), "bean-demo\tcoder\tdo the task\n")

    def test_stale_working_assignment_alerts_for_manual_inspection(self):
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            tasks = root / "tasks"
            reports = root / "reports"
            state = root / "state"
            for path in (tasks, reports, state):
                path.mkdir()
            (tasks / "coder").write_text("assignment", encoding="utf-8")
            alerts = []
            with patch.object(observer, "TASKS", str(tasks)), patch.object(observer, "REPORTS", str(reports)), \
                 patch.object(observer, "STATE", str(state)), patch.object(observer, "QUEUE", str(state / "queue")), \
                 patch.object(observer, "NOW", 3000), patch.object(observer, "STALL_MIN", 30), \
                 patch.object(observer, "agents", return_value={"coder": {"agent": "codex", "agent_status": "working"}}), \
                 patch.object(observer, "ledger", return_value=[("coder", "bean-demo", 0)]), \
                 patch.object(observer, "pane_states", return_value={"coder": {"state": "working", "pane_unchanged_seconds": 2000}}), \
                 patch.object(observer, "progress", return_value=(0, "fleet/coder-bean-demo")), \
                 patch.object(observer, "git", return_value=""), patch.object(observer, "sh", return_value=""), \
                 patch.object(observer, "dispatch"), patch.object(observer, "alert", side_effect=lambda key, *args: alerts.append(key)), \
                 patch.object(observer, "DRY", True):
                observer.REPORT_OF.clear()
                observer.check()
                self.assertIn("stale-working-coder-bean-demo", alerts)
                alerts.clear()
                with patch.object(observer, "progress", return_value=(2500, "fleet/coder-bean-demo")):
                    observer.check()
                self.assertNotIn("stale-working-coder-bean-demo", alerts)

    def test_production_behind_converged_returns_none(self):
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            behind_file = pathlib.Path(directory) / "observe-prod-behind.json"
            behind_file.write_text('{"live_sha": "111", "tip": "222", "first_seen": 100}', encoding="utf-8")
            sha = "1111111111111111111111111111111111111111"
            with patch.object(observer, "STATE", directory), patch.object(observer, "DRY", False):
                self.assertIsNone(observer.check_production_behind(sha, sha))
                self.assertFalse(behind_file.exists())

    def test_production_behind_recent_push_does_not_alert(self):
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            live = "1111111111111111111111111111111111111111"
            tip = "2222222222222222222222222222222222222222"
            now = 100000
            with patch.object(observer, "STATE", directory), patch.object(observer, "DRY", False):
                self.assertIsNone(observer.check_production_behind(live, tip, now))
                behind_file = pathlib.Path(directory) / "observe-prod-behind.json"
                self.assertTrue(behind_file.exists())
                data = json.loads(behind_file.read_text(encoding="utf-8"))
                self.assertEqual(data["first_seen"], now)
                self.assertEqual(data["live_sha"], live)

    def test_production_behind_old_push_returns_alert_info(self):
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            live = "1111111111111111111111111111111111111111"
            tip = "2222222222222222222222222222222222222222"
            now = 100000
            behind_file = pathlib.Path(directory) / "observe-prod-behind.json"
            behind_file.write_text(json.dumps({"live_sha": live, "tip": tip, "first_seen": now - 35 * 60}), encoding="utf-8")
            with patch.object(observer, "STATE", directory), patch.object(observer, "DRY", False), \
                 patch.object(observer, "git", return_value=tip):
                res = observer.check_production_behind(live, tip, now)
                self.assertIsNotNone(res)
                self.assertEqual(res["minutes_behind"], 35)
                self.assertEqual(res["live"], live)
                self.assertEqual(res["tip"], tip)

    def test_production_behind_preserves_first_seen_when_tip_advances(self):
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            live = "1111111111111111111111111111111111111111"
            tip1 = "2222222222222222222222222222222222222222"
            tip2 = "3333333333333333333333333333333333333333"
            now = 100000
            behind_file = pathlib.Path(directory) / "observe-prod-behind.json"
            behind_file.write_text(json.dumps({"live_sha": live, "tip": tip1, "first_seen": now - 35 * 60}), encoding="utf-8")
            with patch.object(observer, "STATE", directory), patch.object(observer, "DRY", False), \
                 patch.object(observer, "git", return_value=tip1):
                # Tip advanced to tip2, but live is still undeployed
                res = observer.check_production_behind(live, tip2, now)
                self.assertIsNotNone(res)
                self.assertEqual(res["minutes_behind"], 35)
                self.assertEqual(res["tip"], tip2)

    def test_production_behind_continuous_divergence_across_intermediate_deploy(self):
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            live1 = "1111111111111111111111111111111111111111"
            live2 = "2222222222222222222222222222222222222222"
            tip = "3333333333333333333333333333333333333333"
            now = 100000
            behind_file = pathlib.Path(directory) / "observe-prod-behind.json"
            behind_file.write_text(json.dumps({"live_sha": live1, "tip": tip, "first_seen": now - 35 * 60}), encoding="utf-8")
            with patch.object(observer, "STATE", directory), patch.object(observer, "DRY", False), \
                 patch.object(observer, "git", return_value=tip):
                # Live advanced from live1 to live2 (intermediate deploy), but still behind tip!
                # Continuous divergence preserves first_seen.
                res = observer.check_production_behind(live2, tip, now)
                self.assertIsNotNone(res)
                self.assertEqual(res["minutes_behind"], 35)
                self.assertEqual(res["live"], live2)

    def test_production_behind_probe_failure_retains_state(self):
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            tip = "3333333333333333333333333333333333333333"
            now = 100000
            behind_file = pathlib.Path(directory) / "observe-prod-behind.json"
            behind_file.write_text(json.dumps({"live_sha": None, "tip": tip, "first_seen": now - 35 * 60}), encoding="utf-8")
            with patch.object(observer, "STATE", directory), patch.object(observer, "DRY", False):
                # Probe failed: live_sha is None
                res = observer.check_production_behind(None, tip, now)
                self.assertIsNotNone(res)
                self.assertEqual(res["minutes_behind"], 35)
                self.assertTrue(behind_file.exists())

    def test_production_behind_initializes_first_seen_to_now_when_divergence_first_detected(self):
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            live = "1111111111111111111111111111111111111111"
            tip = "2222222222222222222222222222222222222222"
            now = 100000
            with patch.object(observer, "STATE", directory), patch.object(observer, "DRY", False), \
                 patch.object(observer, "git", return_value=tip):
                # First check: initializes first_seen to now, does not alert immediately (0m behind <= 30m)
                res = observer.check_production_behind(live, tip, now)
                self.assertIsNone(res)
                behind_file = pathlib.Path(directory) / "observe-prod-behind.json"
                self.assertTrue(behind_file.exists())
                data = json.loads(behind_file.read_text(encoding="utf-8"))
                self.assertEqual(data["first_seen"], now)

                # 35 minutes later: alerts with 35m behind
                res_later = observer.check_production_behind(live, tip, now + 35 * 60)
                self.assertIsNotNone(res_later)
                self.assertEqual(res_later["minutes_behind"], 35)

    def test_production_behind_does_not_clear_if_deploy_state_in_progress(self):
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            tip = "2222222222222222222222222222222222222222"
            now = 100000
            behind_file = pathlib.Path(directory) / "observe-prod-behind.json"
            behind_file.write_text(json.dumps({"live_sha": "1" * 40, "tip": tip, "first_seen": now - 35 * 60}), encoding="utf-8")
            deploy_state_file = pathlib.Path(directory) / "apprunner-deploy-state.json"
            deploy_state_file.write_text(json.dumps({"inProgressSha": tip, "startedAt": "2026-09-29T16:00:00Z"}), encoding="utf-8")
            with patch.object(observer, "STATE", directory), patch.object(observer, "DRY", False), \
                 patch.object(observer, "git", return_value=tip):
                # live equals tip (e.g. env stamp updated), but inProgressSha is still set
                res = observer.check_production_behind(tip, tip, now)
                self.assertIsNotNone(res)
                self.assertEqual(res["minutes_behind"], 35)
                self.assertTrue(behind_file.exists())


if __name__ == "__main__":
    unittest.main()
