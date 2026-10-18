"""Patch a name in the _items submodule that reads it; patching it here does not reach callers."""
from brigade_lib._items.doc_gate import DocAllowed, DocPolicy, DocRefused, PreparedDocFire, _CATALOG, _SharedDoc, _can_cover_catalog, _covers, _fixed_docs, _gate_inventory, _held_from_tracked, _is_skill_file, _lease_parts, _prefix_docs, _repo_relative, _skill_roots, _walk_opaque, doc_policy, prepare_doc_fire
from brigade_lib._items.fire import block_note, checked_fire_label, fire, fire_label, record_blocked, record_fire_paths, spend_hold, unfireable, workers_full, write_lead_kind
from brigade_lib._items.retire import ARCHIVE_PREFIX, announce_archive, drop, other_items_threads, retired_worker_threads, worker_bindings
from brigade_lib._items.rounds import ATTEMPT_AFTER, attempt_kind, attempt_starts, item_escalation, latest_mode_note, menu_purpose, record_escalation
