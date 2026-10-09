# ScopeGuard

Freeze an issue's file scope. Check the actual Git changes. Run the approved verification commands and get a fresh PASS, FAIL or INCOMPLETE receipt.

ScopeGuard is a small Python CLI for teams delegating coding tasks to agents. The contract combines file ownership with required command results.

## Reproduce the demo

Python 3.11+ and Git are required. No model API key is used.

```text
git clone https://github.com/Hardik-S/scopeguard.git
cd scopeguard
python -m pip install .
python examples/demo.py
```

The demo creates disposable Git repositories and computes every verdict:

| Scenario | Expected result | Reason |
| --- | --- | --- |
| Correct change to owned implementation | PASS | Scope is valid and the fixture check succeeds |
| Agent edits the forbidden verifier | FAIL | The actual diff violates the frozen contract |
| Implementation returns a wrong answer | FAIL | The required command fails |
| Agent skips verification | INCOMPLETE | Fresh check evidence is missing |

These are synthetic software fixtures, not a model leaderboard.

## Use in a repository

Commit your issue contract and add `.scopeguard/` to Git ignore rules before freezing. The starting tree must be clean. Copy and adapt `examples/issue.json`; paths ending in `/` cover that directory, and other paths match exactly. Forbidden paths win.

```text
scopeguard freeze --repo . --contract issue.json
# Delegate the bounded change to a worker, then inspect it.
scopeguard check --repo . --run-checks --format json
```

The explicit `--run-checks` flag executes the contract's trusted argument arrays from the Git root. Review them first. Without the flag, missing check evidence produces INCOMPLETE. Scope violations block command execution. Fresh reports are written to `.scopeguard/report.json`; the baseline records the exact starting commit and contract hash.

Exit codes: `0` for PASS or a successful freeze, `1` for FAIL, and `2` for invalid input or INCOMPLETE.

## Coverage And Limits

Checks inspect committed, staged, unstaged, untracked, deleted and renamed paths, contract changes, failed or timed-out commands, and source mutations during verification.

This is not a filesystem sandbox or a security boundary. It cannot attest worker identity, verify remote provider outcomes, protect itself from a malicious verifier environment, or prove complete semantic issue fulfillment. Submodules and changed embedded Git repositories fail closed in v0.1. Command timeouts allow a short process-reaping grace and do not isolate descendants. Contracts and commands require trusted review. Reports are local evidence, not signed attestations.

See [SPEC.md](SPEC.md) for the v0.1 interface and [the launch note](docs/LAUNCH.md) for the reproducible claim.

## Development

```text
python -m unittest discover -s tests -v
python examples/demo.py
```

## Related Work

[proof-of-done](https://github.com/lykAntonio/proof-of-done) supplies acceptance contracts and independent audits. [BoardFlowBench](https://github.com/ChenNeuro/BoardFlowBench) evaluates sequential agent handoffs. ScopeGuard focuses on deterministic Git path ownership and newly executed command evidence. It does not claim a new acceptance protocol or a broad benchmark advantage.

MIT licensed. Built by Hardik Shrestha with an issue-scoped GPT-6 Luna pod.
