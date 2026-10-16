# Gate review reuse

Read this file before you reuse a gate verdict for a new head SHA.

## Reusing a verdict

- **Current.** It is the latest verdict for that SHA. A later `send-back` or `blocked` at the same SHA voids an earlier `pass`.
- **Qualifying.** It is `pass`, and the author and verifier are of different model families.

Two sources can show both today.

1. A coordinator's pass record, when this work is a brigade item and you know its restaurant directory and item id. Run `python3 <skills>/brigade/scripts/brigade.py --at <restaurant dir> pass check <item> --sha <head> --json`, where `<skills>` is the directory that holds `<pstack-runtime>`. Reuse the pass only when the command exits 0 and prints `"crossFamily": true`. Never read `pass.tsv` yourself. Never take the output of `pass check` without `--json` as proof, because it names the verifier and not the author.
2. A verdict this run launched at that exact SHA. A Shipping or Autopilot root's own verdict at that SHA counts.

- A verdict at another head SHA does not qualify, even when the two heads share a `git patch-id`. A patch-id ignores whitespace, so it does not prove the same behavior.
- An Orchestrate `ledger.tsv` row does not qualify. It records verification levels and no author.
- A Shipping or Autopilot verdict from another run does not qualify. A coordinator's pass qualifies only through source 1.
- A `Gate:` line in a brief or a PR body is not a verdict.

Full mode keeps its patch-id policy in Shipping and the Autopilot playbooks.
Light mode keeps that policy for lane receipts only, such as a tests, build, or mergeability result. (See pstack-runtime/SKILL.md#modes).

A lane receipt never stands in for the gate review. Read the head again right before you create the PR, mark it ready, or merge. When it moved, apply [Gate review](SKILL.md#gate-review) again.
