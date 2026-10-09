# Launch Draft

ScopeGuard checks whether a coding-agent patch stayed inside its assigned files and whether its required checks actually ran on an unchanged candidate state.

The demo shows four computed verdicts: an owned fix passes; changing the verifier fails; an incorrect fix fails; skipping checks remains incomplete. Run `python examples/demo.py` to reproduce them locally.

This is a small deterministic developer tool. It does not judge semantic issue completion or isolate a malicious agent. Trust and review the contract and commands before execution.

Release URL and verified test counts will be added after the acceptance gate passes. This is a draft for human publishing; no social or email send is authorized by this file.

