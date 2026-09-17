#!/usr/bin/env python3
"""Measure a real council of language models, and find out whether it may sit.

``etalon.council.reliability`` refuses a council that has not been shown to discriminate. This
script is the measurement that ruling needs, run against the ``claude`` CLI rather than against a
scripted double, because a qualification threshold that has only ever been exercised on fixtures is
a threshold nobody knows the height of.

The task is the one the council actually has jurisdiction over. ``F_COORDINATES_ARE_A_DEPICTION``
fires on a handoff record whose coordinates were rebuilt from SMILES rather than taken from a pose
-- measured on propranolol out of MolCascade's shortlist exporter: 19 heavy atoms, zero explicit
hydrogens, every z exactly 0.00. The deterministic check reads ``coordinate_source`` and is done.
This experiment removes that field from both seats, which is the situation the council is for: a
producer that did not declare its origin, or declared it wrongly.

Two seats, and their evidence is disjoint by construction:

``geometry-reader``    the molblock's coordinate statistics and the molecule's identity. It can see
                       a flat structure and nothing about where the file came from.
``provenance-reader``  which tier emitted the row, the revision, whether a receptor was named. It
                       can see that a shortlist exporter produced a row a docking tier should have
                       and nothing about the coordinates.

Both are genuinely informative and neither is sufficient, which is the arrangement
``council.seat.charter`` exists to require. What comes out is whatever comes out: a council that
fails to qualify here is a finding about these seats on this task, and is recorded as one.

    python tools/measure_council.py --records 24 --workers 8
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from etalon.council import Evidence, Seat, Vote, charter, composition, rule  # noqa: E402
from etalon.judgment.advisor import Advisory, ClaudeCli  # noqa: E402

#: Real drug-like SMILES, so a seat reading chemistry is reading something plausible rather than a
#: string this script invented to be guessable.
MOLECULES = [
    ("propranolol", "CC(C)NCC(O)COc1cccc2ccccc12", 19),
    ("atenolol", "CC(C)NCC(O)COc1ccc(CC(N)=O)cc1", 20),
    ("ibuprofen", "CC(C)Cc1ccc(C(C)C(=O)O)cc1", 15),
    ("naproxen", "COc1ccc2cc(C(C)C(=O)O)ccc2c1", 17),
    ("celecoxib", "Cc1ccc(-c2cc(C(F)(F)F)nn2-c2ccc(S(N)(=O)=O)cc2)cc1", 26),
    ("imatinib-core", "Cc1ccc(Nc2nccc(-c3cccnc3)n2)cc1", 21),
    ("gefitinib-core", "COc1cc2ncnc(Nc3ccc(F)c(Cl)c3)c2cc1OC", 24),
    ("sildenafil-core", "CCCc1nn(C)c2c1nc(-c1cc(S(=O)(=O)N)ccc1OCC)[nH]c2=O", 30),
    ("warfarin", "CC(=O)CC(c1ccccc1)c1c(O)c2ccccc2oc1=O", 24),
    ("diclofenac", "OC(=O)Cc1ccccc1Nc1c(Cl)cccc1Cl", 19),
    ("losartan-core", "CCCCc1nc(Cl)c(CO)n1Cc1ccc(-c2ccccc2)cc1", 24),
    ("metoprolol", "COCCc1ccc(OCC(O)CNC(C)C)cc1", 19),
]


def _record(name: str, smiles: str, heavy: int, *, depiction: bool, rng: random.Random) -> dict:
    """One handoff row, with the field the deterministic check reads deliberately absent.

    A depiction has every z at zero because that is what rebuilding from a 2-D drawing produces --
    the measured case, not a stylised one. A pose has a real spread in all three axes.
    """

    if depiction:
        z_min = z_max = 0.0
        z_spread = 0.0
        producer = "shortlist.export@0.3.1"
        conformer_energy = None
    else:
        z_min = round(rng.uniform(-6.5, -1.0), 2)
        z_max = round(z_min + rng.uniform(3.0, 9.0), 2)
        z_spread = round(z_max - z_min, 2)
        producer = "docking.unidock@1.2.0"
        conformer_energy = round(rng.uniform(-9.8, -6.1), 2)

    return {
        "chemistry": {
            "name": name,
            "smiles": smiles,
            "heavy_atom_count": heavy,
            "explicit_hydrogen_count": 0 if depiction else rng.randint(10, 22),
            "coordinate_statistics": {
                "z_min": z_min,
                "z_max": z_max,
                "z_spread": z_spread,
                "atoms_with_coordinates": heavy,
            },
        },
        "provenance": {
            "emitted_by": producer,
            "revision_id": f"rev-{rng.randint(1000, 9999)}",
            "receptor_id": None if depiction else f"sha256:{rng.getrandbits(64):016x}",
            "docking_score_present": not depiction,
            "conformer_energy_kcal_mol": conformer_energy,
            "tier": "shortlist" if depiction else "docking",
        },
    }


GEOMETRY_BRIEF = (
    "You see only the molecule and statistics over the coordinates in its structure file. "
    "Coordinates rebuilt from a 2-D drawing are flat: every z is identical, usually exactly zero. "
    "Coordinates from a real three-dimensional pose are spread across all three axes."
)
PROVENANCE_BRIEF = (
    "You see only which stage emitted this row and what it carries. You cannot see any "
    "coordinates. Consider whether the producer named here is one that computes three-dimensional "
    "structure, or one that exports a list and rebuilds geometry from a name."
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=int, default=24)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--model", default="sonnet")
    parser.add_argument("--seed", type=int, default=20260917)
    parser.add_argument("--out", default="findings/0012-the-council-measured.json")
    arguments = parser.parse_args()

    rng = random.Random(arguments.seed)
    half = arguments.records // 2
    truth: list[bool] = []
    records: list[dict] = []
    for index in range(arguments.records):
        name, smiles, heavy = MOLECULES[index % len(MOLECULES)]
        depiction = index < half
        records.append(_record(name, smiles, heavy, depiction=depiction, rng=rng))
        truth.append(depiction)

    order = list(range(len(records)))
    rng.shuffle(order)
    records = [records[i] for i in order]
    truth = [truth[i] for i in order]

    seats = charter(
        [
            Seat(
                "geometry-reader",
                Advisory(ClaudeCli(model=arguments.model), f"claude-{arguments.model}"),
                frozenset({Evidence.CHEMISTRY}),
                brief=GEOMETRY_BRIEF,
            ),
            Seat(
                "provenance-reader",
                Advisory(ClaudeCli(model=arguments.model), f"claude-{arguments.model}"),
                frozenset({Evidence.PROVENANCE}),
                brief=PROVENANCE_BRIEF,
            ),
        ]
    )

    from etalon.council.convene import _ask

    jobs = [(seat, index) for seat in seats for index in range(len(records))]
    print(
        f"{len(jobs)} calls: {len(seats)} seats x {len(records)} records "
        f"({half} depictions, {len(records) - half} poses)",
        file=sys.stderr,
    )

    def run(job):  # noqa: ANN001, ANN202
        seat, index = job
        ballot = _ask(seat, "F_COORDINATES_ARE_A_DEPICTION", records[index])
        print(
            f"  {seat.name:<19} #{index:<3} {ballot.vote.value:<8}"
            f"{' ERROR ' + ballot.error[:60] if ballot.error else ''}",
            file=sys.stderr,
        )
        return seat.name, index, ballot

    with ThreadPoolExecutor(max_workers=arguments.workers) as pool:
        results = list(pool.map(run, jobs))

    votes: dict[str, list[Vote]] = {seat.name: [Vote.ABSTAIN] * len(records) for seat in seats}
    errors: dict[str, int] = {seat.name: 0 for seat in seats}
    for name, index, ballot in results:
        votes[name][index] = ballot.vote
        if ballot.error:
            errors[name] += 1

    ruling = rule(
        votes,
        truth,
        labels_described=(
            f"{len(records)} synthetic md_system_input rows, {half} with coordinates rebuilt from "
            "SMILES (every z exactly 0.00, emitted by a shortlist exporter) and "
            f"{len(records) - half} from real docked poses. coordinate_source withheld from both "
            "seats, which is the case the deterministic check cannot rule on."
        ),
    )
    print("\n" + ruling.render())

    payload = {
        "finding": (
            "A two-seat council of claude-"
            + arguments.model
            + " advisors with disjoint evidence was measured against labelled handoff records and "
            + ("QUALIFIED" if ruling.qualified else "was REFUSED")
            + "."
        ),
        "measured_on": datetime.now(UTC).date().isoformat(),
        "what_was_run": {
            "model": f"claude-{arguments.model}",
            "transport": "claude CLI, one-shot (-p), no session carried between questions",
            "seats": composition(seats)["seats"],
            "records": len(records),
            "depictions": half,
            "poses": len(records) - half,
            "calls": len(jobs),
            "seed": arguments.seed,
            "fault": "F_COORDINATES_ARE_A_DEPICTION",
            "withheld_from_both_seats": "coordinate_source",
        },
        "result": ruling.as_dict(),
        "rendered": ruling.render(),
        "transport_errors": errors,
        "what_this_does_not_show": [
            "That the council is right about real MolCascade output. These records were built by "
            "this script with a known answer, so they are cleaner than a real handoff row and the "
            "signal in them is stronger than a campaign would see.",
            "That either seat generalises. A seat qualified on this fault has established nothing "
            "about any other cause in the taxonomy, and reliability is per task.",
            "Anything about a council of more than two seats, or of models other than the one "
            "named above.",
        ],
    }
    out = Path(arguments.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"\nwritten to {out}", file=sys.stderr)
    return 0 if ruling.qualified else 1


if __name__ == "__main__":
    raise SystemExit(main())
