# ScopeGuard v0.1 Contract

ScopeGuard checks whether an agent's actual Git changes obey a frozen issue contract and whether required commands ran successfully on the candidate state. It is a local development tool, not a security sandbox or an LLM judge.

## Contract JSON

Required keys: schema_version (1), id (nonempty string), title (nonempty string), allowed_paths (nonempty array), forbidden_paths (array), checks (nonempty array). Unknown fields are rejected. Each check has only id (unique, nonempty), argv (nonempty string array), timeout_seconds (integer 1-300). Paths are repository-relative POSIX paths. A trailing slash grants a directory prefix; otherwise the path is exact. Reject absolute paths, backslashes, dot or dot-dot segments, empty strings, and wildcard syntax. Forbidden paths always win.

## Core API

scopeguard.engine.freeze(repo: pathlib.Path, contract_path: pathlib.Path) -> dict
- Validate contract. Resolve Git root and exact HEAD SHA. Require a clean tracked and untracked starting tree, respecting Git ignores. Return a baseline with schema_version, contract_path (relative), contract_sha256, base_sha. Never change source files. CLI writes this baseline under .scopeguard/.

scopeguard.engine.evaluate(repo: pathlib.Path, contract_path: pathlib.Path, baseline: dict, run_checks: bool = False) -> dict
- Reject invalid baseline and contract. Verify the contract hash matches the baseline.
- Inspect all candidate changes against base_sha, including staged, unstaged, committed, deleted, renamed (treat as deleted+added), and untracked nonignored paths. Use Git NUL-delimited output, never line parsing.
- v0.1 rejects repositories containing submodules/gitlinks and changed embedded Git directories before command execution. It must never convert uninspectable directory state into PASS.
- Special filesystem nodes exposed by Git change discovery are unsupported and rejected; only regular files, symlinks and missing/deleted paths can be fingerprinted.
- Compare every path with allowed_paths and forbidden_paths. Ignore only the tool's .scopeguard/ output directory. Resolve base_sha as a commit before diffing.
- Run checks only with run_checks=True. Execute exact argv with shell=False from the repo root. Each check has a timeout; capture at most 12000 characters each of stdout/stderr; mark nonzero exits, missing executables and timeouts failed.
- Compare candidate content fingerprints before and after check execution. If tracked or untracked source state changes during verification, verdict cannot pass. Include changes caused by checks in the final changed_paths and scope violations.
- Fingerprints include length-delimited content records, observed file modes, and full staged blob identities; identical Git status labels alone are insufficient.
- Return keys schema_version, verdict (PASS|FAIL|INCOMPLETE), base_sha, head_sha, contract_sha256, changed_paths, violations (string array), checks (records with id, status, argv, exit_code, stdout, stderr), state_changed_during_checks (bool). Extra evidence keys are allowed. PASS requires no violation, every check executed and passing, and unchanged candidate state during checks. Without --run-checks use INCOMPLETE unless there is a definite FAIL.
- engine exceptions for invalid input should be ValueError. Operational errors must be actionable and fail closed.

## CLI

python -m scopeguard freeze --repo PATH --contract PATH
python -m scopeguard check --repo PATH [--contract PATH] [--run-checks] [--format text|json]

freeze writes .scopeguard/baseline.json; check always writes a fresh .scopeguard/report.json. Both find the Git root. Contract paths may be relative to repo; baseline contract_path supplies the check default. JSON mode emits valid JSON only. Text mode summarizes verdict, paths, violations and check results. Exit codes: 0 PASS/freeze-success, 1 FAIL, 2 invalid input or INCOMPLETE. Scope violations must not run arbitrary checks. The explicit --run-checks flag authorizes execution of trusted contract commands.

## Acceptance

Real temporary Git repositories must cover: valid owned changes; staged and unstaged violations; committed violations; untracked forbidden files; deletion and rename; changed contract; absent checks; failing checks; timeout; check-induced source mutation; paths with spaces; malformed contracts and baselines. The synthetic demo must show PASS, forbidden-path FAIL, check FAIL and missing-evidence INCOMPLETE. Every demo verdict is computed by the tool, never hardcoded.

## Limits

No filesystem sandbox, credential isolation, worker identity attestation, remote receipts, signed evidence, or semantic proof of issue fulfillment. Submodules and embedded Git directories are unsupported and fail closed. Command deadlines include a short bounded cleanup grace for reaping a killed process; descendant processes are not isolated. Contracts and check commands require trusted Director review. The tool cannot reliably protect itself from a malicious actor controlling its executable or the verifier environment.
