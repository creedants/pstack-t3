if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

import json
import tempfile
import unittest
from .catalogs import (
    LIVE_EFFORT_MODEL,
    LIVE_EFFORT_VALUES,
    LIVE_VARIANT_MODEL,
    LIVE_VARIANT_VALUES,
    live_catalog,
)
from .support import Repo, setUpModule


PARENT = f"opencode/{LIVE_VARIANT_MODEL}"


def _catalog(model_id, options):
    return {"providers": [{
        "providerInstanceId": "opencode",
        "driverKind": "opencode",
        "canRunChildTask": True,
        "constraints": [],
        "models": [{"id": model_id, "options": options}],
    }]}


def _select(option_id, values):
    return {"id": option_id, "type": "select", "options": [{"id": value} for value in values]}


def variant_seat(variant=None):
    seat = {"providerInstanceId": "opencode", "model": LIVE_VARIANT_MODEL}
    if variant is not None:
        seat["options"] = {"variant": variant}
    return seat


def effort_seat(effort=None):
    seat = {"providerInstanceId": "codex", "model": LIVE_EFFORT_MODEL}
    if effort is not None:
        seat["options"] = {"reasoningEffort": effort}
    return seat


def run_show(catalog, roles_file, parent=PARENT, brief_mode=None):
    with tempfile.TemporaryDirectory() as directory:
        repo = Repo(directory)
        path = repo.directory / "catalog.json"
        repo.put(path, catalog)
        repo.put(repo.user, roles_file)
        argv = ["show", "--catalog", str(path), "--parent", parent, "--role", "bug-fix"]
        if brief_mode is not None:
            argv.extend(["--brief-mode", brief_mode])
        return repo.run(*argv)


def run_validate(catalog, roles_file, parent=PARENT):
    with tempfile.TemporaryDirectory() as directory:
        repo = Repo(directory)
        path = repo.directory / "catalog.json"
        repo.put(path, catalog)
        repo.put(repo.user, roles_file)
        return repo.run("validate", "--catalog", str(path), "--parent", parent)


def seats_of(completed):
    return json.loads(completed.stdout)["roles"]["bug-fix"]["seats"]


class LiveVariantBudgetTest(unittest.TestCase):
    """A budget reaches a live-shaped variant option, which carries values and no type."""

    def test_live_variant_model_adds_the_budget_option(self):
        roles_file = {"budget": "large", "roles": {"bug-fix": [variant_seat()]}}
        completed = run_show(live_catalog(), roles_file, brief_mode="full")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(seats_of(completed), [variant_seat("xhigh")])

    def test_live_variant_model_gets_the_light_medium_cap(self):
        roles_file = {"mode": "light", "roles": {"bug-fix": [variant_seat("max")]}}
        completed = run_show(live_catalog(), roles_file)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(seats_of(completed), [variant_seat("medium")])


class LiveEffortBudgetTest(unittest.TestCase):
    """A budget reaches a live-shaped reasoningEffort option, which carries values and no type."""

    def test_live_effort_model_adds_the_budget_option(self):
        roles_file = {"budget": "large", "roles": {"bug-fix": [effort_seat()]}}
        completed = run_show(live_catalog(), roles_file, parent=f"codex/{LIVE_EFFORT_MODEL}", brief_mode="full")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(seats_of(completed), [effort_seat("xhigh")])


class LiveOptionValueTest(unittest.TestCase):
    """A value a live option does not offer is refused, as on the type plus options shape."""

    def test_live_variant_refuses_a_made_up_value_through_show_and_validate(self):
        roles_file = {"mode": "full", "roles": {"bug-fix": [variant_seat("made-up")]}}
        shown = run_show(live_catalog(), roles_file)
        self.assertEqual(shown.returncode, 0, shown.stderr)
        entry = json.loads(shown.stdout)["roles"]["bug-fix"]
        self.assertEqual(entry["seats"], [variant_seat()])
        self.assertIn(
            f"dropped option variant='made-up' is not one of {', '.join(LIVE_VARIANT_VALUES)}",
            entry["notes"],
        )
        validated = run_validate(live_catalog(), roles_file)
        self.assertEqual(validated.returncode, 1, validated.stdout)
        self.assertIn(
            f"bug-fix: option variant='made-up' is not one of {', '.join(LIVE_VARIANT_VALUES)}",
            validated.stdout,
        )

    def test_live_effort_refuses_a_made_up_value_through_show_and_validate(self):
        roles_file = {"mode": "full", "roles": {"bug-fix": [effort_seat("made-up")]}}
        parent = f"codex/{LIVE_EFFORT_MODEL}"
        shown = run_show(live_catalog(), roles_file, parent=parent)
        self.assertEqual(shown.returncode, 0, shown.stderr)
        entry = json.loads(shown.stdout)["roles"]["bug-fix"]
        self.assertEqual(entry["seats"], [effort_seat()])
        self.assertIn(
            f"dropped option reasoningEffort='made-up' is not one of {', '.join(LIVE_EFFORT_VALUES)}",
            entry["notes"],
        )
        validated = run_validate(live_catalog(), roles_file, parent=parent)
        self.assertEqual(validated.returncode, 1, validated.stdout)
        self.assertIn(
            f"bug-fix: option reasoningEffort='made-up' is not one of {', '.join(LIVE_EFFORT_VALUES)}",
            validated.stdout,
        )

    def test_live_boolean_option_refuses_a_non_bool_value(self):
        seat = {"providerInstanceId": "opencode", "model": LIVE_VARIANT_MODEL, "options": {"fastMode": "yes"}}
        roles_file = {"mode": "full", "roles": {"bug-fix": [seat]}}
        shown = run_show(live_catalog(), roles_file)
        self.assertEqual(shown.returncode, 0, shown.stderr)
        entry = json.loads(shown.stdout)["roles"]["bug-fix"]
        self.assertEqual(entry["seats"], [variant_seat()])
        self.assertIn("dropped option fastMode='yes' must be true or false", entry["notes"])
        validated = run_validate(live_catalog(), roles_file)
        self.assertEqual(validated.returncode, 1, validated.stdout)
        self.assertIn("bug-fix: option fastMode='yes' must be true or false", validated.stdout)


class LiveExcludeTest(unittest.TestCase):
    """An exclude entry that names an option value still refuses a live-shaped seat the budget produces."""

    def test_live_shape_declared_option_exclude_refuses_the_capped_seat(self):
        roles_file = {
            "mode": "light",
            "roles": {"bug-fix": [variant_seat("max")]},
            "exclude": [{"options": {"variant": "medium"}}],
        }
        shown = run_show(live_catalog(), roles_file)
        self.assertEqual(shown.returncode, 2, shown.stderr)
        self.assertEqual(shown.stdout, "")
        self.assertIn("exclude entry 1 (variant=medium)", shown.stderr)
        validated = run_validate(live_catalog(), roles_file)
        self.assertEqual(validated.returncode, 1, validated.stdout)
        self.assertIn("exclude entry 1 (variant=medium)", validated.stdout)


class OlderOptionShapeTest(unittest.TestCase):
    """The type plus options shape a saved catalog carries still caps and still refuses a made-up value."""

    def catalog(self):
        return _catalog(LIVE_VARIANT_MODEL, [_select("variant", LIVE_VARIANT_VALUES)])

    def test_older_shape_still_caps_and_still_refuses_a_made_up_value(self):
        capped = {"budget": "large", "roles": {"bug-fix": [variant_seat("max")]}}
        shown = run_show(self.catalog(), capped, brief_mode="full")
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertEqual(seats_of(shown), [variant_seat("xhigh")])
        made_up = {"mode": "full", "roles": {"bug-fix": [variant_seat("made-up")]}}
        validated = run_validate(self.catalog(), made_up)
        self.assertEqual(validated.returncode, 1, validated.stdout)
        self.assertIn(
            f"bug-fix: option variant='made-up' is not one of {', '.join(LIVE_VARIANT_VALUES)}",
            validated.stdout,
        )


if __name__ == "__main__":
    unittest.main()
