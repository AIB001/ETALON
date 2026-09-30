"""The throughput regime: calibrate the gates, authorize them, then sweep a pool through them.

Every other module on this server assumes an expensive stage exists. These tools are for the campaign
that has none -- a million generated molecules and a cascade, where no single measurement is worth
authorising and the irreversible act is a threshold applied too many times to notice.

The order is the argument, and it is shorter than the expensive-stage one:

1. ``etalon_calibrate_gates`` -- screen a known-active panel and read what the funnel did to it. Free.
2. ``etalon_authorize_gates`` -- mint the token, or be refused with the panel's own numbers. Free.
3. ``etalon_sweep_admit`` / ``etalon_sweep_emit`` -- fill the pool, carve batches. Cheap.
4. ``etalon_sweep_claim`` / ``etalon_sweep_record`` -- one batch's reservation and outcome. Free.
5. ``etalon_sweep_recover`` -- resolve anything a crash left claimed. Free.
6. ``etalon_sweep_status`` -- one reading, including whether the batches are comparable.

Step 2 is the one that did not exist. A campaign that skips it gets a refusal at step 3 rather than an
empty shortlist at the end, which is the entire point: MolCascade's shipped docking thresholds reject
all eight molecules of a real SND1 panel, including both co-crystal ligands.
"""

from __future__ import annotations

import json
from typing import Any

from etalon.mcp._common import Cost, absolute_path, fail, ok, tool


def _sweep(workspace: str, pool: str, ledger: str, *, gate_json: str = "") -> Any:
    from etalon.authority.gate import GateAuthorization
    from etalon.boundary.screen import Screen
    from etalon.campaign.ledger import Ledger
    from etalon.campaign.sweep import Sweep

    gate = None
    if gate_json.strip():
        payload = json.loads(gate_json)
        gate = GateAuthorization(
            revision_id=str(payload["revision_id"]),
            calibration_sha256=str(payload["calibration_sha256"]),
            infrastructure_sha256=str(payload.get("infrastructure_sha256", "")),
            panel_size=int(payload["panel_size"]),
            actives=int(payload["actives"]),
            recall=float(payload["recall"]),
            rankable_engines=tuple(payload.get("rankable_engines", ())),
            unchecked=tuple(payload.get("unchecked", ())),
            issued_at=str(payload["issued_at"]),
            expires_at=str(payload["expires_at"]),
            signature=str(payload["signature"]),
        )
    return Sweep(
        Screen(absolute_path(workspace, label="workspace")),
        Ledger(absolute_path(ledger, label="ledger")),
        absolute_path(pool, label="pool"),
        gate=gate,
    )


def register(mcp: Any) -> None:
    @mcp.tool()
    @tool(Cost.FREE)
    def etalon_calibrate_gates(
        workspace: str,
        run_id: str,
        panel_json: str,
        required_recall: float = 1.0,
    ) -> str:
        """FREE. Read what a funnel did to a panel of known molecules, and judge the gates.

        Call this on a run whose library was the panel, **before** screening anything at scale. The
        run may have ended exhausted -- every panel member gated out -- and that is the most useful
        case, because the docking scores are committed regardless and they say how far the threshold
        sits from the known binders.

        Two verdicts come back and they are independent.

        ``admissible`` asks whether any gate deleted a known active. Measured on SND1, MolCascade's
        shipped defaults (Uni-Dock <= -8.5, KarmaDock >= 40) delete all eight panel members including
        both co-crystal ligands, and a campaign that ran them unexamined produced an empty shortlist
        with every log line reading SUCCEEDED.

        ``rankable_engines`` asks whether the score ordered the panel at all. On the same panel it is
        empty: imatinib, which does not bind SND1, outscores both co-crystal ligands. A score that
        cannot order known binders is a usable filter and an unusable ranking, and a campaign
        reporting it as potency is over-reading it. This is the finding a table nobody computed.

        Args:
            workspace: Absolute path to the MolCascade workspace the panel ran in.
            run_id: That run.
            panel_json: ``[{"parent_id": ..., "known_active": true|false, "evidence": "..."}, ...]``.
                ``known_active`` must be an explicit boolean for every member: a panel drawn only
                from binders cannot tell a discriminating gate from one that keeps everything, so the
                negative controls have to be declared rather than implied.
            required_recall: Fraction of known actives that must survive. Leave at 1.0 unless you can
                say which known binder you are willing to lose, and why.
        """

        from etalon.boundary.screen import Screen
        from etalon.campaign.calibrate import PanelMember, calibrate

        rows = json.loads(panel_json)
        if not isinstance(rows, list) or not rows:
            raise ValueError("panel_json must be a nonempty JSON array of panel members")
        panel = [
            PanelMember(
                parent_id=str(row["parent_id"]),
                known_active=row["known_active"],
                evidence=str(row.get("evidence", "")),
            )
            for row in rows
        ]
        screen = Screen(absolute_path(workspace, label="workspace"))
        result = screen.state(run_id)
        if result is None:
            return fail(
                "RunNotFound",
                f"no run {run_id!r} in this workspace",
                hint="Screen the panel first, with --id-column so the members are traceable by name.",
                run_id=run_id,
            )
        calibration = calibrate(screen, result, panel, required_recall=float(required_recall))
        return ok(calibration=calibration.as_dict(), provenance=screen.provenance())

    @mcp.tool()
    @tool(Cost.FREE)
    def etalon_authorize_gates(
        workspace: str,
        run_id: str,
        panel_json: str,
        required_recall: float = 1.0,
        lifetime_hours: float = 168.0,
    ) -> str:
        """FREE. Mint the token a sweep requires, or refuse with the panel's own numbers.

        This is ``etalon_authorize_spend``'s counterpart for a campaign with no simulation stage. That
        one binds permission to a molecule's geometry; this one binds it to a compiled funnel's
        ``revision_id`` and to a digest of the calibration verdict, so calibrating one cascade and
        screening with another is caught the way building a different row is caught.

        The refusal is the product. A token is minted only for a configuration that kept every known
        active, and the error otherwise names which ones it deleted.

        Pass the returned ``gate`` object to every ``etalon_sweep_*`` call as ``gate_json``.

        Args:
            lifetime_hours: 168 (seven days) by default -- a campaign's timescale. A configuration
                does not drift on its own; what invalidates a calibration is a new panel member or a
                rebuilt engine, and two of those are caught by digest rather than by time.
        """

        from etalon.authority.gate import authorize_gate
        from etalon.authority.grant import NotAuthorized
        from etalon.boundary.screen import Screen
        from etalon.campaign.calibrate import PanelMember, calibrate
        from etalon.campaign.calibrate import Calibration  # noqa: F401 -- documented return shape

        rows = json.loads(panel_json)
        panel = [
            PanelMember(str(r["parent_id"]), r["known_active"], str(r.get("evidence", "")))
            for r in rows
        ]
        screen = Screen(absolute_path(workspace, label="workspace"))
        result = screen.state(run_id)
        if result is None:
            return fail("RunNotFound", f"no run {run_id!r}", hint="Screen the panel first.")
        calibration = calibrate(screen, result, panel, required_recall=float(required_recall))
        try:
            token = authorize_gate(
                calibration,
                provenance=screen.provenance(),
                lifetime_hours=float(lifetime_hours),
            )
        except NotAuthorized as error:
            from etalon.authority.gate import unauthorized_gate

            return fail(
                "NotAuthorized",
                str(error),
                hint=(
                    "Widen the gate that deleted the known actives and re-screen the panel. Do not "
                    "lower required_recall to get past this: the panel is the only evidence the "
                    "threshold has."
                ),
                **unauthorized_gate(calibration),
            )
        return ok(
            gate=token.as_dict(),
            calibration=calibration.as_dict(),
            note=(
                "Pass gate verbatim as gate_json to etalon_sweep_emit. "
                + (
                    "No engine's score ordered the panel, so the shortlist's score column is a "
                    "filter and not a ranking."
                    if not token.rankable_engines
                    else f"Rankable engines: {list(token.rankable_engines)}."
                )
            ),
        )

    @mcp.tool()
    @tool(Cost.CHEAP)
    def etalon_sweep_admit(
        workspace: str,
        pool: str,
        ledger: str,
        molecules_json: str,
    ) -> str:
        """CHEAP. Add molecules to the sweep's pool, deduplicating globally and permanently.

        A molecule proposed by two generators, or twice by one, is screened once. The pool records
        which generator proposed it first, which is what lets a shortlist be attributed to a source
        and what makes :mod:`etalon.generate.productivity`'s uniqueness measurement possible -- only
        the pool knows what the library already held.

        Args:
            molecules_json: ``[{"key": ..., "smiles": ..., "source": ...}, ...]``. ``key`` is the
                identity the deduplication is on; an InChIKey is the usual choice. Two different
                standardisation policies produce two different keys for one molecule, so the policy
                has to be fixed before the pool is filled, not after.
        """

        rows = json.loads(molecules_json)
        sweep = _sweep(workspace, pool, ledger)
        report = sweep.admit(
            (str(r["key"]), str(r["smiles"]), str(r.get("source", "unknown"))) for r in rows
        )
        return ok(admitted=report.as_dict())

    @mcp.tool()
    @tool(Cost.CHEAP)
    def etalon_sweep_emit(
        workspace: str,
        pool: str,
        ledger: str,
        revision_id: str,
        gate_json: str,
        flush: bool = False,
    ) -> str:
        """CHEAP. Carve every full batch the pool can support. Refuses without a gate authorization.

        The refusal happens here, before any GPU work, because here is where molecules become
        committed to a configuration.

        Args:
            revision_id: The compiled funnel these batches will be screened with, from
                ``etalon_screen_plan``. Must match the gate authorization's.
            gate_json: The ``gate`` object from ``etalon_authorize_gates``.
            flush: Also carve a short final batch from the remainder. End of campaign only. A short
                batch is not a smaller sample of the same thing -- end-to-end survival is a fraction
                of a percent, and three 5,000-molecule batches measured at the tail of one campaign
                yielded 6, 2 and 0 hits.
        """

        sweep = _sweep(workspace, pool, ledger, gate_json=gate_json)
        carved = sweep.emit(revision_id=revision_id, flush=bool(flush))
        return ok(
            emitted=[batch.as_dict() for batch in carved],
            ready=sweep.ready(),
            pending=len(sweep.pending()),
        )

    @mcp.tool()
    @tool(Cost.FREE)
    def etalon_sweep_claim(
        workspace: str, pool: str, ledger: str, batch_id: str, by: str, library_path: str = ""
    ) -> str:
        """FREE. Reserve one batch for one screener, and optionally write its library CSV.

        The reservation is what makes a crashed screener's batch findable rather than merely absent,
        and the conditional update is what stops two screeners taking the same batch.

        Args:
            by: Who is taking it. Refused if empty: an anonymous reservation cannot be recovered.
            library_path: Absolute path to write the batch as CSV. Always includes an ``id`` column --
                without one a run records no molecule names, and per-tier recall, per-molecule
                explanation and a named shortlist all have nothing to key on.
        """

        from etalon.campaign.sweep import library_rows

        sweep = _sweep(workspace, pool, ledger)
        batch = sweep.claim(batch_id, by=by)
        written = None
        if library_path.strip():
            written = str(
                library_rows(sweep, batch_id, absolute_path(library_path, label="library_path"))
            )
        return ok(batch=batch.as_dict(), library=written)

    @mcp.tool()
    @tool(Cost.FREE)
    def etalon_sweep_record(workspace: str, pool: str, ledger: str, batch_id: str, run_id: str) -> str:
        """FREE. Record a batch's terminal outcome from its run record, and append it to the ledger.

        Three outcomes, not two. ``exhausted`` means every molecule was gated out, which is a
        measurement: every docking score the run committed is in the artifact store, and one such
        batch held 7,545 of them. Recording it as a failure would invite a retry of work that already
        did what it was asked.

        Refuses a revision that disagrees with the one the batch was carved under -- the provenance
        hole that 56 hand-recorded batches had.
        """

        from etalon.boundary.screen import Screen

        sweep = _sweep(workspace, pool, ledger)
        result = Screen(absolute_path(workspace, label="workspace")).state(run_id)
        if result is None:
            return fail("RunNotFound", f"no run {run_id!r}", hint="Check the run id.")
        if result.status not in ("SUCCEEDED", "FAILED"):
            return fail(
                "NotTerminal",
                f"run {run_id} is {result.status}; nothing to record yet",
                hint="Poll etalon_sweep_status, or call etalon_sweep_recover after a crash.",
                retryable=True,
            )
        return ok(batch=sweep.record(batch_id, result).as_dict())

    @mcp.tool()
    @tool(Cost.FREE)
    def etalon_sweep_recover(
        workspace: str, pool: str, ledger: str, abandoned: str = ""
    ) -> str:
        """FREE. Resolve every claimed-but-unrecorded batch by asking the run record.

        Never looks for a process, and that is the design decision. Measured: ``pgrep`` on a run id
        matched the supervisor's own shell, and a worker whose shell had died left a child committing
        stages for another hour. The run record was right in both cases and the process table was not.

        Four actions come back. ``recorded`` -- the run finished, whatever became of its claimer; this
        is the case that left one batch claimed for six hours after its screen succeeded.
        ``requeued`` -- claimed but no run was ever started. ``running`` -- left alone.
        ``abandoned`` -- only for batches you name.

        Args:
            abandoned: Comma-separated batch ids you have confirmed are no longer being screened. A
                non-terminal run whose process is gone cannot be distinguished from a slow one by
                anything durable, so it takes an assertion from someone who looked -- the same
                discipline an interrupted active-learning action requires.
        """

        sweep = _sweep(workspace, pool, ledger)
        named = tuple(part.strip() for part in abandoned.split(",") if part.strip())
        actions = sweep.recover(abandoned=named)
        return ok(recovered=[action.as_dict() for action in actions], state=sweep.state())

    @mcp.tool()
    @tool(Cost.FREE)
    def etalon_sweep_status(workspace: str, pool: str, ledger: str) -> str:
        """FREE. One reading of the sweep: pool, batches by outcome, and whether they are comparable.

        ``comparable`` is false when recorded batches carry more than one cascade revision. Not an
        error -- a campaign may legitimately retune -- but enrichment computed across that boundary is
        attributable to nothing, and a status that did not say so would let the comparison be made
        silently.
        """

        sweep = _sweep(workspace, pool, ledger)
        return ok(sweep=sweep.state())

    @mcp.tool()
    @tool(Cost.FREE)
    def etalon_sweep_progress(workspace: str, run_id: str) -> str:
        """FREE. Which stage a running batch is on, out of how many.

        Use this instead of inferring progress from GPU memory. Both docking engines in a default
        cascade allocate about 25 GB and release it, so a batch at stage 6 of 43 and one at stage 29
        present identically -- a campaign log recorded "nearly finished" for a batch at stage 27 twice
        in one night from exactly that read.
        """

        from etalon.boundary.screen import Screen

        return ok(
            progress=Screen(absolute_path(workspace, label="workspace")).progress(run_id)
        )

    @mcp.tool()
    @tool(Cost.FREE)
    def etalon_generation_productivity(loops_json: str, cost_ceiling: float = 2.0) -> str:
        """FREE. Decide which generation loops to keep running, from measurements taken as they run.

        Two measurements, both free, both taken on 10^5 molecules rather than on 10^1 hits -- which is
        why they are preferred over hit share. ``findings/0005``: at realistic counts a model's share
        of the final hits distinguishes none of five models from each other.

        **Viability** is delivered over requested. A model can be good and deliver almost nothing
        because the box is too large: TargetDiff returned 2 of 100 on a 28 A box and 92 of 100 on a
        22 A box. The refusal names that remedy instead of retiring the model.

        **Exhaustion** is new-to-the-library over delivered, expressed as the cost multiplier it
        implies. FLOWR held 87% flat for 39 chunks (1.15x); MolCRAFT fell from 32% to 24% (3.1x to
        4.2x) and had simply finished -- which nobody noticed for hours because nothing was watching.

        Args:
            loops_json: ``[{"tag": ..., "model": ..., "pocket": ..., "chunks": [{"requested": ...,
                "delivered": ..., "unique": ..., "seconds": ...}, ...]}, ...]``. ``unique`` is
                new-to-the-library after global deduplication, so it comes from the pool rather than
                from the generator. ``pocket`` labels the conditioning geometry including the box:
                viability is a property of the model and the box together.
            cost_ceiling: GPU-hours per screenable molecule, relative to a perfect generator, above
                which a loop should stop. Two by default -- half the output is duplicates.
        """

        from etalon.generate.productivity import Chunk, Productivity, allocate

        rows = json.loads(loops_json)
        if not isinstance(rows, list) or not rows:
            raise ValueError("loops_json must be a nonempty JSON array")
        profiles = [
            Productivity(
                tag=str(row["tag"]),
                model=str(row["model"]),
                pocket=str(row.get("pocket", "unspecified")),
                chunks=tuple(
                    Chunk(
                        requested=int(c["requested"]),
                        delivered=int(c["delivered"]),
                        unique=int(c["unique"]),
                        seconds=float(c["seconds"]),
                    )
                    for c in row["chunks"]
                ),
                cost_ceiling=float(cost_ceiling),
            )
            for row in rows
        ]
        return ok(**allocate(profiles))

    @mcp.tool()
    @tool(Cost.FREE)
    def etalon_devices() -> str:
        """FREE. Measure this machine's accelerators and cores instead of being told about them.

        ``etalon_campaign_plan`` takes device counts as integers and had no way to check them, so a
        plan could be computed for a machine that does not exist -- and its shipped defaults describe
        the single campaign they were measured on, not the host answering this call.

        Detection is MolCascade's ``detect_environment``: one ``nvidia-smi`` fork, **no torch import**,
        and it never raises. A machine that answers nothing returns a reading full of nulls plus a note
        saying why, because "we could not tell" is not an outage.

        Two things this does not establish. ``memory_free_mib`` is one snapshot taken now, not a
        reservation, and a neighbouring tenant between allocations looks idle -- attribute load with
        the commands in ``etalon-campaign-monitoring`` before concluding a card is yours. And a card
        detected here may still be unreachable by a framework: a ``torch`` built ``+cpu`` sees none of
        them, which this call cannot see and a screen will discover.
        """

        from etalon.boundary.infra import load

        # Pinned before the import rather than after. A bare ``from molcascade...`` binds whichever
        # copy sys.path found, and an editable install shadowing the vendored tree is silent -- which
        # is the failure load() exists to refuse, and the reason the commit is reported below.
        infra = load("molcascade")
        from molcascade.environment import detect_environment

        environment = detect_environment()
        devices = [
            {
                "device": f"cuda:{gpu.index}",
                "index": gpu.index,
                "vendor": str(gpu.vendor),
                "name": gpu.name,
                "memory_total_gib": gpu.memory_total_gib,
                "memory_free_mib": gpu.memory_free_mib,
                "compute_capability": gpu.compute_capability,
                "driver_version": gpu.driver_version,
            }
            for gpu in environment.gpus
        ]
        return ok(
            devices=devices,
            total=len(devices),
            accelerator=str(environment.accelerator),
            usable_cores=environment.cpu.usable_cores,
            total_gpu_memory_gib=environment.total_gpu_memory_gib,
            wsl=environment.platform.wsl,
            notes=list(environment.notes),
            measured_by={
                "asset": infra.name,
                "source_commit": infra.source_commit,
                "function": "molcascade.environment.detect_environment",
            },
        )

    @mcp.tool()
    @tool(Cost.FREE)
    def etalon_campaign_plan(
        pool_size: int,
        batch_size: int = 20000,
        screen_devices: int = 3,
        generation_devices: int = 5,
        minutes_per_batch: float = 95.0,
        unique_per_generator_hour: float = 4100.0,
        batches_per_device: int = 1,
        detect: bool = False,
    ) -> str:
        """FREE. Size a sweep's generation and screening against each other, before starting one.

        The failure this prevents is not a wrong calculation, it is an unbalanced machine. One
        campaign ran five generation loops against six screen workers and sat generation-limited by
        14x for its first eighteen hours -- the screeners idle, ``pending`` at zero every check --
        because nobody had computed the ratio. Moving three GPUs from screening to generation
        afterwards produced half that campaign's hits.

        Returns molecules per hour on each side, which side binds, and how long the pool takes.

        The device counts are yours to declare and detection does not overrule them -- but with
        ``detect=True`` this call measures the machine and **refuses a plan that asks for more devices
        than exist**, which is the one arithmetic error here that no amount of care in the rates can
        catch. ``recommended_split`` is the balance point for whatever total it ends up with.

        One honesty note that the result now carries rather than only this docstring: both shipped
        rates were measured on one campaign and **neither records the device it was measured on**, so
        detecting an 80 GB card cannot tell you the 95 minutes still holds. Re-measure them on your
        own cascade and target before trusting a split computed from them.

        Args:
            pool_size: Molecules the campaign intends to screen. Zero for an open-ended sweep.
            detect: Measure the host with :func:`etalon_devices` and cross-check the declared counts
                against it. Off by default so the tool stays pure arithmetic that runs anywhere,
                including a laptop planning a run for a cluster.
            minutes_per_batch: Wall clock for one batch through the cascade. 95 measured on a
                20,000-molecule batch with two docking engines and a redock tier.
            unique_per_generator_hour: New-to-the-library molecules one generation loop delivers per
                hour. 4,100 measured for FLOWR at 87% uniqueness; a model at 30% delivers a third of
                that from the same GPU-hour, which is why uniqueness and not raw throughput is the
                input here.
        """

        if batch_size < 1 or screen_devices < 1 or generation_devices < 0:
            raise ValueError("batch_size and screen_devices must be positive")
        if batches_per_device < 1:
            raise ValueError("batches_per_device must be at least 1")
        if minutes_per_batch <= 0 or unique_per_generator_hour <= 0:
            raise ValueError("rates must be positive")

        machine: dict[str, Any] | None = None
        if detect:
            from etalon.boundary.infra import load

            load("molcascade")
            from molcascade.environment import detect_environment

            environment = detect_environment()
            names = sorted({gpu.name for gpu in environment.gpus})
            machine = {
                "detected_devices": len(environment.gpus),
                "device_names": names,
                "usable_cores": environment.cpu.usable_cores,
                "notes": list(environment.notes),
            }
            asked = screen_devices + generation_devices
            if asked > len(environment.gpus):
                raise ValueError(
                    f"this plan asks for {asked} devices ({screen_devices} screening + "
                    f"{generation_devices} generation) and {len(environment.gpus)} were detected"
                    + (f" ({', '.join(names)})" if names else "")
                    + ". Every rate below would be arithmetic about a machine that does not exist. "
                    "Declare the counts this host has, or plan without detect=True for a different one."
                )

        per_screen_device = (60.0 / minutes_per_batch) * batch_size
        produced = generation_devices * unique_per_generator_hour
        # Screening throughput scales with concurrent batches, not with cards. Supervisor
        # takes batches_per_device for the case a measurement made real on ALK2: the
        # docking tier was CPU-bound, so eight cards each ran two batches and the plan that
        # could only describe one-per-card refused to size the configuration the campaign
        # was actually running.
        consumed = screen_devices * batches_per_device * per_screen_device
        # Generation stopped is a phase, not a shortage. A sweep that has finished generating and is
        # draining a fixed pool has one question -- how long to screen it -- and the concurrent model
        # answered it with null, because min(0, screening) is zero. Measured on ALK2: generation was
        # deliberately retired with the pool fully carved, and the plan advised moving four cards
        # back to generation and declined to say how long the remaining batches would take.
        draining = generation_devices == 0
        if draining:
            binding = "screening"
            ratio = None
            hours = (pool_size / consumed) if pool_size and consumed else None
        else:
            binding = "generation" if produced < consumed else "screening"
            ratio = (consumed / produced) if produced else float("inf")
            hours = (
                (pool_size / min(produced, consumed))
                if pool_size and min(produced, consumed)
                else None
            )

        # The balance point, closed form: generation and screening match when
        # g * r_gen == s * r_screen with g + s fixed, so g = total * r_screen / (r_gen + r_screen).
        # Reported beside `advice` rather than replacing it because they answer different questions --
        # advice frees the screeners that current generation does not need, this says where the two
        # sides meet once the freed cards are generating too.
        total_devices = machine["detected_devices"] if machine else screen_devices + generation_devices
        split: dict[str, Any] | None = None
        if total_devices >= 2:
            generating = round(total_devices * per_screen_device / (unique_per_generator_hour + per_screen_device))
            generating = min(max(generating, 1), total_devices - 1)
            split = {
                "total_devices": total_devices,
                "generation": generating,
                "screening": total_devices - generating,
                "basis": "detected" if machine else "declared",
            }
        elif total_devices == 1:
            split = {
                "total_devices": 1,
                "note": "one device cannot be split; generate a pool first, then screen it.",
            }

        advice = []
        if draining:
            advice.append(
                f"Generation is stopped, so the pool is fixed at {pool_size:,} molecules and "
                f"hours_to_screen_pool is the whole answer. Nothing here argues for restarting "
                "generation; a pool already carved into batches gains nothing from more molecules "
                "queued behind them."
            )
        elif binding == "generation" and ratio > 1.5:
            # The move comes from `recommended_split` rather than from a second calculation here. The
            # earlier one asked how many screeners the *current* generation rate does not need, which
            # ignores that a moved card then generates: on the 5-generation/6-screening campaign it
            # said four. Four lands at 0.68, which is 1.47x imbalanced the other way -- inside the
            # band this advice tolerates, by 0.01, and still worse than the 1.16 that three gives.
            # Three is also the move that campaign's operator actually made.
            move = max(1, screen_devices - split["screening"]) if split and "screening" in split else 1
            advice.append(
                f"Screening has {ratio:.1f}x the capacity generation is feeding it. Move {move} "
                f"device(s) from screening to generation; the screen still keeps up, and the two "
                "sides meet at the split in recommended_split."
            )
        elif binding == "screening" and ratio < 0.67:
            advice.append(
                f"Generation outruns screening by {1 / ratio:.1f}x. The pool will grow without bound; "
                "add screen devices or accept that the tail is screened after generation stops."
            )
        else:
            advice.append("The two sides are within a factor of 1.5, which is balanced enough.")
        if batch_size < 10000:
            advice.append(
                f"A {batch_size}-molecule batch is small for a funnel whose end-to-end survival is a "
                "fraction of a percent: three 5,000-molecule batches measured on one campaign yielded "
                "6, 2 and 0 hits. Prefer 20,000 except when flushing the tail."
            )
        return ok(
            unique_molecules_per_hour={"generation": round(produced), "screening": round(consumed)},
            binding_constraint=binding,
            batches_per_device=batches_per_device,
            capacity_ratio=round(ratio, 2) if produced else None,
            hours_to_screen_pool=None if hours is None else round(hours, 1),
            recommended_split=split,
            machine=machine,
            advice=advice,
            # Which inputs are this campaign's and which are borrowed. Every number here is either
            # supplied by the caller or a default measured on one other campaign, and the two are
            # worth different amounts of trust -- a plan built entirely from the second column is a
            # plan for somebody else's machine.
            basis={
                "devices": "detected" if machine else "declared by the caller",
                "minutes_per_batch": (
                    "caller" if minutes_per_batch != 95.0 else "default, measured once on a "
                    "20,000-molecule batch with two docking engines and a redock tier; no device recorded"
                ),
                "unique_per_generator_hour": (
                    "caller" if unique_per_generator_hour != 4100.0 else "default, measured once for "
                    "FLOWR at 87% uniqueness; no device recorded"
                ),
            },
        )
