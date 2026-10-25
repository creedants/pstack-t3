# Onboard questions

Which restaurant to open. This names the store directory. Record it with open. Before open creates it the summary prints the lines and a not saved line and writes nothing.

Who lands work. Options are `merge`, `human`, `push`, and `local`. For a repository with a remote, choose `merge`. Record it with `land.py init --mode` or `land.py mode`. When the repository already has a landing contract, its mode applies and onboard reports it as configured.

How often the coordinator replies. Options are `milestones`, `every-turn`, and `digest`. Recommend `milestones`. Record it with `open --reporting` or `set --reporting`. A missing key reads as `milestones` and is never missing.

Which mode the workers use. Options are `light` and `full`. For a project with routine dishes, choose `light`. Record it with `open --mode` or `set --mode`. A missing key leaves the choice to the roles files, so onboard lists it as missing until it is recorded.

Whether pull requests credit the models that did the work. Options are `on` and `off`. Record it with `open --pr-credit` or `set --pr-credit`. A missing key reads as off and is missing until it is recorded.

Which models the workers use. This is missing when neither the user roles file nor the project roles file exists. Record it with setup-pstack.

How many dishes run at once. This is a whole number of 1 or more. Recommend 2. Record it with `open --workers` or `set --workers`. A missing key reads as 2 and is never missing.

Which sources this restaurant owns. This is a comma separated list such as `github`. Record it with `set --intake`. An empty list is missing until at least one source is recorded. A source a sibling already owns stays with that sibling.

Which commands verify edits. Onboard reads the store's commands first, then the contract's checks for the check command. It guesses both from `package.json`, `Makefile`, `Cargo.toml`, and `go.mod`, plus `pyproject.toml` or `tox.ini` for the check command and `scripts/build.py` for the build command. The check command is missing when the store, the contract, and the manifest name no command. Record both as contract checks with `land.py init --check`; the queue runs those checks. Only a stored `buildCommand` or `checkCommand` guides edits verify and affected reports. The build command is missing when the store and the manifest name none. Offer the contract setup command as a worktree-create script per Open a restaurant step 6.

Where change notes go. This is a repository relative directory such as `changes`. For a repository that tracks doc changes, choose `changes`. Record it with `set --change-note-dir`. An unset key leases no fragment and is missing until it is recorded.

The rest need no question. Onboard shows their effective values in the summary and never lists them as missing. Their defaults are autofire `normal`, low reserve 1, round budget 3, the liveness default for the reporting level, context cap 300000, and provider `claude`. A creation writes no settings file unless it passes `--context-cap` or `--coordinator-provider`. Change any of them later with the matching `set` flag. The file size limit is not an onboard question. The landing contract stores it once `land.py init --max-file-tokens` sets it.
