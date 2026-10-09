# ScopeGuard

An executable issue contract for coding-agent changes. Freeze allowed paths and required verification, then inspect the actual Git diff and run the approved checks.

Status: implementation in progress. Read SPEC.md for the exact v0.1 interface and limits. Public release requires the independent tests and synthetic demo to pass.

## Related work

[proof-of-done](https://github.com/lykAntonio/proof-of-done) offers evidence-backed acceptance contracts and independent agent audits. [BoardFlowBench](https://github.com/ChenNeuro/BoardFlowBench) evaluates sequential agent handoffs. ScopeGuard focuses on a small deterministic CLI for Git path ownership and freshly executed command evidence. This is a bounded engineering tool, not a claim of research novelty.

