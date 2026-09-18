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
    from etalon.mcp import active, execution, governance, planning

    collector = Collector()
    planning.register(collector)
    governance.register(collector)
    active.register(collector)
    execution.register(collector)
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
