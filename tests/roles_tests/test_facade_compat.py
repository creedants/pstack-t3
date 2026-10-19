"""Tests for facade name and pickle compatibility."""

import importlib.util
import pickle
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ROLES_PY = ROOT / "t3/scripts/roles.py"
if str(ROOT / "t3/scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "t3/scripts"))

BASE_PUBLIC_NAMES = (
    "ATTEMPTS", "AUTHOR_CHECKED_ROLES", "BUDGETS", "Backup",
    "CATALOG_REQUIRED", "CURSOR_DRIVER", "DEFAULT_MODE", "DRIVER_RUNTIME_MODES",
    "DefaultSelection", "EFFORT_IDS", "EXCLUDE_RULE", "GRAMMAR_LABELS",
    "HAIKU_BRIEF", "IGNORED", "INHERIT", "LADDER",
    "LIGHT_WAIVERS", "MISSING_MODE", "MODES", "ModeDecision",
    "ModeSettingsError", "NO_CATALOG_INFO", "NO_PARENT_INFO", "OpenCodeAllow",
    "PANEL_BACKUP_ROLE", "PANEL_CATALOG_NOTE", "PANEL_GATE_ROLE", "PANEL_MAXIMUM_REPRODUCED_BLOCKERS",
    "PANEL_MINIMUM_PASSES", "PANEL_ROLES", "PANEL_RULE", "PanelVerdict",
    "Parent", "Path", "REVIEW_ROLES", "ROLES",
    "RUNTIME_MODE_GAP", "RolesError", "SINGLE_ROLES", "SPECIAL",
    "UNSET_NOTE", "UNSET_ROLE_NOTE", "USAGE_LIMIT_PATTERNS", "Waivers",
    "allow_for", "allow_shape_error", "apply_budget", "apply_level",
    "argparse", "attach_haiku_brief", "author_families", "back_up",
    "backup_option_problems", "backup_problems", "backup_seat", "backups_for",
    "bare_id", "brief_lines", "brief_problems", "canonical",
    "catalog_for_check", "catalog_provider", "catalog_says_free", "check_option_values",
    "check_shape", "command_backup", "command_check_brief", "command_mode",
    "command_show", "command_validate", "command_write", "config_home",
    "configured_seats", "cursor_note", "dataclass", "default_seats",
    "durable_reason", "effective_mode", "effort_option", "errno",
    "escalate", "escalated_path", "escalation_reason", "exclude_label",
    "exclude_option_problems", "exclude_reason", "exclude_rule_matches", "excluded_backup_problems",
    "excluded_problems", "family", "find_model", "finish_entry",
    "finish_seat", "fnmatch", "for_runtime_mode", "functools",
    "given_parent", "given_runtime_mode", "inherit_parent", "inherit_replacement",
    "is_opencode_provider", "is_snapshot_path", "json", "launch_provider",
    "launch_refusal", "lease_paths", "light_cap_gap", "load_brief_rules",
    "load_catalog", "load_json", "load_show_catalog", "main",
    "merged_config", "mode_problems", "model_matches", "models_of",
    "new_file", "non_negative_int", "normalized_bare", "opencode_allow_line",
    "opencode_allow_lines", "opencode_model_is_metered", "options_of", "os",
    "panel_shortfall", "panel_verdicts", "parent_for", "parse_applied_options",
    "parse_backups", "parse_exclude", "parse_json", "parse_parent",
    "parse_seat", "persona_path", "playbook_stem", "playbook_stems",
    "playbook_values", "posixpath", "project_config_path", "provider_matches",
    "providers_by_id", "rank", "re", "replace",
    "resolve", "resolve_seat", "runnable", "runs_haiku_55",
    "runs_on_cursor", "seat_budget", "seat_level", "segments_match",
    "settle_inherit_catalog", "settle_options", "snapshot_path", "stamp_parent",
    "stored_allow", "subprocess", "suppress", "sys",
    "tracked_files", "usage_limited", "user_config_path", "validate",
    "waived_value", "work_key", "write_atomic",
)


def load_by_path():
    name = "pstack_roles"
    if name in sys.modules:
        del sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROLES_PY)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def check_names(test, module):
    missing = [key for key in BASE_PUBLIC_NAMES if not hasattr(module, key)]
    test.assertEqual(missing, [], f"missing names {missing}")


def check_pickle(test, module):
    parent = module.Parent("grok", "grok-4.7")
    test.assertEqual(pickle.loads(pickle.dumps(parent)), parent)
    parsed = pickle.loads(pickle.dumps(module.parse_seat))
    test.assertIs(parsed, module.parse_seat)
    decision = module.ModeDecision("light", "default", "default")
    test.assertEqual(pickle.loads(pickle.dumps(decision)), decision)
    backup = module.Backup("relaunch", "report")
    test.assertEqual(pickle.loads(pickle.dumps(backup)), backup)
    error = module.RolesError("boom")
    restored = pickle.loads(pickle.dumps(error))
    test.assertIs(type(restored), module.RolesError)
    test.assertEqual(restored.args, ("boom",))


class FacadeImportCase(unittest.TestCase):
    def test_import_roles_exposes_base_names(self):
        import roles
        check_names(self, roles)

    def test_import_roles_pickle_round_trips(self):
        import roles
        check_pickle(self, roles)


class FacadePathCase(unittest.TestCase):
    def test_path_load_exposes_base_names(self):
        module = load_by_path()
        self.addCleanup(sys.modules.pop, "pstack_roles", None)
        check_names(self, module)

    def test_path_load_pickle_round_trips(self):
        module = load_by_path()
        self.addCleanup(sys.modules.pop, "pstack_roles", None)
        check_pickle(self, module)


if __name__ == "__main__":
    unittest.main()
