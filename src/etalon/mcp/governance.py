"""The tools that refuse things, which is what makes an autonomous campaign safe to run.

A model driving a campaign cannot be asked to confirm every action -- there are 750,000 of them -- so
the governance is not per-action confirmation. It is that the expensive actions are guarded by cheap
checks the model is expected to call, and that the guards refuse rather than warn.

Each tool here answers one question and costs nothing:

``etalon_check_handoff``     would a simulation of this molecule be about this molecule?
``etalon_check_stability``   what did the trajectory establish, and what did it not?
``etalon_rule_admissible``   may this measurement update the screen?
``etalon_design_fep_network`` which of these molecules can a relative method relate at all?
``etalon_recommend_waiver``  -- and this one cannot grant, whatever the caller asks.

The last is the only tool in the server a model may not complete. A waiver lets a campaign spend past
a fault that would otherwise refuse a molecule, and its entire value is that a named person accepted
a named consequence and can be asked about it later. A model writes that justification better than
most people and cannot be held to it, so the tool records a recommendation and says who must grant it.
``docs/adr/0003`` carries the reasoning and the measurements behind it.
"""

from __future__ import annotations

import json
from typing import Any

from etalon.mcp._common import Cost, absolute_path, ok, tool

#: What the ``waived`` argument must carry, quoted in the refusal so the remedy is in the error
#: rather than only in the documentation.
_WAIVER_SHAPE = '[{"code": ..., "reason": ..., "granted_by": ..., "expires": "YYYY-MM-DD"}]'


def _waivers(waived: str) -> Any:
    """Parse the ``waived`` argument into a :class:`~etalon.judgment.waiver.WaiverSet`.

    This function exists because of a hole it closes. Both governance tools used to take ``waived``
    as a comma-separated list of fault codes and turn it straight into a set that
    :func:`~etalon.faults.preflight.blocking` would honour. No waiver was constructed, so none of the
    guards in ``judgment.waiver`` ran: no grantor, no expiry, no check that the code exists, and no
    check that the code is one a waiver can release. A model could release
    ``F_COORDINATES_ARE_A_DEPICTION`` -- which the tool's own ``next_step`` calls unfixable by a
    waiver -- by typing its name.

    That is precisely the "disabled check wearing a decision's clothes" the waiver module was written
    to prevent, and the prevention lived in a module the MCP path did not call. So the MCP path calls
    it now: every entry is constructed through :class:`Waiver`, which refuses an unknown code, a
    reason under 24 characters, an unnamed grantor, a grantor that looks like a model, and a fault
    whose consequence only qualifies a claim. Expiry is honoured too -- ``codes()`` filters on the
    date, which the raw frozenset never did.

    What this still cannot do is verify that a named person exists or agreed. A model that writes a
    plausible human name into ``granted_by`` produces a waiver this function accepts. That limit is
    real and is the reason the tools echo every waiver back in their result: the defence against a
    fabricated grantor is that the operator reads the name, not that the parser catches it.

    Raises:
        ValueError: On a bare code list, which is the old shape and the hole.
    """

    from etalon.judgment.waiver import Waiver, WaiverSet

    text = waived.strip()
    if not text:
        return WaiverSet(())
    if not text.startswith("["):
        raise ValueError(
            f"waived must be a JSON array of granted waivers, {_WAIVER_SHAPE}, and got {text!r}. A "
            "bare fault code releases a check without recording who accepted the consequence, which "
            "is the one thing a waiver is for. Call etalon_recommend_waiver, show the result to the "
            "operator, and pass back what they granted under their own name. Do not fill granted_by "
            "with your own -- see docs/adr/0003."
        )
    payload = json.loads(text)
    if not isinstance(payload, list):
        raise ValueError(f"waived must be a JSON array of granted waivers, {_WAIVER_SHAPE}")
    entries = []
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError(f"each waived entry must be an object, {_WAIVER_SHAPE}; got {item!r}")
        missing = sorted({"code", "reason", "granted_by", "expires"} - set(item))
        if missing:
            raise ValueError(
                f"the waiver for {item.get('code', '?')!r} is missing {', '.join(missing)}. Every "
                "field is load-bearing: the reason is what a reader has a year from now, the "
                "grantor is who can be asked, and the expiry is what stops a waiver becoming the "
                "configuration."
            )
        entries.append(Waiver.from_dict(item))
    return WaiverSet(tuple(entries))


def register(mcp: Any) -> None:
    @mcp.tool()
    @tool(Cost.FREE)
    def etalon_check_handoff(
        records_json: str,
        receptor_path: str = "",
        toolchain_seeded: bool = False,
        waived: str = "",
    ) -> str:
        """FREE. Rule on handoff records before a GPU-second is spent on them.

        Each record is one ``md_system_input/v1`` row: where the coordinates came from, whether there
        are hydrogens, the formal charge, the stereochemistry, the receptor, the protonation state.
        This is the cheapest refusal in the pipeline and the most valuable: measured here,
        MolCascade's shortlist exporter rebuilds geometry from SMILES -- 19 heavy atoms, zero explicit
        hydrogens, every z exactly 0.00 -- PRISM's ligand validator accepts it, and across 41 gaff2
        builds from such input the topology carried zero hydrogens in 41 of 41 with no warning.

        Returns per molecule: what blocks the spend, what merely qualifies a claim about the run, and
        what could not be checked. A check that could not run is reported as unevaluable rather than
        as clean, because "0 blocking" printed over four unevaluated checks says the opposite of the
        truth.

        Args:
            records_json: A JSON array of ``md_system_input/v1`` rows.
            receptor_path: Absolute path to the receptor the build will use. Supplying it turns the
                receptor-identity check from unevaluable into a digest comparison, which is the only
                form in which that fault can be ruled out.
            toolchain_seeded: Whether ETALON's gmx shim will be on the path. Without it ion placement
                draws from the clock and no re-run reproduces the result.
            waived: JSON array of waivers a person has granted, each
                ``{code, reason, granted_by, expires}``. Not a list of codes: a bare code releases a
                check without recording who accepted the consequence. Call etalon_recommend_waiver,
                show it to the operator, and pass back what they granted under their own name.
        """

        from etalon.faults.preflight import (
            blocking,
            check_record,
            unchecked,
            unverifiable,
            waived_blocking,
        )

        rows = json.loads(records_json)
        if not isinstance(rows, list):
            raise ValueError("records_json must be a JSON array of md_system_input/v1 rows")
        waivers = _waivers(waived)
        # ``codes()`` applies the expiry date; the frozenset this replaced did not, so an expired
        # waiver went on releasing its fault for as long as the string was passed.
        released = waivers.codes()
        expired = waivers.expired()
        receptor = absolute_path(receptor_path, label="receptor_path") if receptor_path else None

        verdicts = []
        spendable = []
        for row in rows:
            seen = check_record(row, receptor_path=receptor, toolchain_active=toolchain_seeded)
            blocked = [entry.code for entry in blocking(seen, waived=released)]
            accepted = [entry.code for entry in waived_blocking(seen, released)]
            identifier = str(row.get("parent_id", "?"))
            if not blocked:
                spendable.append(identifier)
            verdicts.append(
                {
                    "parent_id": identifier,
                    "may_spend": not blocked,
                    "blocking": blocked,
                    "proceeding_under_waiver": accepted,
                    "qualifies_the_claim_only": [e.code for e in unverifiable(seen)],
                    "could_not_be_checked": [e.code for e in unchecked(seen)],
                    "detail": {entry.code: entry.detail for entry in seen if entry.fired},
                }
            )
        return ok(
            molecules=len(rows),
            may_spend=len(spendable),
            spendable_parent_ids=spendable,
            verdicts=verdicts,
            # Echoed in full, and not only as codes. A parser can refuse a grantor that looks like a
            # model; it cannot refuse a plausible human name a model invented. The defence against
            # that is an operator reading who accepted what, so the result has to carry it.
            waivers_in_force=[waiver.as_dict() for waiver in waivers.active()],
            waivers_expired=[waiver.as_dict() for waiver in expired],
            next_step=(
                f"{len(rows) - len(spendable)} record(s) are refused. Read the detail: a record built "
                "from a drawing or with no explicit hydrogens is not fixable by a waiver -- fix the "
                "producer. Only an undecided protonation state is a reasonable thing to accept, and "
                "that needs a person via etalon_recommend_waiver."
                if len(spendable) < len(rows)
                else "All records may proceed. Check could_not_be_checked before treating that as "
                "clean."
            )
            + (
                f" {len(expired)} waiver(s) have expired and are no longer releasing anything; "
                "their faults block again."
                if expired
                else ""
            ),
        )

    @mcp.tool()
    @tool(Cost.FREE)
    def etalon_check_stability(
        ligand_rmsd_nm: str,
        nanoseconds: float = 0.0,
        replicas: int = 1,
        contacts_json: str = "",
        scored_contacts: str = "",
    ) -> str:
        """FREE. Read a finished trajectory as the three questions 'is the pose stable' hides.

        **Did the ligand stay?** If not, every number from the run describes a solvated ligand near a
        protein. **Did it settle?** A pose still moving was averaged over a non-stationary segment.
        **Is it the pose that was scored?** A ligand can hold the site and lose every contact the
        docking score was about, which does not make the free energy wrong but makes comparing it
        with the screen a comparison between two poses.

        The first two block. The third withholds the molecule from teaching the screen without
        withholding the molecule.

        One run establishes less than it appears to. Replicas differing only in initial velocities
        have disagreed by up to 15 kcal/mol on one system, so a run that loses the pose has not shown
        the pose is wrong and a run that holds it has not shown it is right.

        Args:
            ligand_rmsd_nm: JSON array of per-frame ligand RMSD in nm. The series, not a summary: a
                mean cannot distinguish a settled pose from one that left the site and returned.
            replicas: How many independent runs this is one of.
            contacts_json: JSON object of residue to the fraction of frames it contacted the ligand.
            scored_contacts: Comma-separated residues the docked pose was scored for.
        """

        from etalon.faults.preflight import blocking, unchecked, unverifiable
        from etalon.faults.stability import (
            Trajectory,
            check_stability,
            necessary_not_sufficient,
        )

        series = json.loads(ligand_rmsd_nm)
        trajectory = Trajectory(
            ligand_rmsd_nm=tuple(float(value) for value in series),
            contacts=json.loads(contacts_json) if contacts_json else {},
            scored_contacts=tuple(s.strip() for s in scored_contacts.split(",") if s.strip()),
            replicas=replicas,
            nanoseconds=nanoseconds,
        )
        observed = check_stability(trajectory)
        return ok(
            blocks_the_result=[entry.code for entry in blocking(observed)],
            qualifies_the_claim_only=[entry.code for entry in unverifiable(observed)],
            could_not_be_checked=[entry.code for entry in unchecked(observed)],
            observations=[
                {"code": entry.code, "fired": entry.fired, "evaluable": entry.evaluable, "detail": entry.detail}
                for entry in observed
            ],
            measurements={
                "peak_rmsd_nm": round(max(trajectory.ligand_rmsd_nm), 4),
                "trend_over_final_third_nm": round(trajectory.drift(), 4),
                "fluctuation_nm": round(trajectory.fluctuation(), 4),
                "settled": trajectory.settled(),
                "scored_contacts_retained": trajectory.retained_fraction(),
            },
            what_one_run_establishes=necessary_not_sufficient(),
        )

    @mcp.tool()
    @tool(Cost.FREE)
    def etalon_rule_admissible(measurements_json: str, waived: str = "") -> str:
        """FREE. Decide which measurements may update the screen, and which may not.

        This is the gate the published CADD loops do not have. They put machine learning inside the
        campaign -- which is old and well done -- on the assumption that a number arriving from the
        expensive stage is a measurement of the molecule it is filed under. Measured here, that is
        false: across 41 gaff2 builds from hydrogen-free input the topology carried zero hydrogens in
        41 of 41. A loop without this gate does not record one bad number; it fits the thresholds
        applied to every later molecule to a label from a molecule that was never simulated.

        The asymmetry is the whole argument. Withholding a good measurement costs one molecule's
        information. Admitting a bad one costs a shift in the policy applied to all of them.

        Read ``admission_rate`` before acting on the result. A loop learning only from measurements it
        could verify is learning from a selected subpopulation, and on a congeneric series those are
        systematically the molecules whose geometry was easy -- which are not the ones a screen is
        getting wrong.

        Args:
            measurements_json: JSON array of ``{parent_id, cheap_value, expensive_value,
                observations: [{code, fired, evaluable, detail}]}``.
            waived: JSON array of waivers a person has granted, each
                ``{code, reason, granted_by, expires}`` -- the same shape etalon_check_handoff takes.
                A waiver here admits a measurement whose cause would otherwise withhold it from
                teaching the screen, which is a larger decision than letting one molecule be
                simulated: the label it releases is fitted against every later molecule.
        """

        from etalon.faults.attribution import Observation
        from etalon.learn.admissible import Measurement, admissible

        entries = json.loads(measurements_json)
        measurements = [
            Measurement(
                parent_id=str(entry["parent_id"]),
                cheap_value=entry.get("cheap_value"),
                expensive_value=entry.get("expensive_value"),
                observations=tuple(
                    Observation(
                        code=str(item["code"]),
                        fired=bool(item.get("fired")),
                        detail=str(item.get("detail", "")),
                        evaluable=bool(item.get("evaluable", True)),
                    )
                    for item in entry.get("observations", ())
                ),
            )
            for entry in entries
        ]
        waivers = _waivers(waived)
        report = admissible(measurements, waivers)
        return ok(
            report=report.as_dict(),
            waivers_in_force=[waiver.as_dict() for waiver in waivers.active()],
            waivers_expired=[waiver.as_dict() for waiver in waivers.expired()],
            next_step=(
                "Do not fit anything on this round. The admission rate is low enough that what "
                "survived is a selection rather than a sample."
                if report.rate < 2 / 3
                else "These measurements may update the screen. Any change they support must still "
                "beat the panel's resolution -- see etalon_tune_screen for what that is."
            ),
        )

    @mcp.tool()
    @tool(Cost.CHEAP)
    def etalon_design_fep_network(
        molecules_json: str,
        references: str = "",
        min_core_fraction: float = 0.5,
        cycle_edges: int = 0,
    ) -> str:
        """CHEAP. Design the edge network a relative free energy calculation needs.

        Relative FEP scores differences, so a set of molecules is not an input to it. Call this
        before committing any GPU time to FEP: it takes about 34 ms per pair and it will usually tell
        you something uncomfortable.

        Measured on the 16 most potent molecules of a real kinase panel: 110 of 120 pairs map through
        a common core smaller than half the larger molecule, and 7 of the 16 have no usable edge to
        anything. Those sixteen were not a series -- they were sixteen chemotypes that bind the same
        kinase, which is what a funnel selecting for diversity and scaffold novelty delivers, and
        exactly what a relative method cannot relate.

        Each edge beyond a spanning forest closes one independent cycle, and the deviation of the sum
        of differences around a cycle from zero is hysteresis -- the one error estimate in this
        pipeline that is a measurement rather than a literature value. Ask for cycles deliberately.

        Args:
            molecules_json: JSON object of ``{id: smiles}``.
            references: Comma-separated ids with a measured affinity. A component containing none
                yields differences and no absolute values.
            min_core_fraction: Minimum share of the larger molecule the common core must cover. 0.5
                is already generous against published guidance for alchemical edges.
            cycle_edges: Edges beyond a spanning forest, each buying one hysteresis check.
        """

        from etalon.fep.network import design

        molecules = json.loads(molecules_json)
        if not isinstance(molecules, dict):
            raise ValueError("molecules_json must be a JSON object of {id: smiles}")
        network = design(
            molecules,
            references=tuple(s.strip() for s in references.split(",") if s.strip()),
            min_core_fraction=min_core_fraction,
            cycle_edges=cycle_edges,
        )
        return ok(
            network=network.as_dict(),
            rendered=network.render(),
            next_step=(
                f"{len(network.unreachable)} molecule(s) have no usable edge. They need an absolute "
                "method or a reference compound from their own chemotype; connecting one through a "
                "degenerate mapping produces numbers rather than an error. See docs/adr/0005 for the "
                "three ways out, and note that the choice belongs before generation."
                if network.unreachable
                else "Every molecule is reachable. If cycles is 0 this network has no internal error "
                "estimate; each extra edge buys one hysteresis check."
            ),
        )

    @mcp.tool()
    @tool(Cost.NEVER)
    def etalon_recommend_waiver(code: str, reason: str, expires: str, recommended_by: str) -> str:
        """NEVER COMPLETED BY A MODEL. Record a waiver recommendation for a person to grant.

        A waiver lets a campaign spend past a fault that would otherwise refuse a molecule. Its
        entire value is that a named person accepted a named consequence for a stated reason, with an
        expiry, and can be asked about it a year later when somebody reads the record.

        A language model writes that reason better than most people and cannot be held to it. That is
        the measured problem rather than a principle: structured-looking output carries an impression
        of rigour its content has not earned, and a waiver reason is pure structure -- a plausible
        justification and a sound one are indistinguishable at the point of reading, which is the only
        point at which anyone reads it.

        So this tool returns a recommendation with ``status: AWAITING_A_PERSON``. Show it to the
        operator. If they agree, they grant it, and their name goes in the record rather than yours.

        Args:
            code: The fault code. Must be one a waiver can release -- a fault that only qualifies a
                claim about the run was never blocking and has nothing to waive.
            reason: What makes this acceptable for this campaign. At least 24 characters, because a
                reader a year from now has only this sentence.
            expires: ISO date. Required: an unbounded waiver becomes the configuration and nobody
                revisits it.
            recommended_by: Who is recommending. Say so honestly -- if it is you, say which model.
        """

        from datetime import date

        from etalon.judgment.proposal import Advisor, AdvisorKind
        from etalon.judgment.waiver import Waiver

        advisor = Advisor(
            kind=AdvisorKind.LANGUAGE_MODEL, identifier=recommended_by, transport="mcp"
        )
        recommendation = Waiver.recommend(advisor, code, reason, date.fromisoformat(expires))
        return ok(
            **recommendation,
            next_step=(
                "Show this to the operator. If they agree, they grant it under their own name, and "
                "the granted waiver -- "
                + _WAIVER_SHAPE
                + " -- is what goes in the waived argument of etalon_check_handoff or "
                "etalon_rule_admissible. Both tools refuse a bare code, and both echo the grantor "
                "back in their result so the operator can see whose name is on it. Do not write "
                "your own name into granted_by: a waiver nobody granted is a disabled check wearing "
                "a decision's clothes."
            ),
        )


__all__ = ["register"]
