- kind polish
- `t3/runtime.md` replaces `the row below` with `the row in [light.md](light.md) that names it` and points reuse at new `t3/added/pstack-runtime/gate-review.md`.
- `wc -w t3/runtime.md` reads 9188 before and 9014 after. New `t3/added/pstack-runtime/gate-review.md` reads 309 words.
- Gate links before, each resolves to `### Gate review` in `skills/pstack-runtime/SKILL.md`.
  `t3/runtime.md` `[Gate review](#gate-review)` resolves in file to `### Gate review`.
  `t3/added/pstack-runtime/light.md` nine `(SKILL.md#gate-review)` rows resolve to `SKILL.md` `### Gate review`.
  `t3/added/pstack-runtime/failures.md` `[Gate review](SKILL.md#gate-review)` resolves to `SKILL.md` `### Gate review`.
  `t3/added/brigade/SKILL.md` and `recovery.md` and `opening-a-pr.md` `SKILL.md#gate-review` links resolve to `skills/pstack-runtime/SKILL.md` `### Gate review`.
- Gate links after keep every link above and add two that resolve.
  `[Reusing a verdict](gate-review.md#reusing-a-verdict)` in `SKILL.md` resolves to `gate-review.md` `## Reusing a verdict`.
  `[Gate review](SKILL.md#gate-review)` in `gate-review.md` resolves to `SKILL.md` `### Gate review`.
  All `light.md` `(SKILL.md#gate-review)` rows still resolve to `SKILL.md` `### Gate review`.
  `opening-a-pr.md` keeps `(../../pstack-runtime/SKILL.md#gate-review)` to `SKILL.md` `### Gate review`.
- Moved Attempt lines still stated.
  old `The lines include Attempt:.` still in `### Resolve and carry the mode` as `The printed lines include Attempt:.`.
  old `Under Mode: light they also include any Waived by mode: line.` still in same bullet.
  old `Under Mode: full there is no Waived by mode: line, and the brief must not add one.` still in same bullet.
  `Delegation` step 4 now says `See [Resolve and carry the mode](#resolve-and-carry-the-mode) for the Attempt: and Waived by mode: lines.`.
- Collapsed reuse sentences still stated in `gate-review.md` `## Reusing a verdict`.
  old `Two sources can show both today.` kept verbatim.
  old source 1 `brigade.py --at pass check` with `--json`, `crossFamily true`, `Never read pass.tsv`, `Never take without --json` kept verbatim.
  old source 2 `A verdict this run launched at that exact SHA.` kept verbatim.
  old four `does not qualify` bullets kept verbatim.
  old `Full mode keeps its patch-id policy in Shipping and the Autopilot playbooks.` kept verbatim.
  old `Light mode keeps that policy for lane receipts only` kept with `(See pstack-runtime/SKILL.md#modes)`.
  `SKILL.md` keeps `Launch a new gate unless a current qualifying verdict exists at the exact head SHA.`, `Current` and `Qualifying` bullets verbatim, one `pass check <item> --sha <head> --json` line, pointer to `gate-review.md`, and lane receipt sentences.
- Tightened watch heartbeat still states every rule.
  old `When the event you are waiting for is the merge itself, create a schedule_task heartbeat` still as `When the wait is for the merge itself, add a schedule_task heartbeat`.
  old `A merge never wakes the thread, so the heartbeat is how you learn that the PR merged.` still as `A merge never wakes the thread.` and `The heartbeat is how you learn the PR merged.`.
  `3600000`, `900000`, merge queue, human mode, merge mode exception, `required-checks wake runs land`, forge commands stay, `Run them after a wake`, `They are not the wait` all still stated.
- `tests/test_pstack_t3.py` adds `gate-review.md` to `RUNTIME_DOCS` and asserts reuse text, bullet equality, no `the row below`, and no `|` line in `gate-review.md`.
