---
name: Upstream sync
about: A newer upstream pstack is out and needs porting
---

**Upstream version or commit.**

**Overrides whose upstream file changed.** Paste the rows from `python3 scripts/sync_upstream.py --merge --dry-run`, which writes nothing in the checkout. Each row names one override and says what the merge does with it.
