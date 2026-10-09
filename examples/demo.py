"""Compute four real ScopeGuard verdicts in disposable synthetic repositories."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scopeguard.engine import evaluate, freeze


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def fixture(repo: Path) -> Path:
    (repo / "app").mkdir()
    (repo / "tests").mkdir()
    (repo / "app" / "calculator.py").write_text("def add(a, b):\n    return 0\n", encoding="utf-8")
    (repo / "tests" / "check.py").write_text(
        "import runpy\nassert runpy.run_path('app/calculator.py')['add'](2, 3) == 5\n",
        encoding="utf-8",
    )
    (repo / ".gitignore").write_text(".scopeguard/\n__pycache__/\n", encoding="utf-8")
    contract = repo / "issue.json"
    contract.write_text(json.dumps({
        "schema_version": 1,
        "id": "calculator-fix",
        "title": "Fix addition without changing the verifier",
        "allowed_paths": ["app/"],
        "forbidden_paths": ["tests/", "issue.json"],
        "checks": [{"id": "addition", "argv": [sys.executable, "tests/check.py"], "timeout_seconds": 10}],
    }, indent=2) + "\n", encoding="utf-8")
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "ScopeGuard Demo")
    git(repo, "config", "user.email", "demo@example.invalid")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "Synthetic baseline")
    return contract


def main() -> int:
    scenarios = [
        ("owned-fix", "PASS", "def add(a, b):\n    return a + b\n", True, False),
        ("forbidden-verifier-edit", "FAIL", "def add(a, b):\n    return a + b\n", True, True),
        ("incorrect-fix", "FAIL", "def add(a, b):\n    return a - b\n", True, False),
        ("checks-not-executed", "INCOMPLETE", "def add(a, b):\n    return a + b\n", False, False),
    ]
    summaries = []
    with tempfile.TemporaryDirectory(prefix="scopeguard-demo-") as directory:
        for name, expected, source, run_checks, alter_verifier in scenarios:
            repo = Path(directory) / name
            repo.mkdir()
            contract = fixture(repo)
            baseline = freeze(repo, contract)
            (repo / "app" / "calculator.py").write_text(source, encoding="utf-8")
            if alter_verifier:
                (repo / "tests" / "check.py").write_text("print('pretend success')\n", encoding="utf-8")
            report = evaluate(repo, contract, baseline, run_checks=run_checks)
            verdict = report["verdict"]
            summaries.append({"scenario": name, "verdict": verdict, "expected": expected,
                              "changed_paths": report["changed_paths"], "violations": report["violations"]})
            if verdict != expected:
                print(json.dumps({"error": "Unexpected verdict", "report": report}, indent=2))
                return 1
    print(json.dumps({"kind": "synthetic-demo", "scenarios": summaries}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

