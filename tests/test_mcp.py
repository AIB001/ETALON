"""The tool surface a model drives a campaign through, and the one tool it cannot complete.

A campaign has 750,000 actions, so the governance cannot be per-action confirmation. It is that the
expensive steps are guarded by cheap checks the model is expected to call, and that the guards refuse
rather than warn. Most of this file is about the guards refusing and about every tool declaring what it
spends before a model chooses rather than after.

Nothing here needs the MCP SDK: the tools are plain functions and a four-line collector stands in for a
server. That is deliberate -- a tool surface testable only through a running server is a tool surface
whose error paths do not get tested.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from etalon.mcp._common import Cost, absolute_path, fail, ok, tool
from etalon.mcp.server import SKILL_RESOURCE, costs


class Collector:
    """Stands in for an MCP server: records what a module registers."""

    def __init__(self) -> None:
        self.tools: dict[str, object] = {}

    def tool(self):  # noqa: ANN201 -- mirrors the SDK's untyped decorator factory
        def decorate(function):  # noqa: ANN001, ANN202
            self.tools[function.__name__] = function
            return function

        return decorate

    def resource(self, _uri: str):  # noqa: ANN201
        def decorate(function):  # noqa: ANN001, ANN202
            return function

        return decorate


def _tools() -> dict[str, object]:
    """Every tool the server registers, collected through the server's own module tuple.

    This used to carry its own copy of the module list, and the copy was missing ``sweep`` -- the
    same omission ``costs()`` had. So ``test_every_tool_declares_what_it_spends`` compared two
    copies of one wrong set and passed over 12 missing tools for as long as both lists agreed.
    """

    from etalon.mcp.server import tool_modules

    collector = Collector()
    for module in tool_modules():
        module.register(collector)
    return collector.tools


#: A handoff row of the kind MolCascade's shortlist exporter actually produces.
FROM_A_DRAWING = {
    "parent_id": "p:drawing",
    "molblock": "(rebuilt from SMILES)",
    "coordinate_source": "TWO_D_DEPICTION",
    "hydrogens": "IMPLICIT",
    "hydrogen_count": 0,
    "heavy_atom_count": 19,
    "formal_charge": 0,
    "stereo_smiles": None,
    "parent_smiles": "CC(C)NCC(O)COc1cccc2ccccc12",
    "protonation_state_id": "INHERITED_FROM_STANDARDIZER",
    "receptor_id": None,
    "status": "OK",
}


#: The same row as a producer that kept its geometry would emit it. Used by the authorization
#: tests, which need a record that survives the check as well as one that does not.
CLEAN = {
    "parent_id": "p:clean",
    "molblock": "(a docked pose)",
    "coordinate_source": "DOCKED_POSE",
    "hydrogens": "EXPLICIT_ALL",
    "hydrogen_count": 14,
    "heavy_atom_count": 19,
    "formal_charge": 0,
    "stereo_smiles": "CC(C)NCC(O)COc1cccc2ccccc12",
    "parent_smiles": "CC(C)NCC(O)COc1cccc2ccccc12",
    "protonation_state_id": "propka:ph7.4",
    "receptor_id": "sha256:abc123",
    "status": "OK",
}

# -- the error shape ------------------------------------------------------


def test_every_error_carries_a_hint_and_a_retryable_flag() -> None:
    """retryable separates "call this again" from "fix the input".

    A model that cannot tell those apart will give up on something transient or hammer something
    permanent, and both look like the tool failing.
    """

    payload = json.loads(fail("Code", "message", hint="do this", retryable=True, extra=1))

    assert payload["ok"] is False
    assert payload["error"]["retryable"] is True
    assert payload["error"]["hint"] == "do this"
    assert payload["error"]["context"]["extra"] == 1


def test_a_raising_tool_returns_the_error_shape_rather_than_a_traceback() -> None:
    @tool(Cost.FREE)
    def probe(value: int) -> str:
        if value < 0:
            raise ValueError("negative")
        return ok(value=value)

    assert json.loads(probe(1))["ok"] is True
    refused = json.loads(probe(-1))
    assert refused["ok"] is False
    assert refused["error"]["retryable"] is False
    assert refused["error"]["context"]["tool"] == "probe"


def test_a_relative_path_is_refused_with_the_path_in_the_message() -> None:
    """A stdio server inherits a working directory the caller cannot see."""

    with pytest.raises(ValueError, match="absolute path"):
        absolute_path("relative/thing.pdb", label="receptor_path")
    assert absolute_path("/abs/thing.pdb", label="receptor_path").is_absolute()


# -- the cost classification ---------------------------------------------


def test_every_tool_declares_what_it_spends() -> None:
    """Derived from the decorator, so a tool cannot be registered without one."""

    classification = costs()

    assert classification, "no tools registered"
    assert set(classification) == set(_tools())
    assert all(value in {c.value for c in Cost} for value in classification.values())


def test_the_cost_classification_covers_every_module_the_server_registers() -> None:
    """``costs()`` kept its own module list and it was missing one of the seven.

    It reported 45 of 57 tools and omitted every tool in ``mcp/sweep.py``, including
    ``etalon_authorize_gates`` and ``etalon_sweep_emit`` -- the two that stand between a
    calibration and a campaign's worth of GPU time. Both readers now walk one tuple, so this
    asserts the consequence rather than the mechanism: a namespace cannot go missing again.
    """

    from etalon.mcp.server import tool_modules

    classification = costs()

    assert len(tool_modules()) == 7
    assert len(classification) == 57
    for namespace in (
        "etalon_sweep_", "etalon_data_", "etalon_workflow_", "etalon_active_",
        "etalon_screen_", "etalon_executor_", "etalon_council_",
    ):
        assert any(name.startswith(namespace) for name in classification), namespace
    # The two the omission actually hid.
    assert classification["etalon_authorize_gates"] == "free"
    assert classification["etalon_sweep_emit"] == "cheap"


def test_the_cost_a_tool_declares_is_the_one_its_description_shows() -> None:
    """The prefix a model reads is a hand-typed literal; nothing prepends it from the decorator.

    ``tool(cost)`` only sets ``etalon_cost``, and its only reader is ``costs()``. So the
    FREE/CHEAP/SPENDS/NEVER word at the start of every description is a convention, one typo away
    from telling a model that a spending tool is free. Asserted here for all 57 rather than
    trusted, which is what the decorator's own docstring used to claim the server did.
    """

    from etalon.mcp._common import Cost

    expected = {
        Cost.FREE: "FREE.",
        Cost.CHEAP: "CHEAP.",
        Cost.SPENDS: "SPENDS",
        Cost.NEVER: "NEVER COMPLETED BY A MODEL.",
    }
    wrong: list[str] = []
    for name, function in sorted(_tools().items()):
        cost = getattr(function, "etalon_cost", None)
        assert cost is not None, f"{name} carries no cost"
        description = (function.__doc__ or "").strip()
        if not description.startswith(expected[cost]):
            wrong.append(f"{name}: declares {cost.value}, description starts {description[:28]!r}")

    assert not wrong, "\n".join(wrong)


def test_the_planning_tools_are_all_free() -> None:
    """A campaign is planned by calling these repeatedly; any cost would discourage that."""

    classification = costs()

    for name in ("etalon_plan_campaign", "etalon_tune_screen", "etalon_stages", "etalon_infrastructure"):
        assert classification[name] == "free", name


def test_the_waiver_tool_is_the_only_one_a_model_cannot_complete() -> None:
    classification = costs()
    never = [name for name, value in classification.items() if value == "never-by-a-model"]

    assert never == ["etalon_recommend_waiver"]


def test_active_inspection_does_not_create_a_journal(tmp_path):
    database = tmp_path / "absent.sqlite"
    response = json.loads(_tools()["etalon_active_status"](str(database)))
    assert response["ok"] is False
    assert not database.exists()


def test_active_mcp_replay_and_plan_share_the_persisted_feedback(tmp_path):
    from etalon.active.replay import synthetic_manifest

    manifest = tmp_path / "oracle.json"
    manifest.write_text(json.dumps(synthetic_manifest(size=16)), encoding="utf-8")
    database = tmp_path / "journal.sqlite"
    result = json.loads(_tools()["etalon_active_replay"](str(manifest), str(database), rounds=2))
    assert result["ok"] and len(result["rounds"]) == 2
    plan = json.loads(_tools()["etalon_active_plan"](str(database)))
    assert plan["ok"] and plan["model"]["training_size"] == result["state"]["admitted"]
    assert all(c["evidence"]["model_hash"] == plan["model"]["training_hash"] for c in plan["choices"])


# -- the guards refuse ----------------------------------------------------


def test_a_record_built_from_a_drawing_is_refused_and_the_remedy_is_not_a_waiver() -> None:
    result = json.loads(_tools()["etalon_check_handoff"](json.dumps([FROM_A_DRAWING])))

    assert result["may_spend"] == 0
    blocking = result["verdicts"][0]["blocking"]
    assert "F_COORDINATES_ARE_A_DEPICTION" in blocking
    assert "F_HYDROGENS_IMPLICIT" in blocking
    assert "not fixable by a waiver" in result["next_step"]


def test_an_unchecked_check_is_reported_rather_than_counted_as_clean() -> None:
    clean = {
        **FROM_A_DRAWING,
        "coordinate_source": "EMBEDDED_CONFORMER",
        "hydrogens": "EXPLICIT_ALL",
        "hydrogen_count": 21,
        "stereo_smiles": FROM_A_DRAWING["parent_smiles"],
        "protonation_state_id": "propka:ph7.4",
    }

    result = json.loads(_tools()["etalon_check_handoff"](json.dumps([clean])))

    assert result["may_spend"] == 1
    assert result["verdicts"][0]["could_not_be_checked"]
    assert "could_not_be_checked" in result["next_step"]


def test_the_waiver_tool_returns_a_recommendation_awaiting_a_person() -> None:
    result = json.loads(
        _tools()["etalon_recommend_waiver"](
            "F_PROTONATION_UNDECIDED",
            "Screened at pH 7.4 and the ligand is taken in the standardizer's neutral form.",
            "2099-01-01",
            "claude-sonnet-5",
        )
    )

    assert result["recommended_waiver"]["status"] == "AWAITING_A_PERSON"
    assert result["recommended_waiver"]["advisor_kind"] == "language_model"
    assert "granted_by" in result["next_step"]


# -- the hole the waived argument used to be ------------------------------
#
# Both governance tools took ``waived`` as a comma-separated list of fault codes and turned it
# straight into a set that ``blocking`` honoured. No Waiver was constructed, so every guard in
# judgment.waiver was bypassed: a model could release F_COORDINATES_ARE_A_DEPICTION -- which
# check_handoff's own next_step calls unfixable by a waiver -- by typing its name. These tests are
# the hole, stated as behaviour.


def _granted(code: str, expires: str = "2099-01-01") -> str:
    return json.dumps(
        [
            {
                "code": code,
                "reason": "Screened at pH 7.4; the ligand is accepted in the neutral form.",
                "granted_by": "R. Feng, project chemist",
                "expires": expires,
            }
        ]
    )


def test_a_bare_fault_code_is_refused_where_a_waiver_is_required() -> None:
    """The old shape, refused, with the remedy in the message rather than in the documentation."""

    result = json.loads(
        _tools()["etalon_check_handoff"](
            json.dumps([FROM_A_DRAWING]), waived="F_COORDINATES_ARE_A_DEPICTION,F_HYDROGENS_IMPLICIT"
        )
    )

    assert result["ok"] is False
    assert result["error"]["retryable"] is False
    assert "granted waivers" in result["error"]["message"]
    assert "etalon_recommend_waiver" in result["error"]["message"]


def test_a_model_cannot_grant_the_waiver_it_recommended() -> None:
    """The guard that already existed in judgment.waiver, now on the path that is actually used."""

    signed_by_a_model = json.dumps(
        [
            {
                "code": "F_PROTONATION_UNDECIDED",
                "reason": "Screened at pH 7.4; the ligand is accepted in the neutral form.",
                "granted_by": "claude-sonnet-5",
                "expires": "2099-01-01",
            }
        ]
    )

    result = json.loads(
        _tools()["etalon_check_handoff"](json.dumps([FROM_A_DRAWING]), waived=signed_by_a_model)
    )

    assert result["ok"] is False
    assert "language model" in result["error"]["message"]


def test_a_waiver_cannot_release_a_cause_that_was_never_fixable_by_one() -> None:
    """A drawing is not a protonation state: the consequence is that the number is about another
    molecule, and no grantor makes that untrue. The refusal is the taxonomy's, reached through
    Waiver rather than around it."""

    result = json.loads(
        _tools()["etalon_check_handoff"](
            json.dumps([FROM_A_DRAWING]), waived=_granted("F_COORDINATES_ARE_A_DEPICTION")
        )
    )

    # The waiver itself is well formed and accepted; what it releases is one cause, and the record
    # is still refused for the other one it cannot touch.
    assert result["ok"] is True
    assert result["may_spend"] == 0
    assert "F_HYDROGENS_IMPLICIT" in result["verdicts"][0]["blocking"]
    assert result["verdicts"][0]["proceeding_under_waiver"] == ["F_COORDINATES_ARE_A_DEPICTION"]


def test_a_granted_waiver_is_echoed_with_the_name_on_it() -> None:
    """A parser cannot tell an invented person from a real one. An operator reading the name can,
    so the name has to be in the result rather than only in the argument."""

    result = json.loads(
        _tools()["etalon_check_handoff"](
            json.dumps([FROM_A_DRAWING]), waived=_granted("F_PROTONATION_UNDECIDED")
        )
    )

    in_force = result["waivers_in_force"]
    assert [entry["code"] for entry in in_force] == ["F_PROTONATION_UNDECIDED"]
    assert in_force[0]["granted_by"] == "R. Feng, project chemist"
    assert in_force[0]["accepted_consequence"] == "wrong_subject"
    assert in_force[0]["granted_at"]


def test_an_expired_waiver_stops_releasing_its_fault() -> None:
    """The raw frozenset honoured no date at all, so a waiver went on working for as long as the
    string was passed."""

    result = json.loads(
        _tools()["etalon_check_handoff"](
            json.dumps([FROM_A_DRAWING]),
            waived=_granted("F_PROTONATION_UNDECIDED", expires="2020-01-01"),
        )
    )

    assert result["waivers_in_force"] == []
    assert [entry["code"] for entry in result["waivers_expired"]] == ["F_PROTONATION_UNDECIDED"]
    assert "F_PROTONATION_UNDECIDED" in result["verdicts"][0]["blocking"]
    assert "expired" in result["next_step"]


def test_the_admissibility_gate_takes_the_same_shape_and_refuses_the_same_way() -> None:
    """It used to be worse here: it built a Waiver with a hardcoded grantor of
    'recorded-separately', an expiry in 2099, and a reason pointing at a campaign ledger no MCP tool
    writes. A label from a molecule built from a drawing entered the training set on a typed
    string."""

    measurements = json.dumps(
        [
            {
                "parent_id": "b",
                "cheap_value": -7.0,
                "expensive_value": -29.0,
                "observations": [
                    {"code": "F_COORDINATES_ARE_A_DEPICTION", "fired": True, "detail": "drawing"}
                ],
            }
        ]
    )

    bare = json.loads(
        _tools()["etalon_rule_admissible"](measurements, waived="F_COORDINATES_ARE_A_DEPICTION")
    )
    assert bare["ok"] is False
    assert "granted waivers" in bare["error"]["message"]

    granted = json.loads(
        _tools()["etalon_rule_admissible"](
            measurements, waived=_granted("F_COORDINATES_ARE_A_DEPICTION")
        )
    )
    assert granted["ok"] is True
    assert granted["report"]["admitted"] == 1
    assert granted["waivers_in_force"][0]["granted_by"] == "R. Feng, project chemist"


def test_a_waiver_missing_the_field_that_makes_it_one_is_refused() -> None:
    incomplete = json.dumps([{"code": "F_PROTONATION_UNDECIDED", "expires": "2099-01-01"}])

    result = json.loads(
        _tools()["etalon_check_handoff"](json.dumps([FROM_A_DRAWING]), waived=incomplete)
    )

    assert result["ok"] is False
    assert "granted_by" in result["error"]["message"]
    assert "reason" in result["error"]["message"]


def test_a_stability_check_separates_blocking_from_qualifying() -> None:
    import random

    rng = random.Random(3)
    settled = [0.12 + rng.gauss(0, 0.02) for _ in range(300)]

    result = json.loads(
        _tools()["etalon_check_stability"](
            json.dumps(settled),
            nanoseconds=1000,
            replicas=1,
            contacts_json=json.dumps({"ASP118": 0.9}),
            scored_contacts="ASP118",
        )
    )

    assert result["blocks_the_result"] == []
    assert "F_SINGLE_REPLICA_ESTIMATE" in str(result["observations"])
    assert result["measurements"]["settled"] is True
    assert "cannot show that one does" in result["what_one_run_establishes"]


def test_admissibility_reports_the_selection_effect() -> None:
    measurements = [
        {
            "parent_id": "a",
            "cheap_value": -8.0,
            "expensive_value": -30.0,
            "observations": [{"code": "F_COORDINATES_ARE_A_DEPICTION", "fired": False, "detail": "pose"}],
        },
        {
            "parent_id": "b",
            "cheap_value": -7.0,
            "expensive_value": -29.0,
            "observations": [{"code": "F_COORDINATES_ARE_A_DEPICTION", "fired": True, "detail": "drawing"}],
        },
        {
            "parent_id": "c",
            "cheap_value": -6.0,
            "expensive_value": -28.0,
            "observations": [{"code": "F_HYDROGENS_IMPLICIT", "fired": True, "detail": "0 H"}],
        },
    ]

    result = json.loads(_tools()["etalon_rule_admissible"](json.dumps(measurements)))

    assert result["report"]["admitted"] == 1
    assert result["report"]["admission_rate"] < 0.5
    assert "selection rather than a sample" in result["next_step"]


def test_the_fep_tool_names_what_it_cannot_reach() -> None:
    molecules = {
        "indolinone": "O=C1Nc2ccccc2C1=Cc1ccc[nH]1",
        "thienopyrimidine": "c1ccc(-c2cc3ncncc3s2)cc1",
    }

    result = json.loads(
        _tools()["etalon_design_fep_network"](json.dumps(molecules), references="indolinone")
    )

    assert set(result["network"]["unreachable"]) == set(molecules)
    assert "no usable edge" in result["next_step"]
    assert "docs/adr/0005" in result["next_step"]


# -- the planning tools --------------------------------------------------


def test_a_plan_names_its_unmeasured_inputs_in_the_next_step() -> None:
    result = json.loads(_tools()["etalon_plan_campaign"](pool=750_000, budget_gpu_hours=8760.0))

    assert result["ok"] is True
    assert result["plan"]["unmeasured_inputs"]
    assert "docking" in result["next_step"]


def test_an_unknown_stage_override_is_refused_with_the_catalogue() -> None:
    result = json.loads(
        _tools()["etalon_plan_campaign"](pool=1000, budget_gpu_hours=100.0, measured="invented=0.5")
    )

    assert result["ok"] is False
    assert "etalon_stages" in result["error"]["message"]


def test_tuning_refuses_a_conditional_knob_until_its_precondition_is_recorded() -> None:
    locked = json.loads(_tools()["etalon_tune_screen"]())
    unlocked = json.loads(_tools()["etalon_tune_screen"](established="rescore_ml,consensus_three"))

    assert locked["advice"]["worth_turning"] == []
    assert "Establish a precondition" in locked["next_step"]
    assert len(unlocked["advice"]["worth_turning"]) == 2


# -- the workflow document ----------------------------------------------


def test_the_skill_is_one_file_serving_both_consumers() -> None:
    """A second copy would drift, and the numbers are the part that must not."""

    from etalon.mcp.server import _skill_path

    path = _skill_path()
    text = path.read_text(encoding="utf-8")

    assert text.startswith("---"), "a Claude Code skill needs frontmatter"
    assert "name: etalon-campaign" in text
    assert SKILL_RESOURCE == "etalon://skills/campaign"
    # The measurements a refusal rests on have to be in the document, because a refusal quoted
    # without its measurement looks like fussiness.
    for number in ("41 of 41", "12 kcal/mol", "110 of 120", "0.767", "37.5%"):
        assert number in text, number


def test_every_workflow_document_resolves_and_is_a_skill() -> None:
    """Six documents, one per regime or decision, each serving a skill and a resource."""

    from etalon.mcp.server import WORKFLOWS, _skill_path

    for slug, name in WORKFLOWS.items():
        text = _skill_path(slug).read_text(encoding="utf-8")
        assert text.startswith("---"), f"{slug} needs frontmatter"
        assert f"name: {name}" in text, slug
        # A description is what a model routes on; an undescribed skill is one nobody reaches.
        assert "description:" in text.split("---")[1], slug


def test_an_unknown_workflow_slug_is_refused() -> None:
    from etalon.mcp.server import _skill_path

    with pytest.raises(KeyError, match="no such workflow"):
        _skill_path("no-such-regime")


def test_the_campaign_document_routes_to_the_other_regime() -> None:
    """A screening-only campaign takes none of the paths the campaign document guards.

    Measured: one 1,056,280-molecule campaign ran to completion calling no handoff or spend tool. If
    the campaign document does not say where such a campaign should go, nothing does.
    """

    from etalon.mcp.server import _skill_path

    text = _skill_path("campaign").read_text(encoding="utf-8")
    assert "etalon-screening-sweep" in text
    assert "etalon_authorize_gates" in text


def test_the_sweep_document_carries_the_panel_that_justifies_its_guard() -> None:
    """The gate check is the sweep's one rule, and its argument is a table of measurements."""

    from etalon.mcp.server import _skill_path

    text = _skill_path("sweep").read_text(encoding="utf-8")
    for number in (
        "-8.5",  # the shipped threshold, as a literal config value: ASCII, inside inline code
        "−5.90",  # a measured score, as a numeral in prose: U+2212
        "−7.78",
        "570 nM",  # the 570 nM binder the default gate rejects
        "7,545",  # scores an exhausted batch still committed
        "0.028%",  # end-to-end survival, which is why a short batch yields nothing
    ):
        assert number in text, number


# --------------------------------------------------------------------------------------
# The tools added after ADR 0006's class of bug was traced past its instance: a gate whose
# output the next step requires, and a council that must qualify before it counts.
# --------------------------------------------------------------------------------------


def test_authorize_issues_a_token_only_for_a_record_that_survives_the_check() -> None:
    result = json.loads(
        _tools()["etalon_authorize_spend"](
            records_json=json.dumps([FROM_A_DRAWING, CLEAN])
        )
    )
    assert result["ok"]
    assert result["authorized_count"] == 1
    assert FROM_A_DRAWING["parent_id"] in result["refused"]
    token = result["authorized"][CLEAN["parent_id"]]
    assert len(token["record_sha256"]) == 64
    assert len(token["signature"]) == 64


def test_a_token_names_what_could_not_be_checked_rather_than_implying_it_passed() -> None:
    result = json.loads(
        _tools()["etalon_authorize_spend"](records_json=json.dumps([CLEAN]))
    )
    token = result["authorized"][CLEAN["parent_id"]]
    assert token["unchecked"], "a token over unevaluable checks must say which"
    assert "does not assert the check passed" in result["next_step"]


def test_the_council_is_refused_when_a_seat_is_at_chance() -> None:
    votes = {
        "careful": ["refuse"] * 9 + ["clear"] + ["clear"] * 9 + ["refuse"],
        "refuses-everything": ["refuse"] * 20,
    }
    result = json.loads(
        _tools()["etalon_council_reliability"](
            votes_json=json.dumps(votes),
            truth_json=json.dumps([True] * 10 + [False] * 10),
            labels="20 hand-labelled handoff rows",
        )
    )
    assert result["ok"]
    assert result["reliability"]["qualified"] is False
    assert any("refuses-everything" in r for r in result["reliability"]["refusals"])
    assert "may not sit" in result["next_step"]


def test_the_council_reports_how_many_opinions_it_actually_carries() -> None:
    twin = ["refuse"] * 11 + ["clear"] + ["clear"] * 11 + ["refuse"]
    result = json.loads(
        _tools()["etalon_council_reliability"](
            votes_json=json.dumps({"one": twin, "two": list(twin)}),
            truth_json=json.dumps([True] * 12 + [False] * 12),
            labels="24 rows",
        )
    )
    assert result["reliability"]["effective_votes"] < 2.0
    assert result["reliability"]["seats_seated"] == 2


def test_a_vote_that_is_not_a_vote_is_refused_rather_than_guessed() -> None:
    result = json.loads(
        _tools()["etalon_council_reliability"](
            votes_json=json.dumps({"a": ["probably"], "b": ["clear"]}),
            truth_json=json.dumps([True]),
            labels="1 row",
        )
    )
    assert result["ok"] is False
    assert "abstain" in result["error"]["message"]


def test_agreement_that_nothing_is_wrong_never_makes_a_check_evaluable() -> None:
    """The one result that would let a campaign buy a clean record from a model."""

    result = json.loads(
        _tools()["etalon_council_adjudicate"](
            code="F_RECEPTOR_NOT_THE_ONE_SCORED",
            ballots_json=json.dumps(
                [
                    {"seat": "a", "vote": "clear", "reason": "looks right"},
                    {"seat": "b", "vote": "clear", "reason": "no concern"},
                ]
            ),
            reliability_qualified=True,
        )
    )
    assert result["finding"]["outcome"] == "cleared_but_still_unchecked"
    assert result["observation"]["evaluable"] is False
    assert result["observation"]["fired"] is False


def test_a_unanimous_refusal_is_the_one_direction_a_council_may_move_a_check() -> None:
    result = json.loads(
        _tools()["etalon_council_adjudicate"](
            code="F_RECEPTOR_NOT_THE_ONE_SCORED",
            ballots_json=json.dumps(
                [
                    {"seat": "a", "vote": "refuse", "reason": "receptor id is absent"},
                    {"seat": "b", "vote": "abstain", "reason": "not my evidence"},
                ]
            ),
            reliability_qualified=True,
        )
    )
    assert result["finding"]["outcome"] == "refused"
    assert result["observation"]["evaluable"] is True
    assert result["observation"]["fired"] is True


def test_a_split_waits_for_a_person_and_is_not_recorded_as_a_refusal() -> None:
    result = json.loads(
        _tools()["etalon_council_adjudicate"](
            code="F_RECEPTOR_NOT_THE_ONE_SCORED",
            ballots_json=json.dumps(
                [
                    {"seat": "a", "vote": "refuse", "reason": "the digest is absent"},
                    {"seat": "b", "vote": "clear", "reason": "the run named one"},
                ]
            ),
            reliability_qualified=True,
            parent_id="M1",
        )
    )
    assert result["finding"]["outcome"] == "split"
    assert result["observation"]["fired"] is False
    assert [b["reason"] for b in result["finding"]["dissent"]]
    assert "person must rule" in result["next_step"]


def test_an_unqualified_council_aggregates_nothing() -> None:
    result = json.loads(
        _tools()["etalon_council_adjudicate"](
            code="F_RECEPTOR_NOT_THE_ONE_SCORED",
            ballots_json=json.dumps(
                [
                    {"seat": "a", "vote": "refuse", "reason": "x"},
                    {"seat": "b", "vote": "refuse", "reason": "y"},
                ]
            ),
        )
    )
    assert result["finding"]["outcome"] == "council_not_qualified"
    assert result["observation"]["evaluable"] is False


def test_one_ballot_is_not_a_council() -> None:
    result = json.loads(
        _tools()["etalon_council_adjudicate"](
            code="F_RECEPTOR_NOT_THE_ONE_SCORED",
            ballots_json=json.dumps([{"seat": "a", "vote": "refuse"}]),
            reliability_qualified=True,
        )
    )
    assert result["ok"] is False
    assert "quorum's weight behind one opinion" in result["error"]["message"]


def test_two_ballots_from_one_seat_are_refused() -> None:
    result = json.loads(
        _tools()["etalon_council_adjudicate"](
            code="F_RECEPTOR_NOT_THE_ONE_SCORED",
            ballots_json=json.dumps(
                [{"seat": "a", "vote": "refuse"}, {"seat": "a", "vote": "clear"}]
            ),
            reliability_qualified=True,
        )
    )
    assert result["ok"] is False
    assert "one seat voting twice" in result["error"]["message"]


def test_a_code_outside_the_taxonomy_is_refused_by_the_council_tool() -> None:
    result = json.loads(
        _tools()["etalon_council_adjudicate"](
            code="F_INVENTED",
            ballots_json=json.dumps(
                [{"seat": "a", "vote": "refuse"}, {"seat": "b", "vote": "refuse"}]
            ),
            reliability_qualified=True,
        )
    )
    assert result["ok"] is False
    assert "not in the fault taxonomy" in result["error"]["message"]


def test_every_workflow_document_is_carried_into_the_wheel() -> None:
    """An installed package has no .claude directory, so the wheel has to carry these.

    Only `campaign` was ever force-included. A wheel-installed server therefore raised
    FileNotFoundError for the other five -- including `sweep`, the document a screening-only
    campaign is routed to and the one carrying the panel that justifies its gate. Verified by
    building the wheel: one file under mcp/skills where WORKFLOWS names six.

    The table cannot be globbed -- the destination is the slug and the source is the directory, and
    `sweep` comes from `etalon-screening-sweep` -- so it is written out, and written-out tables
    drift. This is the check that stops the next one being forgotten rather than the fix for the
    five that were.
    """

    import tomllib

    from etalon.mcp.server import WORKFLOWS, _skill_path

    root = Path(__file__).resolve().parent.parent
    with (root / "pyproject.toml").open("rb") as handle:
        config = tomllib.load(handle)
    included = config["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]

    for slug, name in WORKFLOWS.items():
        source = f".claude/skills/{name}/SKILL.md"
        assert source in included, (
            f"workflow {slug!r} is served by the server but {source} is not force-included, so a "
            "wheel-installed client cannot read it"
        )
        assert included[source] == f"etalon/mcp/skills/{slug}.md", (
            f"{slug!r} must land at the slug _skill_path falls back to, not at {included[source]!r}"
        )
        # The source has to exist, or the wheel ships a mapping to nothing.
        assert (root / source).is_file(), f"{source} is force-included but absent"
        assert _skill_path(slug).is_file(), slug


def _sweep_tools() -> dict[str, object]:
    from etalon.mcp import sweep

    collector = Collector()
    sweep.register(collector)
    return collector.tools


def test_a_drained_pool_is_a_phase_not_a_generation_shortage() -> None:
    """With generation stopped the pool is fixed, and the only question is how long to screen it.

    The concurrent model answered that question with null, because ``min(0, screening)`` is zero,
    and advised moving cards back to generation. Measured on ALK2: generation was deliberately
    retired with the pool fully carved into batches, and the plan declined to say how long the
    remaining batches would take while recommending the campaign undo the decision it had just made.
    """

    plan = json.loads(
        _sweep_tools()["etalon_campaign_plan"](
            pool_size=220_000,
            batch_size=20_000,
            screen_devices=8,
            generation_devices=0,
            batches_per_device=2,
            minutes_per_batch=261.0,
        )
    )
    assert plan["ok"] is True
    assert plan["binding_constraint"] == "screening"
    assert plan["capacity_ratio"] is None
    # 8 cards x 2 batches x 20,000 / 261 min = 73,563 an hour; 220,000 of them is three hours.
    assert plan["unique_molecules_per_hour"]["screening"] == 73_563
    assert plan["hours_to_screen_pool"] == 3.0
    assert any("pool is fixed" in line for line in plan["advice"])
    assert not any("Move" in line for line in plan["advice"])


def test_screening_capacity_counts_batches_not_cards() -> None:
    """``batches_per_device`` doubles throughput without asking for cards that do not exist."""

    tools = _sweep_tools()
    one = json.loads(
        tools["etalon_campaign_plan"](pool_size=0, screen_devices=8, generation_devices=0)
    )
    two = json.loads(
        tools["etalon_campaign_plan"](
            pool_size=0, screen_devices=8, generation_devices=0, batches_per_device=2
        )
    )
    # Within one, because the payload rounds and round(2x) is not always 2 * round(x).
    assert (
        abs(
            two["unique_molecules_per_hour"]["screening"]
            - 2 * one["unique_molecules_per_hour"]["screening"]
        )
        <= 1
    )
    assert two["batches_per_device"] == 2


# -- a refusal is not a crash, and the boundary said it was ------------------
#
# Two governance refusals reached a model wearing the wrong clothes, in opposite directions, both
# because the boundary classified by Python's exception hierarchy rather than by meaning.
#
# `NotAuthorized` subclasses `PermissionError` subclasses `OSError`, which is `_TRANSIENT`. So the
# one refusal that stands between a calibration and a campaign's worth of GPU time was reported as
# "it looks transient, a filesystem or device error, calling again is reasonable once." None of its
# three causes -- no gate, expired or unverifiable signature, wrong funnel revision -- is fixed by
# calling again.
#
# `SweepError` is a `RuntimeError` and so not transient, and every deliberate sweep refusal was
# reported as "this is a defect in ETALON ... Report it with this message rather than retrying."
# The remedy was in the message, and the hint told the reader to disregard it.


def test_a_missing_gate_is_a_refusal_not_a_transient_filesystem_error(tmp_path) -> None:
    (tmp_path / "ws").mkdir()
    response = json.loads(_tools()["etalon_sweep_emit"](
        str(tmp_path / "ws"), str(tmp_path / "p.sqlite"), str(tmp_path / "l.jsonl"),
        "rev-1", "",
    ))

    assert response["ok"] is False
    error = response["error"]
    assert error["code"] == "NotAuthorized"
    assert error["retryable"] is False, "a missing gate was reported as worth calling again"
    # `etalon_sweep_emit` catches this itself, so the hint is the emit-specific one rather than
    # the boundary's generic refusal text. Both say the same thing about retrying.
    assert "Compare revision_id against the gate" in error["hint"]
    assert "etalon_authorize_gates" in error["hint"]
    assert "ETALON_AUTHORITY_KEY" in error["hint"]
    # The token's own numbers, so the caller can tell which of the three causes fired.
    assert error["context"]["requested_revision_id"] == "rev-1"
    assert error["context"]["gate_revision_id"] is None


def test_a_deliberate_sweep_refusal_is_not_reported_as_an_etalon_defect(tmp_path, caplog) -> None:
    """Claiming a batch that does not exist is a `SweepError` with its remedy in the message.

    It used to arrive as *"this is a defect in ETALON or in an input it did not validate. Report
    it with this message rather than retrying"* -- and the message it told the reader to report
    rather than read was the one naming what to do. It was also filed by `logger.exception`, so a
    governance decision was recorded with a traceback, which is what an operator greps for when
    something broke.
    """

    workspace, pool, ledger = tmp_path / "ws", tmp_path / "p.sqlite", tmp_path / "l.jsonl"
    workspace.mkdir()

    admitted = json.loads(_tools()["etalon_sweep_admit"](
        str(workspace), str(pool), str(ledger),
        json.dumps([{"key": f"K{i}", "smiles": f"C{i}", "source": "s"} for i in range(3)]),
    ))
    assert admitted["ok"] is True, admitted

    refusal = json.loads(_tools()["etalon_sweep_claim"](
        str(workspace), str(pool), str(ledger), "batch_0001", "cuda:0", str(tmp_path / "libs"), "",
    ))
    assert refusal["ok"] is False
    error = refusal["error"]
    assert error["code"] == "SweepError"
    assert error["retryable"] is False
    assert "deliberately, not a defect" in error["hint"]
    assert "defect in ETALON" not in error["hint"]
    # And the message's own remedy is one an MCP caller can act on.
    assert "etalon_sweep_status" in error["message"]

    # Filed as a decision, not as a crash. `logger.exception` writes a traceback, which is what an
    # operator greps for when something broke; before the fix a batch that was simply already
    # claimed produced one.
    records = [r for r in caplog.records if r.name.startswith("etalon.mcp")]
    assert records, "the refusal was not logged at all"
    assert all(r.levelname != "ERROR" for r in records), [r.getMessage() for r in records]
    assert any("refused" in r.getMessage() for r in records)
    assert not any(r.exc_info for r in records), "a refusal was logged with a traceback"


def test_the_boundary_tells_a_refusal_from_a_surprise() -> None:
    """The classifier itself, on the two types and on a genuine surprise."""

    from etalon.authority.grant import NotAuthorized
    from etalon.campaign.sweep import SweepError
    from etalon.mcp._common import _refusal

    gate = _refusal(NotAuthorized("no gate authorization for revision abc"))
    assert gate is not None
    assert "not a transient failure" in gate
    assert "etalon_authorize_gates" in gate
    assert "ETALON_AUTHORITY_KEY" in gate

    swept = _refusal(SweepError("batch_0001 was never emitted"))
    assert swept is not None
    assert "deliberately, not a defect" in swept

    # NotAuthorized is an OSError, which is exactly why it needed naming.
    assert isinstance(NotAuthorized("x"), OSError)
    assert _refusal(OSError("disk went away")) is None
    assert _refusal(RuntimeError("a real surprise")) is None
    assert _refusal(AttributeError("'NoneType' has no attribute 'x'")) is None


@pytest.fixture()
def keyed_gate(tmp_path, monkeypatch):
    """A workspace, pool and ledger with molecules admitted and a real gate token minted.

    The signing key is fixed so a token minted here verifies here: without it
    ``_campaign_secret`` is per-process random and the token is unverifiable the moment it is
    handed back through ``gate_json``.
    """

    from etalon.authority.gate import authorize_gate
    from etalon.campaign.calibrate import Calibration, PanelMember, TierVerdict, separation

    monkeypatch.setenv("ETALON_AUTHORITY_KEY", "test-key")
    workspace, pool, ledger = tmp_path / "ws", tmp_path / "p.sqlite", tmp_path / "l.jsonl"
    workspace.mkdir()

    admitted = json.loads(_tools()["etalon_sweep_admit"](
        str(workspace), str(pool), str(ledger),
        json.dumps([{"key": f"K{i}", "smiles": f"C{i}", "source": "s"} for i in range(5)]),
    ))
    assert admitted["ok"] is True, admitted

    panel = (
        PanelMember("act_1", True, "co-crystal"),
        PanelMember("neg_1", False, "negative control"),
    )
    scores = (
        {"parent_id": "act_1", "engine_id": "unidock", "score": -11.0,
         "score_kind": "VINA_KCAL_MOL", "direction": "LOWER_STRONGER"},
        {"parent_id": "neg_1", "engine_id": "unidock", "score": -6.0,
         "score_kind": "VINA_KCAL_MOL", "direction": "LOWER_STRONGER"},
    )
    verdict = Calibration(
        revision_id="rev-1", run_id="panel", outcome="committed",
        panel_size=2, actives=1, registered=2,
        tiers=(TierVerdict("t9", "docking", 2, 2, (), ("neg_1",)),),
        finalize=(), separation=separation(scores, panel),
    )
    token = authorize_gate(verdict, provenance={"name": "molcascade", "tree_sha256": "d", "pinned": True})
    return workspace, pool, ledger, token.as_dict()


def test_a_claim_with_no_library_reserves_nothing(tmp_path, keyed_gate) -> None:
    """Only ambiguity was checked, so passing neither library argument succeeded and wrote nothing.

    The reservation is irrevocable once taken: `Sweep.claim` refuses a second claim on the same
    batch. So this returned `ok(batch=..., library=None)` for a batch now committed to a screener
    with nothing to screen, with no pointer to `etalon_sweep_recover` -- which is the exact state
    the tool's own LibraryNotWritten block exists to warn about, reached by the one path that does
    not go through it. Refused before the claim now, because a presence check needs nothing from
    the pool.
    """

    workspace, pool, ledger, gate = keyed_gate
    # flush, because five molecules is under any batch size: a short batch is the end-of-campaign
    # case and the only way to carve one deliberately.
    emitted = json.loads(_tools()["etalon_sweep_emit"](
        str(workspace), str(pool), str(ledger), "rev-1", json.dumps(gate), True,
    ))
    assert emitted["ok"] is True, emitted
    batch_id = emitted["emitted"][0]["batch_id"]

    refusal = json.loads(_tools()["etalon_sweep_claim"](
        str(workspace), str(pool), str(ledger), batch_id, "cuda:0", "", "",
    ))
    assert refusal["ok"] is False
    error = refusal["error"]
    assert error["code"] == "NoLibraryRequested"
    assert error["retryable"] is False
    assert "library_dir" in error["hint"]
    assert "Nothing has been reserved" in error["hint"]

    # The proof that nothing was reserved: the same claim succeeds once given a directory.
    claimed = json.loads(_tools()["etalon_sweep_claim"](
        str(workspace), str(pool), str(ledger), batch_id, "cuda:0", str(tmp_path / "libs"), "",
    ))
    assert claimed["ok"] is True, claimed
    assert claimed["library"], "a claim that was refused for no library had taken the batch anyway"
