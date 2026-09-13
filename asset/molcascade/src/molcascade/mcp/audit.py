"""Why a run decided what it decided.

Four readers over committed artifacts.  None of them runs anything in the
pipeline, changes a contract, or touches a cache key, and all four work on a run
that failed partway -- which is usually when the question is worth asking.

They answer different questions and it is worth knowing which is which before
calling one.  :func:`summarise_decisions` is the aggregate: which reason codes
fired, in which stage, how often.  :func:`explain_molecule` is the single case:
one molecule, every stage it reached, and the verdict with the reason.
:func:`audit_stereochemistry` is a specific and easily missed failure:
stereocentres that 3D embedding chose rather than the molecule's name specifying.
:func:`measure_recall` is the calibration: point the funnel at molecules already
known to be worth keeping and count how many it deleted.

That last one is the one to run before trusting any threshold.  Recall multiplies
through a cascade -- twenty-two gates each keeping 98% keep 64% between them, and
no single tier looks wrong at any point -- so the product is the only number that
shows it.
"""

from __future__ import annotations

from typing import Any

from molcascade.mcp._common import logger, ok, open_runner, tool


def register(mcp: Any) -> None:
    @mcp.tool()
    @tool
    def summarise_decisions(
        run_id: str,
        workspace: str,
        stage_id: str = "",
        max_buckets: int = 2048,
        sample_size: int = 5,
    ) -> str:
        """Aggregate every reason code a run recorded, grouped by the stage.

        Every gate in MolCascade writes a typed decision row: the standardizer
        when it picks one parent out of a multi-component record, every threshold
        gate when a property falls outside its window, every alert catalogue when
        a substructure matches.  All of it is committed and immutable, and this
        reads it back.

        Two things about the numbers, because each is a way a summary like this
        quietly reports something false.

        **These are rows, not molecules.**  A hard gate writes one row per
        finding and a property gate one per out-of-range property, so a molecule
        rejected for both its weight and its heavy-atom count contributes two rows
        to one stage.  The exact molecule counts are elsewhere and are exact
        there: per-stage ``reject_count`` is in each artifact's metadata, and the
        funnel survivor counts come from the run state.

        **Reason codes are not low-cardinality.**  The rd_filters adapter mixes a
        hash of the matched rule into its code and the medchem adapters hash rule
        *combinations*, so a run against a full catalogue can emit over a thousand
        distinct codes.  The bucket table is therefore capped; rows whose bucket
        did not fit are counted into ``untracked_rows`` so the totals stay exact,
        and ``outcome_totals`` is aggregated independently of the cap.

        Args:
            run_id: The run to summarise.
            workspace: Absolute path to its workspace.
            stage_id: Report only this stage.  Omit for all of them.
            max_buckets: Distinct reason-code buckets retained per stage.
            sample_size: Example entity ids kept per bucket, to feed into
                ``explain_molecule``.  Set to 0 to suppress them.

        Returns:
            JSON with ``rejected_rows`` and ``warned_rows`` overall, and per stage
            the exact ``outcome_totals`` keyed on entity kind and outcome, the
            retained buckets with their row counts and sample ids, and
            ``untracked_rows``.
        """

        from molcascade.decisions import build_decision_digest

        runner = open_runner(workspace)
        logger.info("Summarising decisions for %s", run_id)
        digest = build_decision_digest(
            runner, run_id, max_buckets=max_buckets, sample_size=sample_size
        )
        payload = digest.as_dict()
        if stage_id:
            payload["stages"] = [
                stage for stage in payload["stages"] if stage["stage_id"] == stage_id
            ]
            if not payload["stages"]:
                payload["note"] = (
                    f"stage {stage_id!r} published no decision dataset in this run; "
                    "source readers and featurisers ordinarily do not"
                )
        return ok(**payload)

    @mcp.tool()
    @tool
    def explain_molecule(
        run_id: str,
        workspace: str,
        smiles: str = "",
        parent_id: str = "",
        desalt_limit: int = 200,
    ) -> str:
        """Follow one molecule through a run and say what happened to it.

        Give either a SMILES or a parent id.  A SMILES is standardised through the
        run's own identity policy -- recovered from the run's manifest rather than
        assumed, and cross-checked, so this refuses rather than guessing if the
        policy cannot be established.  That matters because the same SMILES under
        two different standardisation policies is two different molecules, and an
        explanation keyed on the wrong one would be confidently about nothing.

        The verdict is one of: ``ABSENT`` (never registered), ``REJECTED`` (a gate
        removed it, with the stage and reason), ``DESALTED`` (it was a component
        of a multi-part record and another component became the parent),
        ``SURVIVED`` (it reached the end), ``SELECTED`` or ``NOT_SELECTED`` (the
        shortlist selector kept or dropped it).

        Args:
            run_id: The run to search.
            workspace: Absolute path to its workspace.
            smiles: The molecule as SMILES.  Standardised before matching.
            parent_id: The molecule's parent id, if you have it -- for instance
                from a sample id in ``summarise_decisions``.  Exact, no
                standardisation.
            desalt_limit: How many de-salting records to scan when looking for a
                molecule that was a discarded component.  The scan is the
                expensive half of this tool; lower it on a very large run.

        Returns:
            JSON with the ``verdict``, the ``parent_id`` it resolved to, the
            ordered stage events it passed through with each stage's outcome and
            reason, and -- for a DESALTED verdict -- which record it came from and
            which component won.
        """

        from molcascade.errors import MolCascadeError
        from molcascade.explain import explain_molecule as _explain

        if not smiles and not parent_id:
            raise MolCascadeError(
                "give either smiles or parent_id",
                code="MCP_EXPLAIN_NO_SUBJECT",
                hint=(
                    "A parent id is exact. A SMILES is standardised through the run's "
                    "own identity policy first, which is what makes it comparable."
                ),
            )
        runner = open_runner(workspace)
        verdict = _explain(
            runner,
            run_id,
            smiles=smiles or None,
            parent_id=parent_id or None,
            desalt_limit=desalt_limit,
        )
        return ok(**verdict.as_dict())

    @mcp.tool()
    @tool
    def audit_stereochemistry(run_id: str, workspace: str) -> str:
        """Report which molecules had a stereocentre chosen by 3D embedding.

        Conformer generation enforces the chirality a SMILES already specifies and
        says nothing about the centres it leaves open; distance geometry settles
        those from a seeded hash.  So the molecule that reached a docking engine is
        one specific stereoisomer, while the name it is filed under designates a
        set of them.  This re-derives the configuration from the coordinates that
        were actually used and compares it against that name.

        It reports rather than judges.  A centre assigned by embedding is not an
        error; it is a fact about what was docked, and whether it matters depends
        on whether the target discriminates -- which for a real target is worth 1
        to 3 kcal/mol, larger than the precision a free-energy calculation will
        claim.

        ``CONTRADICTED`` is the status to take seriously: it means the geometry
        names a *different* configuration at a centre the molecule's own SMILES had
        already assigned, which embedding should make impossible. If it appears,
        something upstream of the geometry inverted a centre.

        Args:
            run_id: The run to audit.
            workspace: Absolute path to its workspace.

        Returns:
            JSON with counts per status (``ASSIGNED_BY_EMBEDDING``, ``AGREES``,
            ``CONTRADICTED``, ``UNREADABLE``), how many distinct molecules had a
            configuration chosen for them, and one finding per structure that did
            not simply agree -- each carrying the parent SMILES, the assigned
            SMILES and how many descriptors the geometry added.  A run that
            committed no 3D geometry says so rather than returning nothing.
        """

        from molcascade.stereo import reconcile_stereo

        runner = open_runner(workspace)
        summary = reconcile_stereo(runner, run_id)
        return ok(**summary.as_dict())

    @mcp.tool()
    @tool
    def measure_recall(run_id: str, workspace: str) -> str:
        """Measure what a funnel kept, using molecules known to be worth keeping.

        Point a cascade at a panel of known actives, run it, then call this.  Every
        hard rejection in a cascade is the claim *molecules like this are not worth
        looking at*, and the cheapest way to test one is to count how many known
        binders it deletes.

        The run must have been a cascade and its library must have been read with
        an identifier column -- a panel has to be readable by name, and a run that
        recorded no names is refused here rather than reported as a funnel that
        lost everything.

        Four properties of the numbers, each of which is a way this measurement
        can lie and each handled rather than hoped about.  An unreadable tier makes
        the whole product ``null`` rather than optimistic, because treating
        survivors as equal to arrivals would make a broken run look perfect. A run
        that did not finish gets no end-to-end figure at all.  Stages that ran
        *after* the last tier are counted too -- the shortlist selector caps each
        Murcko scaffold, and a panel of measured molecules is a congeneric series,
        so on a real panel that cap can remove more molecules than every gate
        combined while each tier truthfully reports keeping everything.  And no
        names is an error rather than a result of zero.

        Args:
            run_id: The run whose library was the panel.
            workspace: Absolute path to its workspace.

        Returns:
            JSON with ``registered``, ``survivors``, ``end_to_end_recall`` as the
            product of every stage's retention, per-tier rows naming which
            molecules each tier lost, a separate ``finalize`` block for the
            post-tier stages, and ``notes`` explaining anything withheld.
        """

        from molcascade.recall import measure_recall as _measure

        runner = open_runner(workspace)
        report = _measure(runner, run_id)
        return ok(**report.as_dict())


__all__ = ["register"]
