"""Command-line interface for ScopeGuard."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

class CLIError(ValueError):
    """An actionable command-line or repository error."""


def _git_root(path: Path) -> Path:
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
            shell=False,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", None)
        raise CLIError(f"Cannot resolve Git root for {path}: {(detail or str(exc)).strip()}") from exc
    return Path(result.stdout.strip()).resolve(strict=True)


def _contract_path(value: str, root: Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    try:
        return path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise CLIError(f"Contract does not exist or cannot be resolved: {path}") from exc


def _evidence_dir(root: Path) -> Path:
    evidence = root / ".scopeguard"
    if evidence.is_symlink():
        raise CLIError(f"Refusing symlink evidence directory: {evidence}")
    try:
        evidence.mkdir(exist_ok=True)
        resolved = evidence.resolve(strict=True)
    except OSError as exc:
        raise CLIError(f"Cannot create evidence directory {evidence}: {exc}") from exc
    if resolved != root and root not in resolved.parents:
        raise CLIError(f"Evidence directory escapes Git root: {evidence}")
    return resolved


def _write_json(root: Path, name: str, value: dict) -> Path:
    directory = _evidence_dir(root)
    destination = directory / name
    if destination.is_symlink():
        raise CLIError(f"Refusing symlink evidence file: {destination}")
    if destination.exists() and not destination.is_file():
        raise CLIError(f"Evidence destination is not a regular file: {destination}")
    temp = directory / f".{name}.{uuid.uuid4().hex}.tmp"
    try:
        with temp.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(temp, destination)
    except OSError as exc:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        raise CLIError(f"Cannot write evidence file {destination}: {exc}") from exc
    return destination


def _load_json(path: Path, label: str) -> dict:
    try:
        with path.open("r", encoding="utf-8") as stream:
            value = json.load(stream)
    except (OSError, json.JSONDecodeError) as exc:
        raise CLIError(f"Cannot read {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise CLIError(f"{label.capitalize()} must contain a JSON object: {path}")
    return value


def _emit(value: dict, output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(value, indent=2, sort_keys=True))
        return
    if "verdict" not in value:
        print("Freeze succeeded")
        if "base_sha" in value:
            print(f"Base: {value['base_sha']}")
        return
    print(f"Verdict: {value.get('verdict', 'INCOMPLETE')}")
    print(f"Changed paths: {', '.join(value.get('changed_paths', [])) or '(none)'}")
    for violation in value.get("violations", []):
        print(f"Violation: {violation}")
    for check in value.get("checks", []):
        print(f"Check {check.get('id', '(unknown)')}: {check.get('status', 'unknown')}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scopeguard")
    commands = parser.add_subparsers(dest="command", required=True)
    freeze = commands.add_parser("freeze", help="freeze a clean repository baseline")
    freeze.add_argument("--repo", required=True, type=Path)
    freeze.add_argument("--contract", required=True)
    freeze.add_argument("--format", choices=("text", "json"), default="text")
    check = commands.add_parser("check", help="evaluate repository changes against a baseline")
    check.add_argument("--repo", required=True, type=Path)
    check.add_argument("--contract")
    check.add_argument("--run-checks", action="store_true", help="authorize trusted contract commands")
    check.add_argument("--format", choices=("text", "json"), default="text")
    return parser


def _run(args: argparse.Namespace) -> tuple[dict, int]:
    try:
        from . import engine
    except ImportError as exc:
        raise CLIError("ScopeGuard engine is not available yet; retry after scopeguard.engine is integrated") from exc
    repo = args.repo.resolve(strict=True)
    root = _git_root(repo)
    if args.command == "freeze":
        contract = _contract_path(args.contract, root)
        baseline = engine.freeze(root, contract)
        _write_json(root, "baseline.json", baseline)
        return baseline, 0

    baseline_path = root / ".scopeguard" / "baseline.json"
    baseline = _load_json(baseline_path, "baseline")
    supplied_contract = args.contract
    contract_value = supplied_contract or baseline.get("contract_path")
    if not isinstance(contract_value, str) or not contract_value:
        raise CLIError("No contract path supplied and baseline has no valid contract_path")
    contract = _contract_path(contract_value, root)

    report = engine.evaluate(root, contract, baseline, run_checks=False)
    if args.run_checks and report.get("verdict") != "FAIL":
        report = engine.evaluate(root, contract, baseline, run_checks=True)
    _write_json(root, "report.json", report)
    verdict = report.get("verdict")
    return report, 0 if verdict == "PASS" else 1 if verdict == "FAIL" else 2


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        value, status = _run(args)
        _emit(value, args.format)
        return status
    except (CLIError, ValueError, RuntimeError, OSError) as exc:
        if args.format == "json":
            print(json.dumps({"error": str(exc)}, sort_keys=True))
        else:
            print(f"scopeguard: {exc}", file=sys.stderr)
        return 2
