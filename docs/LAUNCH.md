# Launch Draft

ScopeGuard checks whether a coding-agent patch stayed inside its assigned files and whether its required checks actually ran on an unchanged candidate state.

The demo shows four computed verdicts: an owned fix passes; changing the verifier fails; an incorrect fix fails; skipping checks remains incomplete. Run `python examples/demo.py` to reproduce them locally.

This is a small deterministic developer tool. It does not judge semantic issue completion or isolate a malicious agent. Trust and review the contract and commands before execution.

Repository: https://github.com/Hardik-S/scopeguard

Release target: https://github.com/Hardik-S/scopeguard/releases/tag/v0.1.0

The release gate combines real-Git regression tests, a fresh-wheel quickstart, a four-scenario computed demo, Windows/Linux CI on Python 3.11 and 3.13, and an independent Luna review. Review found and repaired submodule, fingerprint-boundary and executable-mode gaps before release. The exact CI receipts are linked from the release.

This note is ready for human publishing. No social or email action is implied.
