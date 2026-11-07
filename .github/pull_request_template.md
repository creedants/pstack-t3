**What changed and why.**

**How you verified it.** For a skill behavior change, name the provider that led the T3 thread and what it did.

**Risk.** Say what could break and who would notice. Or write "Low:" and one reason.

**How to revert.** Write the command or steps. Say when a plain `git revert` of the merge commit is not enough, such as state written outside the repository, a migration, a release tag, or an installed file.

- [ ] `python3 scripts/build.py` passes and the regenerated `skills/` is committed.
- [ ] `python3 scripts/run_tests.py` passes.
- [ ] No edits to `vendor/` or hand edits to `skills/`.
- [ ] A user-facing change has one bullet in `changes/<encoded branch>.md`, as `CONTRIBUTING.md` describes.
