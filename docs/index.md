# Landing

Landing carries out development actions for people using the Bub 0.5.0 SDK.
Its four modes are `issuer`, `fixer`, `gatekeeper`, and `explainer`.
Use the same action contract from a CLI, a CI shell step, an embedding application, or HTTP webhook admission.

```bash
uv run landing gatekeeper "Review the candidate against the acceptance criteria." \
  --check "uv run pytest" --json --output review.json
```

The command returns zero only when execution completes and the decision is `allow`.
Required checks provide actual validation evidence; a failed check forces `block`.
All task records and Bub model history are stored in SQLite.

See the [repository README](https://github.com/PsiACE/landing#readme) for installation,
CLI examples, the HTTP contract, the SDK entry point, the ONCE-compatible container,
Litestream replication and recovery, and the dogfood workflow.
See [Modules](modules.md) for API reference.
