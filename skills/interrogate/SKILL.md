---
name: interrogate
description: "Use for \"interrogate\", \"adversarial review\", \"multi-model review\", \"challenge this\", \"stress test this code\", \"find blind spots\", or \"tear this apart\". Multiple LLM reviewers challenge changes from independent angles."
---

# Interrogate

Read [the pstack-t3 runtime](../pstack-runtime/SKILL.md) before spawning workers, choosing models, scheduling, or isolating work. It maps those steps onto T3's orchestrator tools.

Spawn one reviewer per configured model to adversarially review code changes. The adversarial signal comes from model diversity, not assigned personas. The deliverable is a synthesized verdict. Do NOT auto-apply changes.

[The runtime's Modes section](../pstack-runtime/SKILL.md#modes) sets the mode lines of every brief this skill writes and how its spawns run in light mode.

## Step 1, Scope

Identify what to review from context:

- If the user points at specific files or a diff, use that.
- If on a feature branch, run `git diff main...HEAD` (or the appropriate base branch) for the full changeset.
- If the user's message references recent work, gather the relevant files.

Package the diff or file contents with any surrounding context files the reviewers need to understand the code.

## Step 2, Intent

Before spawning reviewers, state the intent in one clear paragraph from the user's message, commit messages, the PR description if one exists, and the code. If you're unsure about the intent, ask the user before proceeding.

## Step 3, Spawn Reviewers

Call `orchestrator_capabilities`. Paste that tool result into this quoted heredoc. If the catalog result is large, save it to a temporary file with the host's file tool and pass that path to `--catalog`.

```bash
python3 <pstack-runtime>/scripts/roles.py show --cwd "$PWD" --catalog - --parent "<inheritedProviderInstanceId>/<inheritedModel>" --role "interrogate reviewers" <<'JSON'
<the orchestrator_capabilities JSON>
JSON
```

The quoted heredoc sends the JSON unchanged. The command does not write the catalog into the repository, and parallel children do not share a file. It resolves the `interrogate reviewers` role per [the runtime's Roles section](../pstack-runtime/SKILL.md#roles).

Launch all reviewers in a single message with `delegate_task`, one per seat of the `interrogate reviewers` role, labeled Reviewer A, B, and so on to match the seat count.

Each reviewer gets `mode: "async"`, `role: "review"`, a `task` that is the filled template below, which is a read-only brief, a `clientRequestId` stable per seat, such as `interrogate-<slug>-a`, and `target` set to its seat's resolved target. Omit `target` for an `inherit` seat, so that reviewer runs on the parent model.

Retain every returned `taskId`. If `delegate_task` rejects a target, apply the runtime's fallback, spawn on the fallback seat, and say which reviewer changed and why. Do not block the review on a target issue. Never silently drop a seat.

Read `references/reviewer-prompt.md` and fill in the same template for every reviewer with the stated intent, the diff or file contents, the rubric from `references/rubric.md`, and the code-quality lens from `references/code-quality-review.md`.

Collect each reviewer's result per [the runtime's Delegation step 5](../pstack-runtime/SKILL.md#delegation). If a reviewer fails or returns nothing usable, proceed with N-1 and record the dropout.

## Step 4, Synthesize

As results come back, build a unified picture. Parse every reviewer's findings. Merge findings that describe the same issue differently, and note which models raised each one. Findings two or more models raised independently are the highest signal. Read a lone model's finding, but weight it accordingly. Note disagreements. If one model flags something and another explicitly says the opposite, that is context for the verdict.

## Step 5, Lead Judgment

You are the lead reviewer, a pragmatic senior engineer, not a neutral aggregator. Read `references/lead-judgment.md` for the full framework. Put every finding in one of four buckets, with the model(s) that raised it and a one-line rationale.

- **Act on**. Real issues affecting correctness, security, or maintainability given the actual goals. They would block a real PR.
- **Consider**. Legitimate, but you're not sure they outweigh the cost of addressing them now. Worth the user's attention.
- **Noted**. Technically valid but not actionable. Context-dependent, premature optimization, or low-impact at this stage.
- **Dismissed**. Wrong, nitpicky, or missing context, with a brief reason.

## Output Format

### Intent
> [The stated intent paragraph from Step 2]

### Reviewers
- Reviewer [label]: [provider/model, or inherit and the parent model], [N findings] (one bullet per reviewer)
- Fallbacks and dropouts, and whether the models actually differed

### Act On
[Each: description, which models raised it, why it matters.]

### Consider
[Each: description, which models raised it, the tradeoff.]

### Noted
[Brief list.]

### Dismissed
[Each with a brief rationale.]

### Agreement Map
[Where models agreed and diverged, and what that pattern tells us.]
