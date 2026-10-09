import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scopeguard import engine


REPO_ROOT = Path(__file__).resolve().parents[1]


class GitRepo(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="scopeguard-test-")
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.git("init", "-q")
        self.git("config", "user.name", "ScopeGuard Test")
        self.git("config", "user.email", "scopeguard-test@example.invalid")
        (self.repo / "contracts").mkdir()
        self.contract_path = self.repo / "contracts" / "contract.json"
        self.contract = self.make_contract()
        self.write_contract()
        self.write("owned.txt", "base\n")
        self.commit("initial")
        self.baseline = engine.freeze(self.repo, self.contract_path)

    def make_contract(self, **overrides):
        contract = {
            "schema_version": 1,
            "id": "test-contract",
            "title": "ScopeGuard test contract",
            "allowed_paths": ["owned.txt", "contracts/"],
            "forbidden_paths": ["secret.txt", "private/"],
            "checks": [
                {
                    "id": "ok",
                    "argv": [sys.executable, "-c", "print('ok')"],
                    "timeout_seconds": 5,
                }
            ],
        }
        contract.update(overrides)
        return contract

    def write_contract(self):
        self.contract_path.write_text(json.dumps(self.contract), encoding="utf-8")

    def write(self, relative, content):
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.repo, check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        ).stdout

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-qm", message)

    def evaluate(self, run_checks=False):
        return engine.evaluate(self.repo, self.contract_path, self.baseline, run_checks)

    def assert_violation_mentions(self, report, fragment):
        self.assertEqual(report["verdict"], "FAIL", report)
        self.assertTrue(
            any(fragment in violation for violation in report["violations"]), report
        )

    def make_gitlink_target(self):
        temporary = tempfile.TemporaryDirectory(
            prefix="scopeguard-gitlink-", dir=self.repo.parent
        )
        self.addCleanup(temporary.cleanup)
        target = Path(temporary.name)
        subprocess.run(["git", "init", "-q"], cwd=target, check=True)
        subprocess.run(["git", "config", "user.name", "Gitlink Fixture"], cwd=target, check=True)
        subprocess.run(
            ["git", "config", "user.email", "gitlink@example.invalid"],
            cwd=target,
            check=True,
        )
        (target / "payload.txt").write_text("local fixture\n", encoding="utf-8")
        subprocess.run(["git", "add", "payload.txt"], cwd=target, check=True)
        subprocess.run(["git", "commit", "-qm", "fixture"], cwd=target, check=True)
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=target, text=True).strip()
        return sha

    def install_gitlink(self, path, sha):
        self.git("update-index", "--add", "--cacheinfo", "160000", sha, path)

    def assert_rejected_or_failed_without_checks(self, action):
        try:
            report = action()
        except ValueError:
            return
        self.assertEqual(report["verdict"], "FAIL", report)
        self.assertEqual(report["checks"], [], report)


class EngineScopeTests(GitRepo):
    def test_valid_owned_staged_and_unstaged_changes(self):
        self.write("owned.txt", "unstaged\n")
        (self.repo / "owned.txt").write_text("staged\n", encoding="utf-8")
        self.git("add", "owned.txt")
        (self.repo / "owned.txt").write_text("final\n", encoding="utf-8")

        report = self.evaluate()

        self.assertEqual(report["verdict"], "INCOMPLETE")
        self.assertIn("owned.txt", report["changed_paths"])
        self.assertEqual(report["violations"], [])

    def test_staged_unstaged_untracked_and_committed_forbidden_changes(self):
        target = self.repo / "secret.txt"
        target.write_text("staged\n", encoding="utf-8")
        self.git("add", "secret.txt")
        self.assert_violation_mentions(self.evaluate(), "secret.txt")

        self.git("reset", "--hard", "HEAD")
        target.write_text("unstaged\n", encoding="utf-8")
        self.assert_violation_mentions(self.evaluate(), "secret.txt")

        target.unlink()
        target.write_text("untracked\n", encoding="utf-8")
        self.assert_violation_mentions(self.evaluate(), "secret.txt")

        self.git("add", "secret.txt")
        self.commit("forbidden file")
        self.assert_violation_mentions(self.evaluate(), "secret.txt")

    def test_forbidden_deletion_and_rename_report_both_paths(self):
        self.write("secret.txt", "private\n")
        self.commit("add forbidden file")
        self.baseline = engine.freeze(self.repo, self.contract_path)

        (self.repo / "secret.txt").unlink()
        deleted = self.evaluate()
        self.assert_violation_mentions(deleted, "secret.txt")

        self.git("reset", "--hard", "HEAD")
        (self.repo / "secret.txt").rename(self.repo / "renamed.txt")
        renamed = self.evaluate()
        self.assert_violation_mentions(renamed, "secret.txt")
        self.assert_violation_mentions(renamed, "renamed.txt")
        self.assertIn("secret.txt", renamed["changed_paths"])
        self.assertIn("renamed.txt", renamed["changed_paths"])

    def test_contract_change_and_invalid_contracts_are_rejected(self):
        self.contract["title"] = "changed after freeze"
        self.write_contract()
        with self.assertRaises(ValueError):
            self.evaluate()

        invalid_contracts = (
            {**self.make_contract(), "unexpected": True},
            self.make_contract(allowed_paths=[]),
            self.make_contract(allowed_paths=["../outside"]),
            self.make_contract(allowed_paths=["C:/outside"]),
            self.make_contract(allowed_paths=["a\\b"]),
            self.make_contract(checks=[]),
        )
        for invalid in invalid_contracts:
            with self.subTest(contract=invalid):
                self.write_contract()
                self.contract_path.write_text(json.dumps(invalid), encoding="utf-8")
                with self.assertRaises(ValueError):
                    engine.freeze(self.repo, self.contract_path)

    def test_malformed_baseline_is_rejected(self):
        for baseline in ({}, {**self.baseline, "base_sha": "not-a-commit"}, None):
            with self.subTest(baseline=baseline):
                with self.assertRaises(ValueError):
                    engine.evaluate(self.repo, self.contract_path, baseline)

    def test_missing_check_evidence_is_incomplete_and_scope_failure_skips_checks(self):
        owned = self.write("owned.txt", "changed\n")
        report = self.evaluate(run_checks=False)
        self.assertEqual(report["verdict"], "INCOMPLETE")
        self.assertEqual(report["checks"], [])

        self.write("secret.txt", "forbidden\n")
        forbidden = self.evaluate(run_checks=True)
        self.assertEqual(forbidden["verdict"], "FAIL")
        self.assertEqual(forbidden["checks"], [])
        self.assertTrue(owned.exists())

    def test_failing_and_timed_out_checks_fail(self):
        self.contract["checks"] = [{
            "id": "failure",
            "argv": [sys.executable, "-c", "import sys; print('bad'); sys.exit(7)"],
            "timeout_seconds": 5,
        }]
        self.write_contract()
        self.commit("configure failing check")
        self.baseline = engine.freeze(self.repo, self.contract_path)
        failed = self.evaluate(run_checks=True)
        self.assertEqual(failed["verdict"], "FAIL", failed)
        self.assertEqual(failed["checks"][0]["status"], "FAIL")
        self.assertEqual(failed["checks"][0]["exit_code"], 7)

        self.contract["checks"] = [{
            "id": "timeout",
            "argv": [sys.executable, "-c", "import time; time.sleep(3)"],
            "timeout_seconds": 1,
        }]
        self.write_contract()
        self.commit("configure timed out check")
        self.baseline = engine.freeze(self.repo, self.contract_path)
        timed_out = self.evaluate(run_checks=True)
        self.assertEqual(timed_out["verdict"], "FAIL", timed_out)
        self.assertEqual(timed_out["checks"][0]["status"], "FAIL")

    def test_verification_mutation_is_detected_and_included(self):
        command = "from pathlib import Path; Path('owned.txt').write_text('mutated\\n')"
        self.contract["checks"] = [{
            "id": "mutate",
            "argv": [sys.executable, "-c", command],
            "timeout_seconds": 5,
        }]
        self.write_contract()
        self.commit("configure mutating check")
        self.baseline = engine.freeze(self.repo, self.contract_path)

        report = self.evaluate(run_checks=True)

        self.assertEqual(report["verdict"], "FAIL", report)
        self.assertTrue(report["state_changed_during_checks"])
        self.assertIn("owned.txt", report["changed_paths"])

    def test_paths_with_spaces_and_newlines_are_not_line_split(self):
        names = ["owned file.txt"]
        if os.name != "nt":
            names.append("owned\nfile.txt")
        for name in names:
            self.contract["allowed_paths"].append(name)
        self.write_contract()
        self.commit("allow unusual filenames")
        self.baseline = engine.freeze(self.repo, self.contract_path)
        for name in names:
            self.write(name, "content\n")
        report = self.evaluate()
        self.assertEqual(report["verdict"], "INCOMPLETE")
        self.assertIn("owned file.txt", report["changed_paths"])
        if os.name != "nt":
            self.assertIn("owned\nfile.txt", report["changed_paths"])

    def test_baseline_gitlink_is_rejected_before_any_check_can_pass(self):
        path = "modules/vendor"
        self.contract["allowed_paths"].append("modules/")
        marker = self.repo / "check-ran.txt"
        self.contract["checks"] = [{
            "id": "marker",
            "argv": [sys.executable, "-c", "from pathlib import Path; Path('check-ran.txt').write_text('yes')"],
            "timeout_seconds": 5,
        }]
        self.write_contract()
        self.commit("allow local module path and configure marker")
        self.install_gitlink(path, self.make_gitlink_target())
        self.git("commit", "-qm", "add baseline gitlink")

        try:
            baseline = engine.freeze(self.repo, self.contract_path)
        except ValueError:
            baseline = None
        if baseline is not None:
            self.assert_rejected_or_failed_without_checks(
                lambda: engine.evaluate(self.repo, self.contract_path, baseline, run_checks=True)
            )
        self.assertFalse(marker.exists(), "Gitlink baseline must not authorize checks")

    def test_candidate_gitlink_is_fail_closed_and_does_not_run_checks(self):
        self.contract["allowed_paths"].append("modules/")
        marker = self.repo / "check-ran.txt"
        self.contract["checks"] = [{
            "id": "marker",
            "argv": [sys.executable, "-c", "from pathlib import Path; Path('check-ran.txt').write_text('yes')"],
            "timeout_seconds": 5,
        }]
        self.write_contract()
        self.commit("allow module path and configure marker")
        self.baseline = engine.freeze(self.repo, self.contract_path)
        self.install_gitlink("modules/vendor", self.make_gitlink_target())

        self.assert_rejected_or_failed_without_checks(
            lambda: self.evaluate(run_checks=True)
        )
        self.assertFalse(marker.exists(), "Candidate gitlink must not authorize checks")

    def test_changed_embedded_git_directory_is_rejected_without_checks(self):
        self.contract["allowed_paths"].append("nested/")
        marker = self.repo / "check-ran.txt"
        self.contract["checks"] = [{
            "id": "marker",
            "argv": [sys.executable, "-c", "from pathlib import Path; Path('check-ran.txt').write_text('yes')"],
            "timeout_seconds": 5,
        }]
        self.write_contract()
        self.commit("allow nested path and configure marker")
        self.baseline = engine.freeze(self.repo, self.contract_path)

        embedded = self.repo / "nested"
        embedded.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=embedded, check=True)
        (embedded / "payload.txt").write_text("embedded repository\n", encoding="utf-8")

        self.assert_rejected_or_failed_without_checks(
            lambda: self.evaluate(run_checks=True)
        )
        self.assertFalse(marker.exists(), "Changed embedded Git directory must not run checks")

    def test_check_output_is_bounded_for_each_stream(self):
        self.contract["checks"] = [{
            "id": "large-output",
            "argv": [
                sys.executable,
                "-c",
                "import sys; sys.stdout.write('o' * 20000); sys.stderr.write('e' * 20000)",
            ],
            "timeout_seconds": 5,
        }]
        self.write_contract()
        self.commit("configure large output check")
        self.baseline = engine.freeze(self.repo, self.contract_path)

        report = self.evaluate(run_checks=True)

        self.assertEqual(report["verdict"], "PASS", report)
        check = report["checks"][0]
        self.assertGreater(len(check["stdout"]), 0)
        self.assertGreater(len(check["stderr"]), 0)
        self.assertLessEqual(len(check["stdout"]), 12000)
        self.assertLessEqual(len(check["stderr"]), 12000)

    def test_timeout_when_descendant_keeps_pipes_open(self):
        # Keep the short-lived child outside the fixture's Windows cleanup lock.
        command = (
            "import subprocess, sys, tempfile; "
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(2)'], "
            "cwd=tempfile.gettempdir())"
        )
        self.contract["checks"] = [{
            "id": "inherited-pipes",
            "argv": [sys.executable, "-c", command],
            "timeout_seconds": 1,
        }]
        self.write_contract()
        self.commit("configure inherited pipe timeout check")
        self.baseline = engine.freeze(self.repo, self.contract_path)

        report = self.evaluate(run_checks=True)

        self.assertEqual(report["verdict"], "FAIL", report)
        self.assertEqual(report["checks"][0]["status"], "FAIL")
        self.assertIn("timed out", report["checks"][0]["stderr"].lower())


if __name__ == "__main__":
    unittest.main()
