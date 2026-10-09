"""Frozen issue contracts and fail-closed Git scope evaluation."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path, PurePosixPath
from typing import Any


_SHA = re.compile(r"^[0-9a-f]{40,64}$")
_WILDCARDS = set("*?[]{}")


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        raise RuntimeError(f"Unable to execute Git: {exc}") from exc
    if check and result.returncode:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(f"Git {' '.join(args)} failed: {detail or result.returncode}")
    return result


def _root(repo: Path) -> Path:
    result = _git(repo, "rev-parse", "--show-toplevel")
    return Path(os.fsdecode(result.stdout.rstrip(b"\r\n"))).resolve()


def _repo_path(repo: Path, path: Path) -> str:
    root = _root(repo)
    resolved = path.resolve() if path.is_absolute() else (root / path).resolve()
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("Contract path must be inside the repository") from exc
    if not relative.parts:
        raise ValueError("Contract path must name a file")
    return PurePosixPath(*relative.parts).as_posix()


def _safe_scope_path(value: Any) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("Scope paths must be nonempty repository-relative POSIX paths")
    if value.startswith("/") or re.match(r"^[A-Za-z]:", value):
        raise ValueError(f"Unsafe absolute scope path: {value!r}")
    trimmed = value[:-1] if value.endswith("/") else value
    parts = trimmed.split("/")
    if not trimmed or any(part in ("", ".", "..") for part in parts) or any(c in _WILDCARDS for c in value):
        raise ValueError(f"Unsafe scope path: {value!r}")
    return value


def _load_contract(repo: Path, contract_path: Path) -> tuple[dict[str, Any], bytes, str]:
    relative = _repo_path(repo, contract_path)
    full_path = _root(repo).joinpath(*relative.split("/"))
    try:
        raw = full_path.read_bytes()
        contract = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read valid JSON contract at {relative}: {exc}") from exc
    if not isinstance(contract, dict) or set(contract) != {
        "schema_version", "id", "title", "allowed_paths", "forbidden_paths", "checks"
    }:
        raise ValueError("Contract must contain exactly the required keys")
    if contract["schema_version"] != 1 or isinstance(contract["schema_version"], bool):
        raise ValueError("schema_version must be 1")
    for key in ("id", "title"):
        if not isinstance(contract[key], str) or not contract[key].strip():
            raise ValueError(f"{key} must be a nonempty string")
    for key, nonempty in (("allowed_paths", True), ("forbidden_paths", False)):
        values = contract[key]
        if not isinstance(values, list) or (nonempty and not values):
            raise ValueError(f"{key} must be {'a nonempty' if nonempty else 'an'} array")
        for item in values:
            _safe_scope_path(item)
    checks = contract["checks"]
    if not isinstance(checks, list) or not checks:
        raise ValueError("checks must be a nonempty array")
    seen: set[str] = set()
    for check in checks:
        if not isinstance(check, dict) or set(check) != {"id", "argv", "timeout_seconds"}:
            raise ValueError("Each check must contain exactly id, argv, and timeout_seconds")
        ident, argv, timeout = check["id"], check["argv"], check["timeout_seconds"]
        if not isinstance(ident, str) or not ident.strip() or ident in seen:
            raise ValueError("Check ids must be unique nonempty strings")
        seen.add(ident)
        if not isinstance(argv, list) or not argv or any(not isinstance(arg, str) or not arg for arg in argv):
            raise ValueError(f"Check {ident} argv must be a nonempty string array")
        if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 300:
            raise ValueError(f"Check {ident} timeout_seconds must be an integer from 1 to 300")
    return contract, raw, relative


def _head(repo: Path) -> str:
    sha = _git(repo, "rev-parse", "--verify", "HEAD^{commit}").stdout.decode("ascii").strip()
    if not _SHA.fullmatch(sha):
        raise RuntimeError("Git returned an invalid HEAD commit id")
    return sha


def freeze(repo: Path, contract_path: Path) -> dict[str, Any]:
    root = _root(repo)
    contract, raw, relative = _load_contract(root, contract_path)
    del contract
    status = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all").stdout
    if status:
        raise ValueError("Cannot freeze a dirty Git tree; commit or remove tracked and untracked changes first")
    return {
        "schema_version": 1,
        "contract_path": relative,
        "contract_sha256": hashlib.sha256(raw).hexdigest(),
        "base_sha": _head(root),
    }


def _validate_baseline(baseline: Any) -> dict[str, Any]:
    if not isinstance(baseline, dict) or set(baseline) != {
        "schema_version", "contract_path", "contract_sha256", "base_sha"
    }:
        raise ValueError("Invalid baseline: expected exactly schema_version, contract_path, contract_sha256, base_sha")
    if baseline["schema_version"] != 1 or isinstance(baseline["schema_version"], bool):
        raise ValueError("Invalid baseline schema_version")
    _safe_scope_path(baseline["contract_path"])
    if not isinstance(baseline["contract_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", baseline["contract_sha256"]):
        raise ValueError("Invalid baseline contract_sha256")
    if not isinstance(baseline["base_sha"], str) or not _SHA.fullmatch(baseline["base_sha"]):
        raise ValueError("Invalid baseline base_sha")
    return baseline


def _changed_paths(repo: Path, base: str) -> list[str]:
    # Verify the base is an actual commit before using it in diff expressions.
    _git(repo, "rev-parse", "--verify", f"{base}^{{commit}}")
    diff = _git(repo, "diff", "--name-status", "-z", "--find-renames", base, "--").stdout
    fields = diff.split(b"\0")
    paths: set[str] = set()
    i = 0
    while i < len(fields) and fields[i]:
        status = fields[i].decode("ascii", "strict")
        i += 1
        count = 2 if status.startswith("R") or status.startswith("C") else 1
        if i + count > len(fields):
            raise RuntimeError("Malformed NUL-delimited Git diff output")
        for field in fields[i:i + count]:
            if not field:
                raise RuntimeError("Malformed empty path in Git diff output")
            paths.add(field.decode("utf-8", "surrogateescape"))
        i += count
    untracked = _git(repo, "ls-files", "--others", "--exclude-standard", "-z").stdout
    for field in untracked.split(b"\0"):
        if field:
            paths.add(field.decode("utf-8", "surrogateescape"))
    return sorted(path for path in paths if path != ".scopeguard" and not path.startswith(".scopeguard/"))


def _matches(path: str, scope: str) -> bool:
    if scope.endswith("/"):
        prefix = scope.rstrip("/")
        return path == prefix or path.startswith(prefix + "/")
    return path == scope


def _fingerprint(repo: Path, base: str) -> tuple[list[str], str]:
    paths = _changed_paths(repo, base)
    digest = hashlib.sha256()

    def record(value: bytes) -> None:
        digest.update(len(value).to_bytes(8, "big"))
        digest.update(value)

    record(_head(repo).encode("ascii"))
    record(_git(
        repo, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--",
        ".", ":(exclude).scopeguard", ":(exclude).scopeguard/**",
    ).stdout)
    record(_git(
        repo, "diff", "--cached", "--raw", "--no-abbrev", "-z", "--no-renames",
        base, "--", ".", ":(exclude).scopeguard", ":(exclude).scopeguard/**",
    ).stdout)
    for name in paths:
        encoded = os.fsencode(name)
        record(encoded)
        target = _root(repo).joinpath(*name.split("/"))
        try:
            if target.is_symlink():
                record(target.lstat().st_mode.to_bytes(4, "big"))
                record(b"L" + os.fsencode(os.readlink(target)))
            elif target.is_file():
                record(target.lstat().st_mode.to_bytes(4, "big"))
                record(b"F" + target.read_bytes())
            elif target.is_dir():
                raise RuntimeError(f"Cannot fingerprint changed directory path: {name}")
            else:
                try:
                    target.lstat()
                except FileNotFoundError:
                    record(b"M")
                else:
                    raise RuntimeError(f"Unsupported special filesystem node: {name}")
        except OSError as exc:
            raise RuntimeError(f"Cannot fingerprint changed source path {name}: {exc}") from exc
    return paths, digest.hexdigest()


def _gitlink_paths(repo: Path, base: str) -> set[str]:
    paths: set[str] = set()
    tree = _git(repo, "ls-tree", "-r", "-z", base).stdout
    for entry in tree.split(b"\0"):
        if not entry:
            continue
        metadata, path = entry.split(b"\t", 1)
        if metadata.startswith(b"160000 "):
            paths.add(path.decode("utf-8", "surrogateescape"))
    index = _git(repo, "ls-files", "--stage", "-z").stdout
    for entry in index.split(b"\0"):
        if not entry:
            continue
        metadata, path = entry.split(b"\t", 1)
        if metadata.startswith(b"160000 "):
            paths.add(path.decode("utf-8", "surrogateescape"))
    return paths


def _unsupported_paths(repo: Path, base: str, changed_paths: list[str]) -> list[str]:
    unsupported = [
        f"Unsupported Git submodule/gitlink: {path}"
        for path in sorted(_gitlink_paths(repo, base))
    ]
    root = _root(repo)
    for name in changed_paths:
        target = root.joinpath(*name.split("/"))
        if not target.is_symlink() and target.is_dir():
            unsupported.append(f"Unsupported changed directory/embedded repository path: {name}")
    return unsupported


def _kill_and_reap(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        process.kill()
    try:
        process.wait(timeout=0.2)
    except subprocess.TimeoutExpired:
        pass


def _run_bounded(argv: list[str], cwd: Path, timeout: int) -> tuple[int, bytes, bytes]:
    deadline = time.monotonic() + timeout
    process = subprocess.Popen(
        argv, cwd=cwd, shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    captured = {"stdout": bytearray(), "stderr": bytearray()}
    if time.monotonic() >= deadline:
        _kill_and_reap(process)
        process.stdout.close()
        process.stderr.close()
        raise subprocess.TimeoutExpired(argv, timeout)

    def drain(name: str, stream: Any) -> None:
        try:
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    break
                remaining = 12000 - len(captured[name])
                if remaining > 0:
                    captured[name].extend(chunk[:remaining])
        finally:
            stream.close()

    readers = [
        threading.Thread(target=drain, args=("stdout", process.stdout), daemon=True),
        threading.Thread(target=drain, args=("stderr", process.stderr), daemon=True),
    ]
    for reader in readers:
        reader.start()
    try:
        exit_code = process.wait(timeout=max(0, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        _kill_and_reap(process)
        for reader in readers:
            reader.join(timeout=max(0, deadline - time.monotonic()))
        raise subprocess.TimeoutExpired(
            argv, timeout, output=bytes(captured["stdout"]), stderr=bytes(captured["stderr"])
        )
    for reader in readers:
        reader.join(timeout=max(0, deadline - time.monotonic()))
    if time.monotonic() > deadline or any(reader.is_alive() for reader in readers):
        _kill_and_reap(process)
        raise subprocess.TimeoutExpired(
            argv, timeout, output=bytes(captured["stdout"]), stderr=bytes(captured["stderr"])
        )
    return exit_code, bytes(captured["stdout"]), bytes(captured["stderr"])


def _scope_violations(paths: list[str], contract: dict[str, Any]) -> list[str]:
    violations = []
    for path in paths:
        if any(_matches(path, scope) for scope in contract["forbidden_paths"]):
            violations.append(f"Forbidden path changed: {path}")
        elif not any(_matches(path, scope) for scope in contract["allowed_paths"]):
            violations.append(f"Path is outside allowed scope: {path}")
    return violations


def evaluate(repo: Path, contract_path: Path, baseline: dict, run_checks: bool = False) -> dict[str, Any]:
    root = _root(repo)
    baseline = _validate_baseline(baseline)
    if not isinstance(run_checks, bool):
        raise ValueError("run_checks must be a boolean")
    contract, raw, relative = _load_contract(root, contract_path)
    contract_hash = hashlib.sha256(raw).hexdigest()
    if relative != baseline["contract_path"]:
        raise ValueError("Contract path does not match the frozen baseline")
    if contract_hash != baseline["contract_sha256"]:
        raise ValueError("Contract content does not match the frozen baseline")
    _git(root, "rev-parse", "--verify", f"{baseline['base_sha']}^{{commit}}")
    head_sha = _head(root)
    paths = _changed_paths(root, baseline["base_sha"])
    violations = _scope_violations(paths, contract)
    unsupported = _unsupported_paths(root, baseline["base_sha"], paths)
    if unsupported:
        violations.extend(unsupported)
        return {
            "schema_version": 1, "verdict": "FAIL", "base_sha": baseline["base_sha"],
            "head_sha": head_sha, "contract_sha256": contract_hash,
            "changed_paths": paths, "violations": violations, "checks": [],
            "state_changed_during_checks": False,
        }
    paths, before = _fingerprint(root, baseline["base_sha"])
    results: list[dict[str, Any]] = []
    if run_checks and not violations:
        for check in contract["checks"]:
            record: dict[str, Any] = {
                "id": check["id"], "status": "PASS", "argv": check["argv"],
                "exit_code": None, "stdout": "", "stderr": "",
            }
            try:
                exit_code, stdout, stderr = _run_bounded(
                    check["argv"], root, check["timeout_seconds"]
                )
                record["exit_code"] = exit_code
                record["stdout"] = stdout.decode("utf-8", "replace")
                record["stderr"] = stderr.decode("utf-8", "replace")
                if exit_code:
                    record["status"] = "FAIL"
            except subprocess.TimeoutExpired as exc:
                record["status"] = "FAIL"
                record["stdout"] = _bounded_output(exc.stdout)
                timeout_note = "\nCheck timed out"
                captured_stderr = _bounded_output(exc.stderr)
                record["stderr"] = captured_stderr[:12000 - len(timeout_note)] + timeout_note
            except OSError as exc:
                record["status"] = "FAIL"
                record["stderr"] = str(exc)[:12000]
            results.append(record)
    after_paths = _changed_paths(root, baseline["base_sha"])
    unsupported_after = _unsupported_paths(root, baseline["base_sha"], after_paths)
    if unsupported_after:
        state_changed = True
    else:
        after_paths, after = _fingerprint(root, baseline["base_sha"])
        state_changed = before != after or paths != after_paths
    if state_changed:
        violations.append("Candidate source state changed during check execution")
        violations.extend(_scope_violations(after_paths, contract))
        violations.extend(unsupported_after)
    if violations:
        verdict = "FAIL"
    elif not run_checks or len(results) != len(contract["checks"]) or any(r["status"] != "PASS" for r in results):
        verdict = "INCOMPLETE" if not any(r["status"] == "FAIL" for r in results) else "FAIL"
    else:
        verdict = "PASS"
    return {
        "schema_version": 1, "verdict": verdict, "base_sha": baseline["base_sha"],
        "head_sha": head_sha, "contract_sha256": contract_hash,
        "changed_paths": after_paths, "violations": violations, "checks": results,
        "state_changed_during_checks": state_changed,
    }


def _bounded_output(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value[:12000].decode("utf-8", "replace")
    return value[:12000]
