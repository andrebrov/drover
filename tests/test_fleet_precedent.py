#!/usr/bin/env python3
"""bean-w8or - fleet-precedent: the 2-3 most similar LANDED beans for a brief, and nothing when there is none.

A real fixture repository (4 candidate beans: 2 similar landed, 1 completed-but-unlanded, 1 unrelated) plus
a review report. Nothing here reaches the network.

  python3 -m unittest bundle/tests/test_fleet_precedent.py
"""
import os
import re
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
BIN = os.path.join(HERE, "..", "bin", "fleet-precedent")


def git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True, text=True).stdout.strip()


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def bean_text(bid, slug, title, status, created, body):
    return ("---\n# %s\ntitle: '%s'\nstatus: %s\ntype: task\npriority: normal\n"
            "created_at: %s\nupdated_at: %s\n---\n\n%s\n" % (bid, title, status, created, created, body))


class Fixture:
    """A git repo with an origin/main board, a review-report dir, and the environment to run the tool."""

    def __init__(self, long_summary=False, extra_future=False, extra_candidates=(), secret=False):
        self.tmp = tempfile.mkdtemp(prefix="fleet-precedent-test-")
        self.repo = os.path.join(self.tmp, "repo")
        beans = os.path.join(self.repo, ".beans")
        big = "## Summary of Changes\n" + "".join("- change %d\n" % i for i in range(200))
        summary1 = big if long_summary else "## Summary of Changes\n- added the loader\n- wired it into the registry\n"
        if secret:
            summary1 += '- set PASSWORD="hunter2secret" in .env\n- mail ops@example.com\n'
        summary2 = big if long_summary else "## Summary of Changes\n- added the registry\n"
        write(os.path.join(beans, "bean-skl1--skills-loader.md"), bean_text(
            "bean-skl1", "skills-loader", "Add a skills loader in backend/src/services/skills", "completed",
            "2026-01-01T00:00:00Z", "Touch backend/src/services/skills/loader.ts.\n" + summary1))
        write(os.path.join(beans, "bean-skl2--skills-registry.md"), bean_text(
            "bean-skl2", "skills-registry", "Extend the skills registry", "completed", "2026-01-03T00:00:00Z",
            "Touch backend/src/services/skills/registry.ts.\n" + summary2))
        write(os.path.join(beans, "bean-unld--skills-unlanded.md"), bean_text(
            "bean-unld", "skills-unlanded", "Rework the skills loader", "completed", "2026-01-05T00:00:00Z",
            "Touch backend/src/services/skills/loader.ts again.\n## Summary of Changes\n- unlanded attempt\n"))
        write(os.path.join(beans, "bean-unrl--pricing-page.md"), bean_text(
            "bean-unrl", "pricing-page", "Fix the pricing table on the marketing page", "completed",
            "2026-01-06T00:00:00Z",
            "See backend/src/legacy/pricing.ts.\n## Summary of Changes\n- pricing tweak\n"))
        write(os.path.join(beans, "bean-subj--skills-new.md"), bean_text(
            "bean-subj", "skills-new", "Add a skills cache in backend/src/services/skills", "todo",
            "2026-02-01T00:00:00Z", "Touch backend/src/services/skills/cache.ts.\n"))
        extras = list(extra_candidates)
        if extra_future:
            extras.append(("bean-futr", "skills-future", "Add another skills helper", "2026-03-01T00:00:00Z",
                           "Touch backend/src/services/skills/helper.ts.\n## Summary of Changes\n- helper\n"))
        for bid, slug, title, created, body in extras:
            write(os.path.join(beans, "%s--%s.md" % (bid, slug)), bean_text(
                bid, slug, title, "completed", created, body))

        write(os.path.join(self.repo, "backend/src/services/skills/loader.ts"), "export const loader = 1;\n")
        write(os.path.join(self.repo, "backend/src/services/skills/registry.ts"), "export const registry = 1;\n")
        write(os.path.join(self.repo, "backend/src/legacy/pricing.ts"), "export const pricing = 1;\n")
        write(os.path.join(self.repo, "backend/src/services/skills/cache.ts"), "export const cache = 1;\n")
        write(os.path.join(self.repo, "backend/src/services/skills/helper.ts"), "export const helper = 1;\n")
        write(os.path.join(self.repo, "backend/src/services/skills/other.ts"), "export const other = 1;\n")

        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.email", "t@t")
        git(self.repo, "config", "user.name", "t")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "seed beans and sources")

        def land(path, text, subject):
            write(os.path.join(self.repo, path), text)
            git(self.repo, "add", "-A")
            git(self.repo, "commit", "-qm", subject)
            return git(self.repo, "rev-parse", "HEAD")

        self.landed = land("backend/src/services/skills/loader.ts", "export const loader = 2;\n",
                           "bean-skl1: add the skills loader")
        self.landed2 = land("backend/src/services/skills/registry.ts", "export const registry = 2;\n",
                            "bean-skl2: add the skills registry")
        for bid, slug, title, created, body in extras:
            land("backend/src/services/skills/other.ts", "export const other = %s;\n" % bid[-1],
                 "%s: %s" % (bid, title))
        # a completed-but-unlanded bean: its commit exists only on a fleet branch, never on origin/main
        origin = os.path.join(self.tmp, "origin.git")
        subprocess.run(["git", "init", "-q", "--bare", origin], check=True, capture_output=True)
        git(self.repo, "remote", "add", "origin", origin)
        git(self.repo, "push", "-q", "origin", "main")
        git(self.repo, "checkout", "-q", "-b", "fleet/x-bean-unld")
        write(os.path.join(self.repo, "backend/src/services/skills/other.ts"), "export const other = 2;\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "bean-unld: rework the skills loader")
        self.unlanded = git(self.repo, "rev-parse", "HEAD")

        self.reports = os.path.join(self.tmp, "reports")
        finding1 = ("HIGH - leaked ghp_abcdefghijkl1234 in the loader" if secret
                    else "HIGH - loader swallows the registry error")
        write(os.path.join(self.reports, "rev1-review-bean-skl1-by-claude-coder.md"),
              "# review of bean-skl1\nBranch tip: %s\n\n## %s\n"
              "VERDICT: CHANGES\n" % (self.landed, finding1))
        # a CHANGES round on an earlier revision, superseded by an APPROVE of the landed one
        write(os.path.join(self.reports, "rev2-review-bean-skl2-by-claude-coder.md"),
              "# review of bean-skl2\nBranch tip: %s\n\n## HIGH - registry keys are case sensitive\n"
              "VERDICT: CHANGES\n" % ("1" * 40))
        write(os.path.join(self.reports, "rev3-review-bean-skl2-by-claude-coder.md"),
              "# review of bean-skl2\nBranch tip: %s\n\n## MEDIUM - docs lag\nVERDICT: APPROVE\n"
              % self.landed2)
        os.utime(os.path.join(self.reports, "rev2-review-bean-skl2-by-claude-coder.md"), (1000, 1000))
        os.utime(os.path.join(self.reports, "rev3-review-bean-skl2-by-claude-coder.md"), (2000, 2000))

        self.env = dict(os.environ, FLEET_ROOT=self.repo, FLEET_REPORTS=self.reports, HOME=self.tmp)
        self.env.pop("FLEET_PRECEDENT", None)

    def run(self, *args, env=None):
        r = subprocess.run([sys.executable, BIN, *args], capture_output=True, text=True, timeout=180,
                           env=env or self.env)
        return r.returncode, r.stdout

    def close(self):
        subprocess.run(["rm", "-rf", self.tmp], check=False)


class Precedent(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()

    def tearDown(self):
        self.fx.close()

    def test_similar_landed_beans_are_listed_with_their_landing_shas(self):
        code, out = self.fx.run("bean-subj")
        self.assertEqual(code, 0, out)
        self.assertIn("### bean-skl1 - Add a skills loader in backend/src/services/skills", out)
        self.assertIn("### bean-skl2 - Extend the skills registry", out)
        self.assertIn(self.fx.landed[:12], out)
        self.assertIn(self.fx.landed2[:12], out)
        self.assertIn("ancestor of origin/main", out)

    def test_a_completed_but_unlanded_bean_is_excluded(self):
        _, out = self.fx.run("bean-subj")
        self.assertNotIn("bean-unld", out)
        self.assertNotIn(self.fx.unlanded[:12], out)

    def test_an_unrelated_bean_is_absent(self):
        _, out = self.fx.run("bean-subj")
        self.assertNotIn("bean-unrl", out)
        self.assertNotIn("pricing", out)

    def test_no_similar_landed_bean_prints_nothing(self):
        code, out = self.fx.run("bean-subj", "--title", "Rotate the billing webhook signing secret",
                                "--paths", "backend/src/billing/webhook.ts")
        self.assertEqual((code, out), (0, ""))

    def test_the_bean_is_not_its_own_precedent(self):
        _, out = self.fx.run("bean-skl1")
        self.assertNotIn("### bean-skl1", out)

    def test_a_younger_landed_bean_is_not_a_retrospective(self):
        fx = Fixture(extra_future=True)
        try:
            _, out = fx.run("bean-subj")
            self.assertIn("### bean-skl1", out)
            self.assertNotIn("bean-futr", out)
        finally:
            fx.close()

    def test_provenance_is_printed_for_the_artifact_and_the_review_revision(self):
        _, out = self.fx.run("bean-subj")
        self.assertIn(".beans/bean-skl1--skills-loader.md (on origin/main)", out)
        self.assertIn("rev1-review-bean-skl1-by-claude-coder.md", out)
        self.assertIn("reviewed revision %s" % self.fx.landed, out)
        self.assertIn("HIGH - loader swallows the registry error", out)
        self.assertIn("unresolved CHANGES", out)

    def test_a_superseded_changes_review_is_labelled_fixed_in_the_landed_revision(self):
        _, out = self.fx.run("bean-subj")
        block = out.split("### bean-skl2")[1]
        self.assertIn("fixed in the landed revision", block)
        self.assertIn("HIGH - registry keys are case sensitive", block)

    def test_every_excerpt_is_marked_historical_and_subordinate(self):
        _, out = self.fx.run("bean-subj")
        self.assertIn("HISTORICAL EVIDENCE ONLY", out)
        self.assertIn("subordinate to this bean's current requirements", out)

    def test_the_summary_of_changes_is_printed_verbatim(self):
        _, out = self.fx.run("bean-subj")
        self.assertIn("- added the loader", out)
        self.assertIn("- wired it into the registry", out)

    def test_credentials_and_addresses_in_the_excerpts_are_redacted(self):
        # The block lands in a coder's prompt, so agent-written prose is not forwarded there raw
        # (bean-hkvd): the excerpt is copied with credential- and address-shaped strings removed, and the
        # complete artifact is named instead.
        fx = Fixture(secret=True)
        try:
            _, out = fx.run("bean-subj")
            for leaked in ("hunter2secret", "ops@example.com", "ghp_abcdefghijkl1234"):
                self.assertNotIn(leaked, out)
            self.assertIn(".beans/bean-skl1--skills-loader.md (on origin/main)", out)
            self.assertIn("credential- and address-shaped strings removed", out)
        finally:
            fx.close()

    def test_output_is_capped_with_a_truncation_marker(self):
        big = "## Summary of Changes\n" + "".join("- change %d\n" % i for i in range(200))
        fx = Fixture(long_summary=True, extra_candidates=[
            ("bean-skl3", "skills-extra", "Extend the skills loader again", "2026-01-04T00:00:00Z",
             "Touch backend/src/services/skills/other.ts.\n" + big)])
        try:
            _, out = fx.run("bean-subj")
            self.assertIn("### bean-skl3", out)
            self.assertLessEqual(len(out.splitlines()), 150)
            self.assertIn("truncated at 150 lines", out)
        finally:
            fx.close()

    def test_switched_off_prints_nothing(self):
        env = dict(self.fx.env, FLEET_PRECEDENT="off")
        self.assertEqual(self.fx.run("bean-subj", env=env), (0, ""))

    def test_bad_arguments_exit_64(self):
        code, out = self.fx.run("../etc")
        self.assertEqual((code, out), (64, ""))

    def test_it_is_read_only(self):
        before = git(self.fx.repo, "rev-parse", "origin/main")
        self.fx.run("bean-subj")
        self.assertEqual(git(self.fx.repo, "status", "--porcelain"), "")
        self.assertEqual(git(self.fx.repo, "rev-parse", "origin/main"), before)
        self.assertEqual(git(self.fx.repo, "rev-parse", "HEAD"), self.fx.unlanded)


if __name__ == "__main__":
    unittest.main()
