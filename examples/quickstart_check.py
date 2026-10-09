"""Validate the installed CLI against a disposable repository with spaces."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def run(argv: list[str], cwd: Path, expected: int = 0) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    result = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=30)
    if result.returncode != expected:
        raise RuntimeError(f"Unexpected exit {result.returncode}: {result.stdout} {result.stderr}")
    return result


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="scopeguard-install-") as directory:
        repo = Path(directory) / "repository with spaces"
        repo.mkdir()
        (repo / "src").mkdir()
        (repo / "tests").mkdir()
        (repo / "src" / "answer.py").write_text("answer = 0\n", encoding="utf-8")
        (repo / "tests" / "oracle.py").write_text("import runpy\nassert runpy.run_path('src/answer.py')['answer'] == 42\n", encoding="utf-8")
        (repo / ".gitignore").write_text(".scopeguard/\n__pycache__/\n", encoding="utf-8")
        (repo / "issue.json").write_text(json.dumps({
            "schema_version": 1, "id": "installed-quickstart", "title": "Set the answer",
            "allowed_paths": ["src/"], "forbidden_paths": ["tests/", "issue.json"],
            "checks": [{"id": "oracle", "argv": [sys.executable, "tests/oracle.py"], "timeout_seconds": 10}],
        }), encoding="utf-8")
        for args in (("init", "-q"), ("config", "user.name", "ScopeGuard Fixture"),
                     ("config", "user.email", "fixture@example.invalid"), ("add", "."),
                     ("commit", "-qm", "fixture")):
            run(["git", *args], repo)
        command = [sys.executable, "-m", "scopeguard"]
        run(command + ["freeze", "--repo", str(repo), "--contract", "issue.json", "--format", "json"], repo)
        (repo / "src" / "answer.py").write_text("answer = 42\n", encoding="utf-8")
        incomplete = json.loads(run(command + ["check", "--repo", str(repo), "--format", "json"], repo, 2).stdout)
        assert incomplete["verdict"] == "INCOMPLETE"
        passed = json.loads(run(command + ["check", "--repo", str(repo), "--run-checks", "--format", "json"], repo).stdout)
        assert passed["verdict"] == "PASS"
        assert json.loads((repo / ".scopeguard" / "report.json").read_text(encoding="utf-8")) == passed
        (repo / "tests" / "oracle.py").write_text("print('fake success')\n", encoding="utf-8")
        failed = json.loads(run(command + ["check", "--repo", str(repo), "--run-checks", "--format", "json"], repo, 1).stdout)
        assert failed["verdict"] == "FAIL" and failed["checks"] == []
    print(json.dumps({"installed_quickstart": "PASS", "computed_verdicts": ["INCOMPLETE", "PASS", "FAIL"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
