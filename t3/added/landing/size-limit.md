## File size limit

Agent-written files stay small enough to review. Tokens equal bytes divided by 4 with integer division. The limit is 5000 tokens per file unless the contract sets another maximum.

The committed size-baseline.json names the allowance of each larger file. The landing contract only points at that file with sizeBaseline. The queue reads the baseline from the trunk tip with git show and judges the rebased candidate at land time. Submit never judges sizes. A failure bounces the entry with one line per file naming the path, the tokens, the allowance, and the split step.

Turn the limit on with `python3 <skills>/landing/scripts/land.py --repo <checkout> init --trunk main --mode <mode> --max-file-tokens 5000`. A rerun without the flags keeps the stored values. `land.py status` names the limit while the contract sets one.

Judge work with `land.py size`. The default judges changed worktree files. `--staged` judges staged files from the index. `--all` judges every file. The first run seeds the baseline with each over-limit file at its current tokens. `--shrink` lowers stored allowances that fell and never raises one. `--baseline` reads and writes another file instead of the contract one.

Split an over-limit file before landing. Code moves one responsibility to a sibling module. Tests start a new file for the feature and share helpers. Skill and doc text moves rarely needed sections to an on-demand file with a pointer saying when to read it.

Skipped paths never count. Generated paths from the contract, vendored upstream under vendor, images, lockfiles, codegen skills, docs/cli, and docs/skills.md stay out. The baseline file itself stays out.
