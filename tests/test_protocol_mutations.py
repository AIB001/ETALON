"""Finite protocol changes preserve evidence identity and never execute calculations."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, replace

import pytest

from etalon.active.cascade import CascadeRecipe, Readout
from etalon.active.mutations import DesignSpace, mutate_recipe
from etalon.campaign.design import component, compose


@pytest.fixture
def base():
    config = compose("mutation-base", [{"id": "measure", "title": "Properties", "mode": "serial",
        "criteria": [component("properties", "features.rdkit_properties@0.1.0",
                                settings={"batch_size": 128, "include_sa_score": True}),
                     component("other", "features.rdkit_properties@0.1.0")]}])
    return CascadeRecipe.freeze(config, Readout("properties", "property/v1", "mw"))


def setting(**changes):
    return {"op": "set_setting", "criterion_id": "properties", "path": "/batch_size",
            "expected": 128, "value": 64, **changes}


def reorder(**changes):
    return {"op": "reorder_criteria", "tier_id": "measure", "order": ["other", "properties"], **changes}


def insert(**changes):
    return {"op": "insert_criterion", "tier_id": "measure", "before": "properties",
            "criterion": component("sa", "synthesis.rdkit_sa_score@0.1.0"), **changes}


def apply(base, edit):
    return mutate_recipe(base, [edit], DesignSpace((edit,)))


def test_design_space_has_no_mutable_aliases():
    edit = setting(allowed_failures=["F_COMPONENT_FAILED"])
    space = DesignSpace((edit,), max_edits=2)
    frozen = space.as_dict()
    edit["value"] = 12
    edit["allowed_failures"].append("F_OTHER")
    space.allowed_edits[0]["allowed_failures"].append("F_OTHER")
    exported = space.as_dict()
    exported["allowed_edits"][0]["value"] = 3
    assert space.as_dict() == frozen
    assert DesignSpace.from_dict(frozen).fingerprint == space.fingerprint
    assert len(space.digest) == 64 and space.fingerprint.endswith(space.digest)
    with pytest.raises(FrozenInstanceError):
        space.max_edits = 4


def test_design_space_identity_does_not_depend_on_enumeration_order():
    assert DesignSpace((setting(), reorder())).fingerprint == DesignSpace((reorder(), setting())).fingerprint


@pytest.mark.parametrize("maximum", [True, False, 0, -1, 1.5, "2"])
def test_design_space_rejects_nonpositive_noninteger_maximum(maximum):
    with pytest.raises(ValueError, match="positive integer"):
        DesignSpace((setting(),), max_edits=maximum)


@pytest.mark.parametrize("edit", [
    {"op": "run_shell", "command": "true"},
    setting(extra="unreviewed"),
    setting(value=float("nan")),
    setting(value={0: "invalid JSON key"}),
    setting(allowed_failures="F_COMPONENT_FAILED"),
    setting(allowed_failures=["F_COMPONENT_FAILED", "F_COMPONENT_FAILED"]),
])
def test_design_space_rejects_malformed_operations(edit):
    with pytest.raises(ValueError):
        DesignSpace((edit,))


def test_design_space_rejects_duplicate_operations_even_with_different_failure_metadata():
    with pytest.raises(ValueError, match="duplicate"):
        DesignSpace((setting(), setting(allowed_failures=["F_COMPONENT_FAILED"])))


def test_design_space_requires_its_exact_versioned_shape():
    body = DesignSpace((setting(),)).as_dict()
    for changed in ({**body, "schema_version": True}, {**body, "unknown": 1}, {**body, "schema_version": 2}):
        with pytest.raises(ValueError):
            DesignSpace.from_dict(changed)


def test_setting_changes_only_tiers_and_preserves_readout_resource_and_base_identity(base):
    original = base.configuration
    changed = apply(base, setting())
    old, new = json.loads(original), json.loads(changed.configuration)
    assert new["tiers"][0]["criteria"][0]["settings"]["batch_size"] == 64
    assert {key: value for key, value in old.items() if key != "tiers"} == {
        key: value for key, value in new.items() if key != "tiers"}
    assert changed.readout_json == base.readout_json
    assert changed.input_files == base.input_files
    assert changed.infrastructure_commit == base.infrastructure_commit
    assert changed.protocol_id != base.protocol_id
    assert base.configuration == original


def test_setting_requires_exact_membership_including_failure_metadata(base):
    edit = setting(allowed_failures=["F_COMPONENT_FAILED"])
    space = DesignSpace((edit,))
    for unauthorized in (setting(), setting(value=32, allowed_failures=["F_COMPONENT_FAILED"])):
        with pytest.raises(ValueError, match="exact member"):
            mutate_recipe(base, [unauthorized], space)


@pytest.mark.parametrize("edit,match", [
    (setting(expected=42), "expected"),
    (setting(value=128), "no effective change"),
    (setting(path="/not_there"), "existing field"),
    (setting(criterion_id="not_there"), "exactly one"),
    (setting(path="/include_sa_score", expected=1, value=False), "expected"),
])
def test_setting_rejects_wrong_identity_expected_value_and_noops(base, edit, match):
    with pytest.raises(ValueError, match=match):
        apply(base, edit)


@pytest.mark.parametrize("path", ["", "/", "batch_size", "/bad~2key", "/schema_version",
                                  "/model_path", "/nested/receptorPath", "/PATH", "/command",
                                  "/nested/shell", "/executable", "/model/weights"])
def test_resource_code_schema_and_invalid_paths_cannot_be_whitelisted(path):
    with pytest.raises(ValueError):
        DesignSpace((setting(path=path),))


@pytest.mark.parametrize("value", [{"command": "anything"}, {"model_path": "/tmp/model"},
                                   "/tmp/model", "../model.bin", "https://example.org/model",
                                   "C:\\weights\\model.bin"])
def test_resource_code_values_cannot_be_hidden_in_container_edits(value):
    with pytest.raises(ValueError):
        DesignSpace((setting(value=value),))


def test_nested_settings_json_pointer_and_array_indices(base):
    config = json.loads(base.configuration)
    # Mutation is syntactic; the later compiler independently rejects unknown plugin settings.
    config["tiers"][0]["criteria"][0]["settings"]["tuning"] = {"a/b": {"x~y": [1, 2]}}
    nested = CascadeRecipe.freeze(config, Readout(**json.loads(base.readout_json)))
    edit = setting(path="/tuning/a~1b/x~0y/1", expected=2, value=3)
    changed = apply(nested, edit)
    assert json.loads(changed.configuration)["tiers"][0]["criteria"][0]["settings"]["tuning"]["a/b"]["x~y"] == [1, 3]
    for index in ("-1", "01", "+1", "2"):
        with pytest.raises(ValueError):
            apply(nested, setting(path="/tuning/a~1b/x~0y/" + index, expected=2, value=3))


def test_edits_cannot_cancel_each_other_or_repeat_or_exceed_limit(base):
    edit, inverse = setting(), setting(expected=64, value=128)
    space = DesignSpace((edit, inverse), max_edits=2)
    with pytest.raises(ValueError, match="no effective change"):
        mutate_recipe(base, [edit, inverse], space)
    with pytest.raises(ValueError, match="duplicate"):
        mutate_recipe(base, [edit, edit], space)
    for selected in ([], [edit, inverse, edit]):
        with pytest.raises(ValueError, match="max_edits"):
            mutate_recipe(base, selected, space)


def test_reorder_is_exact_existing_permutation(base):
    changed = apply(base, reorder())
    assert [criterion["id"] for criterion in json.loads(changed.configuration)["tiers"][0]["criteria"]] == ["other", "properties"]
    for edit in (reorder(order=["other"]), reorder(order=["other", "missing"]),
                 reorder(order=["properties", "other"]), reorder(tier_id="missing")):
        with pytest.raises(ValueError):
            apply(base, edit)
    with pytest.raises(ValueError, match="unique"):
        DesignSpace((reorder(order=["other", "other"]),))


@pytest.mark.parametrize("before,expected", [("properties", ["sa", "properties", "other"]),
                                            (None, ["properties", "other", "sa"])])
def test_insert_builtin_criterion_at_explicit_location(base, before, expected):
    changed = apply(base, insert(before=before))
    assert [criterion["id"] for criterion in json.loads(changed.configuration)["tiers"][0]["criteria"]] == expected
    assert changed.readout_json == base.readout_json


def test_insert_rejects_unknown_backend_identity_duplicates_and_missing_anchor(base):
    for edit in (insert(before="missing"),
                 insert(criterion=component("properties", "features.rdkit_properties@0.1.0")),
                 insert(criterion=component("standardize", "features.rdkit_properties@0.1.0"))):
        with pytest.raises(ValueError):
            apply(base, edit)
    edit = insert()
    edit["criterion"]["backend"] = "unreviewed.remote_import@1.0.0"
    with pytest.raises(ValueError, match="known built-in"):
        apply(base, edit)


def test_insert_rejects_new_resource_and_arbitrary_command_settings():
    for settings in ({"receptor_path": "/tmp/receptor"}, {"command": "echo unsafe"},
                     {"nested": {"modelPath": "relative.dat"}}):
        edit = insert()
        edit["criterion"]["settings"].update(settings)
        with pytest.raises(ValueError, match="resource/code"):
            DesignSpace((edit,))


def test_mutations_refuse_disabled_noop_nodes(base):
    config = json.loads(base.configuration)
    config["tiers"][0]["criteria"][0]["enabled"] = False
    disabled = CascadeRecipe.freeze(config, Readout(**json.loads(base.readout_json)))
    with pytest.raises(ValueError, match="disabled"):
        apply(disabled, setting())
    config["tiers"][0]["enabled"] = False
    config["tiers"].append({**config["tiers"][0], "id": "enabled_tier", "enabled": True,
                            "criteria": [component("third", "features.rdkit_properties@0.1.0")]})
    disabled_tier = CascadeRecipe.freeze(config, Readout(**json.loads(base.readout_json)))
    with pytest.raises(ValueError, match="disabled"):
        apply(disabled_tier, reorder())
    edit = insert()
    edit["criterion"]["enabled"] = False
    with pytest.raises(ValueError, match="disabled"):
        apply(base, edit)


def test_pinned_resources_are_preserved_and_changed_resources_refused(base, tmp_path):
    resource = tmp_path / "reference.dat"
    resource.write_text("original", encoding="utf-8")
    pinned = CascadeRecipe.freeze(json.loads(base.configuration), Readout(**json.loads(base.readout_json)), files=(resource,))
    assert apply(pinned, setting()).input_files == pinned.input_files
    resource.write_text("modified", encoding="utf-8")
    with pytest.raises(ValueError, match="resource changed"):
        apply(pinned, setting())


def test_missing_resource_and_changed_infrastructure_are_refused(base, tmp_path):
    missing = replace(base, input_files=((str(tmp_path / "absent.dat"), "0" * 64),))
    with pytest.raises(ValueError, match="missing or unreadable"):
        apply(missing, setting())
    with pytest.raises(ValueError, match="infrastructure changed"):
        apply(replace(base, infrastructure_commit="different"), setting())


def test_mutation_does_not_execute_a_tool(base, monkeypatch):
    from etalon.boundary.screen import Screen

    def forbidden(*args, **kwargs):
        pytest.fail("mutation must not execute a calculation")

    monkeypatch.setattr(Screen, "run", forbidden)
    assert apply(base, insert()).protocol_id != base.protocol_id
