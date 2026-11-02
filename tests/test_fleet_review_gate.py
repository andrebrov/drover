"""Unit tests for the landing review gate.

Tests all required paths:
- no receipt -> refused
- CHANGES -> refused
- APPROVE same harness -> refused
- APPROVE cross-harness on the SHA -> allowed
- bean-only follow-up commit after APPROVE -> allowed
- non-bean follow-up commit after APPROVE -> refused
- self-review receipt (reviewer == author) -> refused (guardrail 1)
- non-seat reviewer receipt ('Lead', etc.) -> refused (guardrail 2)
- audit log line written for accepted receipt -> verified (guardrail 3)
- unloggable audit log -> refused (fail closed)
- author seat prefers branch identity over FLEET_SEAT env var (L2)
- empty HEAD SHA -> refused (L1)
"""
import datetime
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

# Add lib directory
lib_dir = str(Path(__file__).resolve().parents[1] / "lib")
if lib_dir not in sys.path:
    sys.path.insert(0, lib_dir)

from fleet_review_gate import check_review_gate


class TestFleetLandReviewGate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.repo = self.base / "repo"
        self.origin = self.base / "origin.git"
        self.reports = self.base / "reports"
        self.reports.mkdir(parents=True)
        self._env = dict(os.environ)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(self._env)))
        os.environ["FLEET_STATE"] = str(self.base / "state")   # the audit log never lands in a real fleet state
        os.environ.pop("FLEET_SEAT", None)

        # Setup bare origin
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.origin)], check=True)

        # Setup clone
        subprocess.run(["git", "clone", "-q", str(self.origin), str(self.repo)], check=True)
        self.git("config", "user.name", "Agy Coder")
        self.git("config", "user.email", "agy-coder@example.com")

        # Initial commit on main
        (self.repo / "README.md").write_text("# Project\n")
        self.git("add", "README.md")
        self.git("commit", "-qm", "init")
        self.git("push", "-q", "origin", "HEAD:main")

        self.bean = "bean-test"
        self.roster = "claude-reviewer:claude agy-reviewer:agy agy-coder:agy codex-reviewer:codex opencode-reviewer:opencode"

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.repo), *args], text=True).strip()

    def make_commit(self, file_path, content, msg="commit"):
        p = self.repo / file_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        self.git("add", file_path)
        self.git("commit", "-qm", msg)
        return self.git("rev-parse", "HEAD")

    def test_bean_only_landing_allowed_without_receipt(self):
        """A landing changing only files under .beans/ does not require review."""
        self.make_commit(f".beans/{self.bean}--task.md", "status: completed\n", "chore: bean update")
        ok, msg, path = check_review_gate(self.repo, self.bean, reports_dir=self.reports, fleet_roster=self.roster)
        self.assertTrue(ok)
        self.assertIn("bean-file-only", msg)
        self.assertIsNone(path)

    def test_no_receipt_refused(self):
        """Source-changing landing without review receipt is refused."""
        self.make_commit("src/service.ts", "export const foo = 1;\n", "feat: new service")
        ok, msg, path = check_review_gate(self.repo, self.bean, reports_dir=self.reports, fleet_roster=self.roster)
        self.assertFalse(ok)
        self.assertIn(f"no review receipt found for {self.bean}", msg)
        self.assertIsNone(path)

    def test_changes_verdict_refused(self):
        """Review receipt with CHANGES verdict is refused."""
        sha = self.make_commit("src/service.ts", "export const foo = 1;\n", "feat: new service")
        receipt = self.reports / f"claude-reviewer-review-{self.bean}-by-agy-coder.md"
        receipt.write_text(f"""# Review {self.bean} — {sha}

Verdict: CHANGES. Blocker in service.ts.
- HIGH missing error handling

VERDICT: CHANGES
""")
        ok, msg, path = check_review_gate(self.repo, self.bean, reports_dir=self.reports, fleet_roster=self.roster)
        self.assertFalse(ok)
        self.assertIn("verdict is CHANGES", msg)

    def test_same_harness_approve_refused(self):
        """Review receipt with APPROVE from same harness is refused."""
        sha = self.make_commit("src/service.ts", "export const foo = 1;\n", "feat: new service")
        receipt = self.reports / f"agy-reviewer-review-{self.bean}-by-agy-coder.md"
        receipt.write_text(f"""# Review {self.bean} — {sha}
Tip: `{sha}`

VERDICT: APPROVE
""")
        ok, msg, path = check_review_gate(self.repo, self.bean, reports_dir=self.reports, fleet_roster=self.roster)
        self.assertFalse(ok)
        self.assertIn("same harness", msg)

    def test_cross_harness_approve_on_sha_allowed(self):
        """Review receipt with APPROVE from different harness on exact SHA is allowed."""
        sha = self.make_commit("src/service.ts", "export const foo = 1;\n", "feat: new service")
        receipt = self.reports / f"claude-reviewer-review-{self.bean}-by-agy-coder.md"
        receipt.write_text(f"""# Review {self.bean} — {sha}
Reviewed commit: {sha}

BEAN: MET — all
LANDABLE: YES — none
VERDICT: APPROVE
""")
        ok, msg, path = check_review_gate(self.repo, self.bean, reports_dir=self.reports, fleet_roster=self.roster)
        self.assertTrue(ok)
        self.assertIn("review gate passed", msg)
        self.assertEqual(path, str(receipt))

    def test_bean_only_followup_commit_after_approve_allowed(self):
        """Follow-up commit modifying only .beans/ after cross-harness APPROVE is allowed."""
        reviewed_sha = self.make_commit("src/service.ts", "export const foo = 1;\n", "feat: new service")
        receipt = self.reports / f"claude-reviewer-review-{self.bean}-by-agy-coder.md"
        receipt.write_text(f"""# Review {self.bean} — {reviewed_sha}
Tip: {reviewed_sha}

VERDICT: APPROVE
""")
        self.make_commit(f".beans/{self.bean}--task.md", "- [x] verified\n", "chore(beans): update checklist")

        ok, msg, path = check_review_gate(self.repo, self.bean, reports_dir=self.reports, fleet_roster=self.roster)
        self.assertTrue(ok)
        self.assertIn("follow-up commits bean-only", msg)
        self.assertEqual(path, str(receipt))

    def test_non_bean_followup_commit_after_approve_refused(self):
        """Follow-up commit modifying code after APPROVE is refused."""
        reviewed_sha = self.make_commit("src/service.ts", "export const foo = 1;\n", "feat: new service")
        receipt = self.reports / f"claude-reviewer-review-{self.bean}-by-agy-coder.md"
        receipt.write_text(f"""# Review {self.bean} — {reviewed_sha}
Tip: {reviewed_sha}

VERDICT: APPROVE
""")
        self.make_commit("src/other.ts", "export const other = 2;\n", "feat: sneaky addition")

        ok, msg, path = check_review_gate(self.repo, self.bean, reports_dir=self.reports, fleet_roster=self.roster)
        self.assertFalse(ok)
        self.assertIn("unreviewed changes between reviewed commit", msg)

    def test_self_review_reviewer_equals_author_refused(self):
        """Review receipt whose reviewer seat equals author is refused (guardrail 1)."""
        sha = self.make_commit("src/service.ts", "export const foo = 1;\n", "feat: new service")
        # Filename reviewer equals coder
        receipt = self.reports / f"agy-coder-review-{self.bean}-by-agy-coder.md"
        receipt.write_text(f"""# Review {self.bean} — {sha}
REVIEWED: {sha}
VERDICT: APPROVE
""")
        ok, msg, path = check_review_gate(self.repo, self.bean, reports_dir=self.reports, fleet_roster=self.roster)
        self.assertFalse(ok)
        self.assertIn("reviewer seat 'agy-coder' equals author", msg)

        # Worktree / env author equals reviewer
        receipt.unlink()
        os.environ["FLEET_SEAT"] = "claude-reviewer"
        try:
            receipt2 = self.reports / f"claude-reviewer-review-{self.bean}-by-codex-coder.md"
            receipt2.write_text(f"""# Review {self.bean} — {sha}
REVIEWED: {sha}
VERDICT: APPROVE
""")
            ok, msg, path = check_review_gate(self.repo, self.bean, reports_dir=self.reports, fleet_roster=self.roster)
            self.assertFalse(ok)
            self.assertIn("reviewer seat 'claude-reviewer' equals author", msg)
        finally:
            os.environ.pop("FLEET_SEAT", None)

    def test_author_seat_prefers_branch_over_env_var(self):
        """Author seat resolution prefers git branch over FLEET_SEAT environment variable (L2)."""
        self.git("checkout", "-qb", f"fleet/codex-coder-{self.bean}")
        try:
            sha = self.make_commit("src/service.ts", "export const foo = 1;\n", "feat: new service")
            receipt = self.reports / f"claude-reviewer-review-{self.bean}-by-codex-coder.md"
            receipt.write_text(f"""# Review {self.bean} — {sha}
REVIEWED: {sha}
VERDICT: APPROVE
""")
            # If lead/landing process sets FLEET_SEAT to something else, author seat is from branch
            os.environ["FLEET_SEAT"] = "some-pm"
            try:
                ok, msg, path = check_review_gate(self.repo, self.bean, reports_dir=self.reports, fleet_roster=self.roster)
                self.assertTrue(ok, f"expected branch author 'codex-coder' to take priority over FLEET_SEAT 'some-pm': {msg}")
                self.assertIn("review gate passed", msg)
            finally:
                os.environ.pop("FLEET_SEAT", None)
        finally:
            self.git("checkout", "-q", "main")

    def test_lead_reviewer_name_refused(self):
        """Review receipt with 'Lead' as reviewer is refused (guardrail 2: incident fix)."""
        sha = self.make_commit("src/service.ts", "export const foo = 1;\n", "feat: new service")
        # Form 1: lead-review-*.md, a hand-written "lead" receipt with no coder in its name
        receipt1 = self.reports / f"lead-review-{self.bean}-{sha[:10]}.md"
        receipt1.write_text(f"""# Lead execution review — {self.bean} commit {sha}
VERDICT: APPROVE
""")
        ok, msg, path = check_review_gate(self.repo, self.bean, reports_dir=self.reports, fleet_roster=self.roster)
        self.assertFalse(ok)

        # Form 2: Lead-review-*-by-*.md
        receipt1.unlink()
        receipt2 = self.reports / f"lead-review-{self.bean}-by-agy-coder.md"
        receipt2.write_text(f"""# Review {self.bean} — {sha}
REVIEWED: {sha}
VERDICT: APPROVE
""")
        ok, msg, path = check_review_gate(self.repo, self.bean, reports_dir=self.reports, fleet_roster=self.roster)
        self.assertFalse(ok)
        self.assertIn("non-seat names such as 'lead' are refused", msg)

    def test_non_seat_reviewer_name_refused(self):
        """Review receipt naming a non-seat reviewer not in roster is refused (guardrail 2)."""
        sha = self.make_commit("src/service.ts", "export const foo = 1;\n", "feat: new service")
        receipt = self.reports / f"fake-reviewer-review-{self.bean}-by-agy-coder.md"
        receipt.write_text(f"""# Review {self.bean} — {sha}
REVIEWED: {sha}
VERDICT: APPROVE
""")
        ok, msg, path = check_review_gate(self.repo, self.bean, reports_dir=self.reports, fleet_roster=self.roster)
        self.assertFalse(ok)
        self.assertIn("reviewer 'fake-reviewer' is not a roster seat", msg)

    def test_audit_log_written_for_accepted_receipt(self):
        """Audit log line with path, sha256, mtime, gate time is written per accepted receipt (guardrail 3)."""
        state_dir = self.base / "fleet-state"
        os.environ["FLEET_STATE"] = str(state_dir)
        try:
            sha = self.make_commit("src/service.ts", "export const foo = 1;\n", "feat: new service")
            receipt = self.reports / f"claude-reviewer-review-{self.bean}-by-agy-coder.md"
            content = f"""# Review {self.bean} — {sha}
REVIEWED: {sha}
VERDICT: APPROVE
"""
            receipt.write_text(content)
            expected_sha256 = hashlib.sha256(receipt.read_bytes()).hexdigest()

            ok, msg, path = check_review_gate(self.repo, self.bean, reports_dir=self.reports, fleet_roster=self.roster)
            self.assertTrue(ok)
            self.assertIn("review gate passed", msg)

            log_file = state_dir / "review-audit.log"
            self.assertTrue(log_file.is_file())
            log_content = log_file.read_text()
            lines = [l for l in log_content.splitlines() if l.strip()]
            self.assertEqual(len(lines), 1)

            parts = lines[0].split("\t")
            self.assertEqual(len(parts), 4, f"expected 4 fields in audit log, got: {parts}")
            log_path, log_sha256, log_mtime, log_gate_time = parts
            self.assertEqual(log_path, str(receipt.resolve()))
            self.assertEqual(log_sha256, expected_sha256)
            self.assertTrue(datetime.datetime.fromisoformat(log_mtime))
            self.assertTrue(datetime.datetime.fromisoformat(log_gate_time))
        finally:
            os.environ.pop("FLEET_STATE", None)

    def test_unloggable_audit_refused(self):
        """Accepted receipt is refused if review-audit.log cannot be written (fail closed)."""
        blocked_state = self.base / "blocked-state-file"
        blocked_state.write_text("regular file\n")
        os.environ["FLEET_STATE"] = str(blocked_state)
        try:
            sha = self.make_commit("src/service.ts", "export const foo = 1;\n", "feat: new service")
            receipt = self.reports / f"claude-reviewer-review-{self.bean}-by-agy-coder.md"
            receipt.write_text(f"""# Review {self.bean} — {sha}
REVIEWED: {sha}
VERDICT: APPROVE
""")
            ok, msg, path = check_review_gate(self.repo, self.bean, reports_dir=self.reports, fleet_roster=self.roster)
            self.assertFalse(ok)
            self.assertIn("audit logging failed", msg)
        finally:
            os.environ.pop("FLEET_STATE", None)

    def test_env_var_without_receipt_refused(self):
        """Setting FLEET_LAND_REVIEW_OK=1 without a review receipt is refused (HIGH 2)."""
        os.environ["FLEET_LAND_REVIEW_OK"] = "1"
        try:
            self.make_commit("src/service.ts", "export const foo = 1;\n", "feat: new service")
            ok, msg, path = check_review_gate(self.repo, self.bean, reports_dir=self.reports, fleet_roster=self.roster)
            self.assertFalse(ok)
            self.assertIn(f"no review receipt found for {self.bean}", msg)
        finally:
            os.environ.pop("FLEET_LAND_REVIEW_OK", None)

    def test_rebase_onto_moved_main_patch_identical_allowed(self):
        """Approve commit X, move main, rebase -> patch-identical allowed (H1)."""
        sha = self.make_commit("src/feature.ts", "export const f = 1;\n", "feat: feature")
        receipt = self.reports / f"claude-reviewer-review-{self.bean}-by-agy-coder.md"
        receipt.write_text(f"""# Review {self.bean} — {sha}
VERDICT: APPROVE
""")
        # Move main on origin
        temp_clone = self.base / "temp-clone"
        subprocess.run(["git", "clone", "-q", "-b", "main", str(self.origin), str(temp_clone)], check=True)
        (temp_clone / "unrelated.txt").write_text("hello\n")
        subprocess.run(["git", "-C", str(temp_clone), "add", "unrelated.txt"], check=True)
        subprocess.run(["git", "-C", str(temp_clone), "commit", "-qm", "unrelated change on main"], check=True)
        subprocess.run(["git", "-C", str(temp_clone), "push", "-q", "origin", "main"], check=True)

        # In repo: fetch origin and rebase onto origin/main
        self.git("fetch", "origin")
        self.git("rebase", "origin/main")
        rebased_sha = self.git("rev-parse", "HEAD")
        self.assertNotEqual(sha, rebased_sha)

        ok, msg, path = check_review_gate(self.repo, self.bean, reports_dir=self.reports, fleet_roster=self.roster)
        self.assertTrue(ok)
        self.assertIn("patch-identical after rebase", msg)

    def test_rebase_onto_moved_main_with_code_change_refused(self):
        """Approve commit X, move main, rebase with code change -> refused (H1)."""
        sha = self.make_commit("src/feature.ts", "export const f = 1;\n", "feat: feature")
        receipt = self.reports / f"claude-reviewer-review-{self.bean}-by-agy-coder.md"
        receipt.write_text(f"""# Review {self.bean} — {sha}
VERDICT: APPROVE
""")
        # Move main on origin
        temp_clone = self.base / "temp-clone2"
        subprocess.run(["git", "clone", "-q", "-b", "main", str(self.origin), str(temp_clone)], check=True)
        (temp_clone / "unrelated.txt").write_text("hello\n")
        subprocess.run(["git", "-C", str(temp_clone), "add", "unrelated.txt"], check=True)
        subprocess.run(["git", "-C", str(temp_clone), "commit", "-qm", "unrelated change on main"], check=True)
        subprocess.run(["git", "-C", str(temp_clone), "push", "-q", "origin", "main"], check=True)

        # In repo: fetch origin, rebase and amend code change
        self.git("fetch", "origin")
        self.git("rebase", "origin/main")
        (self.repo / "src/feature.ts").write_text("export const f = 2;\n")
        self.git("add", "src/feature.ts")
        self.git("commit", "--amend", "-qm", "altered feature")

        ok, msg, path = check_review_gate(self.repo, self.bean, reports_dir=self.reports, fleet_roster=self.roster)
        self.assertFalse(ok)
        self.assertIn("not a descendant or patch-identical", msg)

    def test_whitespace_mutation_after_rebase_refused(self):
        """Approve guard with indentation, rebase and change indentation -> refused (HIGH 1)."""
        original_code = "def allowed(active):\n    allowed = False\n    if active:\n        pass\n        allowed = True\n    return allowed\n"
        sha = self.make_commit("guard.py", original_code, "reviewed guard")
        receipt = self.reports / f"claude-reviewer-review-{self.bean}-by-agy-coder.md"
        receipt.write_text(f"""# Review {self.bean} — {sha}
REVIEWED: {sha}
VERDICT: APPROVE
""")
        # Move main on origin
        temp_clone = self.base / "temp-clone-ws"
        subprocess.run(["git", "clone", "-q", "-b", "main", str(self.origin), str(temp_clone)], check=True)
        (temp_clone / "unrelated.txt").write_text("hello\n")
        subprocess.run(["git", "-C", str(temp_clone), "add", "unrelated.txt"], check=True)
        subprocess.run(["git", "-C", str(temp_clone), "commit", "-qm", "unrelated change on main"], check=True)
        subprocess.run(["git", "-C", str(temp_clone), "push", "-q", "origin", "main"], check=True)

        # In repo: fetch origin, rebase and alter indentation
        self.git("fetch", "origin")
        self.git("rebase", "origin/main")
        mutated_code = original_code.replace("        allowed = True", "    allowed = True")
        (self.repo / "guard.py").write_text(mutated_code)
        self.git("add", "guard.py")
        self.git("commit", "--amend", "-qm", "unreviewed indentation change")

        ok, msg, path = check_review_gate(self.repo, self.bean, reports_dir=self.reports, fleet_roster=self.roster)
        self.assertFalse(ok)
        self.assertIn("not a descendant or patch-identical", msg)

    def test_file_relocation_after_rebase_refused(self):
        """Approve file at guard.py, rebase and relocate to unused.py -> refused (HIGH 1)."""
        code = "def allowed(active):\n    return bool(active)\n"
        sha = self.make_commit("guard.py", code, "reviewed guard")
        receipt = self.reports / f"claude-reviewer-review-{self.bean}-by-agy-coder.md"
        receipt.write_text(f"""# Review {self.bean} — {sha}
REVIEWED: {sha}
VERDICT: APPROVE
""")
        # Move main on origin
        temp_clone = self.base / "temp-clone-reloc"
        subprocess.run(["git", "clone", "-q", "-b", "main", str(self.origin), str(temp_clone)], check=True)
        (temp_clone / "unrelated.txt").write_text("hello\n")
        subprocess.run(["git", "-C", str(temp_clone), "add", "unrelated.txt"], check=True)
        subprocess.run(["git", "-C", str(temp_clone), "commit", "-qm", "unrelated change on main"], check=True)
        subprocess.run(["git", "-C", str(temp_clone), "push", "-q", "origin", "main"], check=True)

        # In repo: fetch origin, rebase and relocate file
        self.git("fetch", "origin")
        self.git("rebase", "origin/main")
        self.git("mv", "guard.py", "unused.py")
        self.git("commit", "--amend", "-qm", "relocated guard to unused")

        ok, msg, path = check_review_gate(self.repo, self.bean, reports_dir=self.reports, fleet_roster=self.roster)
        self.assertFalse(ok)
        self.assertIn("not a descendant or patch-identical", msg)

    def test_binary_replacement_after_rebase_refused(self):
        """Approve binary payload, rebase and replace binary bytes -> refused (HIGH 1)."""
        reviewed_bytes = bytes.fromhex("0072657669657765642d636f6e74656e7401")
        (self.repo / "payload.bin").write_bytes(reviewed_bytes)
        self.git("add", "payload.bin")
        self.git("commit", "-qm", "reviewed binary payload")
        sha = self.git("rev-parse", "HEAD")

        receipt = self.reports / f"claude-reviewer-review-{self.bean}-by-agy-coder.md"
        receipt.write_text(f"""# Review {self.bean} — {sha}
REVIEWED: {sha}
VERDICT: APPROVE
""")
        # Move main on origin
        temp_clone = self.base / "temp-clone-bin"
        subprocess.run(["git", "clone", "-q", "-b", "main", str(self.origin), str(temp_clone)], check=True)
        (temp_clone / "unrelated.txt").write_text("hello\n")
        subprocess.run(["git", "-C", str(temp_clone), "add", "unrelated.txt"], check=True)
        subprocess.run(["git", "-C", str(temp_clone), "commit", "-qm", "unrelated change on main"], check=True)
        subprocess.run(["git", "-C", str(temp_clone), "push", "-q", "origin", "main"], check=True)

        # In repo: fetch origin, rebase and replace binary content
        self.git("fetch", "origin")
        self.git("rebase", "origin/main")
        mutated_bytes = bytes.fromhex("00554e52455649455745442d434f4e54454e5402")
        (self.repo / "payload.bin").write_bytes(mutated_bytes)
        self.git("add", "payload.bin")
        self.git("commit", "--amend", "-qm", "unreviewed binary payload mutation")

        ok, msg, path = check_review_gate(self.repo, self.bean, reports_dir=self.reports, fleet_roster=self.roster)
        self.assertFalse(ok)
        self.assertIn("not a descendant or patch-identical", msg)

    def test_reviewed_machine_readable_footer_line_allowed(self):
        """Receipt with explicit machine-readable REVIEWED: <sha> footer is allowed (H2)."""
        sha = self.make_commit("src/service.ts", "export const foo = 1;\n", "feat: new service")
        receipt = self.reports / f"claude-reviewer-review-{self.bean}-by-agy-coder.md"
        receipt.write_text(f"""# Arbitrary Freeform Title For Bean

Some paragraphs discussing findings...

REVIEWED: {sha}
BEAN: MET — all
LANDABLE: YES — none
VERDICT: APPROVE
""")
        ok, msg, path = check_review_gate(self.repo, self.bean, reports_dir=self.reports, fleet_roster=self.roster)
        self.assertTrue(ok)
        self.assertIn("review gate passed", msg)

    def test_newer_changes_refuses_even_if_older_approve_exists(self):
        """Newer CHANGES receipt on same SHA blocks landing despite older APPROVE (M1)."""
        sha = self.make_commit("src/service.ts", "export const foo = 1;\n", "feat: new service")

        # Older APPROVE from opencode-reviewer
        approve_receipt = self.reports / f"opencode-reviewer-review-{self.bean}-by-agy-coder.md"
        approve_receipt.write_text(f"""# Review {self.bean} — {sha}
VERDICT: APPROVE
""")
        t0 = time.time() - 10
        os.utime(approve_receipt, (t0, t0))

        # Newer CHANGES from claude-reviewer
        changes_receipt = self.reports / f"claude-reviewer-review-{self.bean}-by-agy-coder.md"
        changes_receipt.write_text(f"""# Review {self.bean} — {sha}
## HIGH
- critical bug

VERDICT: CHANGES
""")
        t1 = time.time()
        os.utime(changes_receipt, (t1, t1))

        ok, msg, path = check_review_gate(self.repo, self.bean, reports_dir=self.reports, fleet_roster=self.roster)
        self.assertFalse(ok)
        self.assertIn("verdict is CHANGES for landing content", msg)


class TestLandBeanRunsTheGate(unittest.TestCase):
    """fleet-land-bean refuses an approved bean whose branch has no covering receipt, and lands it once one exists."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.repo, self.reports, self.home = base / "repo", base / "reports", base / "home"
        self.reports.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.repo)], check=True)
        for k, v in (("user.name", "t"), ("user.email", "t@example.com")):
            subprocess.run(["git", "-C", str(self.repo), "config", k, v], check=True)
        (self.repo / "README.md").write_text("x\n")
        subprocess.run(["git", "-C", str(self.repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "init"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "branch", "fleet/s-coder-bean-gate"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "worktree", "add", "-q", str(base / "wt"), "fleet/s-coder-bean-gate"], check=True)
        (base / "wt" / "src.txt").write_text("code\n")
        subprocess.run(["git", "-C", str(base / "wt"), "add", "."], check=True)
        subprocess.run(["git", "-C", str(base / "wt"), "commit", "-qm", "feat: code"], check=True)
        self.sha = subprocess.check_output(["git", "-C", str(base / "wt"), "rev-parse", "HEAD"], text=True).strip()
        self.env = dict(os.environ, DROVER_REPO=str(self.repo), DROVER_HOME=str(self.home), DROVER_CONFIG="/nonexistent",
                        FLEET_REPORTS=str(self.reports), FLEET_STATE=str(self.home / "state"), HOME=str(base),
                        DROVER_ROSTER="s-coder:claude s-reviewer:codex", DROVER_BEAN_PREFIX="bean-")
        self.env.pop("DROVER_REVIEW_GATE", None)

    def land(self):
        tool = Path(__file__).resolve().parents[1] / "bin" / "fleet-land-bean"
        return subprocess.run([str(tool), "bean-gate"], capture_output=True, text=True, env=self.env, timeout=60)

    def test_no_receipt_refuses_and_a_covering_receipt_lands(self):
        r = self.land()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no review receipt found for bean-gate", r.stdout)
        self.assertEqual(subprocess.check_output(["git", "-C", str(self.repo), "rev-list", "--count", "main"], text=True).strip(), "1")
        (self.reports / "s-reviewer-review-bean-gate-by-s-coder.md").write_text(f"REVIEWED: {self.sha}\nVERDICT: APPROVE\n")
        r = self.land()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("LANDED bean-gate", r.stdout)
        self.assertTrue((self.home / "state" / "review-audit.log").exists())


if __name__ == "__main__":
    unittest.main()
