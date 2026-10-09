import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="scopeguard-cli-")
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.git("init", "-q")
        self.git("config", "user.name", "ScopeGuard CLI Test")
        self.git("config", "user.email", "scopeguard-cli@example.invalid")
        (self.repo / "contracts").mkdir()
        self.contract = self.repo / "contracts" / "contract.json"
        self.contract.write_text(json.dumps({
            "schema_version": 1,
            "id": "cli-test",
            "title": "CLI test contract",
            "allowed_paths": ["owned.txt", "contracts/"],
            "forbidden_paths": ["secret.txt"],
            "checks": [{
                "id": "ok",
                "argv": [sys.executable, "-c", "print('check passed')"],
                "timeout_seconds": 5,
            }],
        }), encoding="utf-8")
        (self.repo / "owned.txt").write_text("base\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-qm", "initial")

    def git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.repo, check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        ).stdout

    def cli(self, *args):
        env = os.environ.copy()
        env["PYTHONPATH"] = str(PACKAGE_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
        return subprocess.run(
            [sys.executable, "-m", "scopeguard", *args],
            cwd=PACKAGE_ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )

    def freeze(self):
        return self.cli("freeze", "--repo", str(self.repo), "--contract", "contracts/contract.json")

    def test_freeze_and_json_check_write_and_emit_fresh_report(self):
        frozen = self.freeze()
        self.assertEqual(frozen.returncode, 0, frozen.stderr)
        baseline_path = self.repo / ".scopeguard" / "baseline.json"
        self.assertTrue(baseline_path.is_file())

        (self.repo / "owned.txt").write_text("changed\n", encoding="utf-8")
        checked = self.cli("check", "--repo", str(self.repo), "--run-checks", "--format", "json")
        self.assertEqual(checked.returncode, 0, checked.stderr)
        payload = json.loads(checked.stdout)
        self.assertEqual(payload["verdict"], "PASS")
        self.assertEqual(payload["checks"][0]["status"], "PASS")
        report_path = self.repo / ".scopeguard" / "report.json"
        saved = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(saved, payload)

        report_path.write_text('{"stale_marker": true}', encoding="utf-8")
        again = self.cli("check", "--repo", str(self.repo), "--run-checks", "--format", "json")
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertNotIn("stale_marker", report_path.read_text(encoding="utf-8"))
        self.assertEqual(json.loads(again.stdout), json.loads(report_path.read_text(encoding="utf-8")))

    def test_json_failure_exit_code_and_scope_violation_does_not_run_check(self):
        frozen = self.freeze()
        self.assertEqual(frozen.returncode, 0, frozen.stderr)
        (self.repo / "secret.txt").write_text("forbidden\n", encoding="utf-8")
        checked = self.cli("check", "--repo", str(self.repo), "--run-checks", "--format", "json")
        self.assertEqual(checked.returncode, 1, checked.stderr)
        payload = json.loads(checked.stdout)
        self.assertEqual(payload["verdict"], "FAIL")
        self.assertEqual(payload["checks"], [])

    def test_missing_baseline_returns_incomplete_exit_code_two(self):
        checked = self.cli("check", "--repo", str(self.repo), "--format", "json")
        self.assertEqual(checked.returncode, 2)


if __name__ == "__main__":
    unittest.main()
