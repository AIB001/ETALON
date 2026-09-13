"""Describe a protein-ligand pose by what touches what, at the distance it touches it.

``findings/0006`` says rescoring existing poses is the largest single lever available to a campaign --
eight engineer-hours worth the same as ten times the compute budget. This is the half of that which
reads the pose.

A rescorer has to see the complex, and that is the whole reason it can beat a ligand-only model. The
surrogate in :mod:`etalon.learn.surrogate` knows a molecule's size, lipophilicity and substructure and
nothing about the protein; it reaches 0.569 pIC50 on a scaffold-grouped split and cannot in principle
do better on a molecule whose only problem is that it does not fit. Docking knows the complex and
scores it with a function fitted decades ago. A learned function over the same geometry is the gap
between those two.

The featurisation is the established grid-free one: count protein-ligand atom pairs by element type
within distance shells. It is what RF-Score and its descendants use, and it is chosen here over a 3D
grid CNN for three reasons that matter more than accuracy.

It is **rotation and translation invariant by construction**, so the model cannot learn the arbitrary
orientation a docking program happened to produce. A grid has to be taught that, or augmented into
learning it, and a model that has partly learned it is a model whose score depends on how the pose was
written to disk.

It is **cheap enough to be a tier**. A campaign screening 750,000 poses needs the featuriser to cost
microseconds, not milliseconds, and a distance histogram over a few thousand atom pairs is arithmetic.
The measured throughput is in ``findings/0008``.

And it is **auditable**. A feature is "carbon to carbon between 3 and 4 angstroms, 41 of them", which a
medicinal chemist can disagree with. A grid channel's activation is not.

What it cannot see is worth stating. No explicit hydrogen bonding geometry, no desolvation, no
torsional strain, no water. A model over these features is learning a statistical shadow of those
things from whatever correlates with them in the training set, which is why it needs to be scored on a
scaffold-grouped split like everything else here.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Element types counted separately. Nine covers the protein side and the ligand side of almost every
#: drug-like complex; everything else is pooled into "other" rather than dropped, because a silently
#: ignored selenium is a feature vector that describes a different molecule.
ELEMENTS: tuple[str, ...] = ("C", "N", "O", "S", "P", "F", "Cl", "Br", "I")

#: Distance shell edges in angstroms. The first starts at 2.0 rather than 0 because anything closer in
#: a docked pose is a clash rather than a contact, and a clash belongs in a validity check. 12 is where
#: a pairwise count stops describing a binding site and starts counting the protein.
#:
#: The outer shell dominates numerically and a caller needs to know it. Measured on real poses, carbon
#: to carbon between 8 and 12 A averages about 2,577 counts per pose while the informative 3 to 5 A
#: contacts are in the tens. On raw counts a linear model is determined almost entirely by the far
#: shell, which is a burial descriptor rather than an interaction one. That is not a reason to drop it
#: -- burial is real and predictive -- but it is the reason :class:`etalon.rescore.model.Rescorer`
#: standardises its inputs rather than consuming counts directly.
SHELLS: tuple[float, ...] = (2.0, 3.0, 4.0, 5.0, 6.0, 8.0, 12.0)

#: Heavy atoms only. A docked pose's hydrogens are placed by whatever added them, so a feature that
#: counts them is partly a feature about the preparation tool.
_HYDROGEN = 1


def feature_names() -> tuple[str, ...]:
    """Every feature, named, in the order :func:`featurize_pose` produces them."""

    kinds = (*ELEMENTS, "other")
    return tuple(
        f"{ligand}-{protein}@{SHELLS[index]:g}-{SHELLS[index + 1]:g}A"
        for ligand in kinds
        for protein in kinds
        for index in range(len(SHELLS) - 1)
    )


@dataclass(frozen=True, slots=True)
class Receptor:
    """Protein heavy atoms, parsed once and reused for every pose.

    Held separately because a campaign rescores thousands of poses against one receptor, and reparsing
    a PDB per pose would make the featuriser's cost the parser's cost.
    """

    coordinates: Any
    element_index: Any
    path: Path
    atoms: int

    @property
    def provenance(self) -> dict[str, object]:
        return {"receptor": str(self.path), "heavy_atoms": self.atoms}


def _element_slot(symbol: str) -> int:
    try:
        return ELEMENTS.index(symbol)
    except ValueError:
        return len(ELEMENTS)


def radius_for(poses: Sequence[Pose], centre: Sequence[float]) -> float:
    """The smallest receptor radius that makes truncation lossless for these poses.

    The ligand's furthest atom from the centre, plus the outermost shell, plus a little. Any protein
    atom outside that distance is further than ``SHELLS[-1]`` from every ligand atom and therefore
    contributes zero to every count -- which is the property the truncation was wrongly assumed to
    have at an arbitrary radius.
    """

    import numpy as np

    if not poses:
        return float(SHELLS[-1])
    origin = np.asarray(centre, dtype=np.float32)
    extent = max(
        float(np.linalg.norm(pose.coordinates - origin, axis=1).max()) for pose in poses
    )
    return extent + float(SHELLS[-1]) + 0.5


def load_receptor(path: str | Path, *, pocket_centre: Sequence[float] | None = None, radius: float = 30.0) -> Receptor:
    """Read a receptor's heavy atoms, optionally keeping only a sphere around the site.

    Args:
        pocket_centre: Keep only atoms within ``radius`` of this point. Worth doing -- a kinase domain
            is a few thousand atoms and a binding site a few hundred, and the pair loop is the whole
            cost -- but only correct when the radius is large enough, and getting that wrong is silent.
        radius: Must cover the ligand's own extent from the centre *plus* the outermost shell. An
            earlier version of this function documented truncation as changing nothing, on the
            reasoning that atoms beyond the last shell contribute zero. That is true of atoms beyond
            the last shell measured from a *ligand atom*, not from the pocket centre: measured here, a
            radius of 18 A against a 12 A outer shell changed feature counts by up to 17, because a
            ligand atom near the edge of the site has protein atoms 8 to 12 A away that sit outside the
            sphere. Use :func:`radius_for` rather than choosing a number.

    Raises:
        ValueError: If the receptor ends up empty, because an empty receptor gives every pose the same
            all-zero features and a model scores those without complaint.
    """

    import numpy as np

    target = Path(path).expanduser().resolve()
    if not target.is_file():
        raise FileNotFoundError(f"no receptor at {target}")

    coordinates: list[tuple[float, float, float]] = []
    slots: list[int] = []
    for line in target.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.startswith(("ATOM", "HETATM")):
            continue
        symbol = (line[76:78].strip() or line[12:16].strip()[:1]).capitalize()
        if symbol in ("H", "D"):
            continue
        try:
            point = (float(line[30:38]), float(line[38:46]), float(line[46:54]))
        except ValueError:
            continue
        coordinates.append(point)
        slots.append(_element_slot(symbol))

    if not coordinates:
        raise ValueError(
            f"{target} yielded no heavy atoms. A PDB whose ATOM records this parser cannot read would "
            "otherwise produce an all-zero feature vector, which a model scores without complaint."
        )
    points = np.asarray(coordinates, dtype=np.float32)
    indices = np.asarray(slots, dtype=np.int64)
    if pocket_centre is not None:
        centre = np.asarray(pocket_centre, dtype=np.float32)
        keep = np.linalg.norm(points - centre, axis=1) <= radius
        points, indices = points[keep], indices[keep]
        if not len(points):
            raise ValueError(
                f"no receptor atoms within {radius} A of {tuple(float(v) for v in centre)}. Check the "
                "pocket centre: an empty receptor gives every pose the same all-zero features."
            )
    return Receptor(coordinates=points, element_index=indices, path=target, atoms=int(len(points)))


@dataclass(frozen=True, slots=True)
class Pose:
    """One ligand pose: heavy-atom coordinates and element slots."""

    coordinates: Any
    element_index: Any
    parent_id: str = ""
    atoms: int = 0


def read_pose(molblock: str, parent_id: str = "") -> Pose | None:
    """Parse a pose from a molblock, or ``None`` when it does not parse.

    ``None`` rather than an exception: a campaign rescoring 750,000 poses will meet some that do not
    parse, and the caller needs to count them rather than handle them one at a time. What must not
    happen is a zero vector standing in for one, which a model would score confidently.
    """

    import numpy as np
    from rdkit import Chem, RDLogger

    RDLogger.DisableLog("rdApp.*")
    molecule = Chem.MolFromMolBlock(molblock, removeHs=True, sanitize=False)
    if molecule is None or molecule.GetNumConformers() == 0:
        return None
    conformer = molecule.GetConformer()
    points: list[tuple[float, float, float]] = []
    slots: list[int] = []
    for atom in molecule.GetAtoms():
        if atom.GetAtomicNum() == _HYDROGEN:
            continue
        position = conformer.GetAtomPosition(atom.GetIdx())
        points.append((position.x, position.y, position.z))
        slots.append(_element_slot(atom.GetSymbol()))
    if not points:
        return None
    return Pose(
        coordinates=np.asarray(points, dtype=np.float32),
        element_index=np.asarray(slots, dtype=np.int64),
        parent_id=parent_id,
        atoms=len(points),
    )


def featurize_pose(pose: Pose, receptor: Receptor) -> Any:
    """One pose's contact histogram: ligand element by protein element by distance shell.

    Vectorised over the full pair matrix rather than looped, because the loop is the whole cost and a
    restricted receptor makes the matrix small enough to hold.
    """

    import numpy as np

    kinds = len(ELEMENTS) + 1
    shells = len(SHELLS) - 1
    counts = np.zeros((kinds, kinds, shells), dtype=np.float32)

    distances = np.linalg.norm(
        pose.coordinates[:, None, :] - receptor.coordinates[None, :, :], axis=2
    )
    # digitize returns 0 for anything below the first edge and len(SHELLS) for anything above the
    # last; both are dropped. Below the first edge is a clash and belongs in a validity check.
    shell_index = np.digitize(distances, SHELLS) - 1
    inside = (shell_index >= 0) & (shell_index < shells)
    if not inside.any():
        return counts.reshape(-1)

    ligand_rows, protein_columns = np.nonzero(inside)
    np.add.at(
        counts,
        (
            pose.element_index[ligand_rows],
            receptor.element_index[protein_columns],
            shell_index[ligand_rows, protein_columns],
        ),
        1.0,
    )
    return counts.reshape(-1)


def featurize_poses_on_gpu(
    poses: Sequence[Pose],
    receptor: Receptor,
    *,
    device: str = "auto",
    pose_chunk: int = 256,
) -> tuple[Any, tuple[str, ...]]:
    """The same histogram, computed on the GPU, which is where the cost actually is.

    Measured before this existed: the contact-histogram model runs at about 6.8 million poses per
    second on a 4090 and 3.1 million on the CPU, while the NumPy featuriser above manages 915 poses
    per second on one core. The model was never the bottleneck -- it is three thousand times faster
    than the thing feeding it -- so putting the *model* on a GPU was the wrong half to accelerate. The
    arithmetic that matters is a pairwise distance matrix between every ligand atom and every pocket
    atom, binned, and that is what a GPU is for.

    The poses are padded into one batched tensor per chunk. An earlier version looped pose by pose
    inside the chunk on the reasoning that padding and masking would cost more attention than the loop
    cost time; measured, the loop was the bottleneck -- 2.4 times faster than NumPy where the batched
    form reaches far more, because a 28-atom ligand against 1,254 pocket atoms is a kernel launch
    wrapped around almost no arithmetic. Ligands differ in atom count, so the padding is masked; the
    mask is one comparison and the claim it was expensive was an assumption.

    Falls back to the NumPy path when no CUDA device is present, and the result is bit-identical
    either way -- verified on real poses, maximum difference zero, and on distances placed exactly on
    a shell edge, which is the case where the two libraries' binning conventions differ and where an
    earlier version of this function silently disagreed with the NumPy one.
    """

    import numpy as np

    try:
        import torch
    except ImportError:
        return featurize_poses(poses, receptor)

    resolved = device if device != "auto" else ("cuda" if torch.cuda.is_available() else "cpu")
    if resolved == "cpu":
        return featurize_poses(poses, receptor)
    if not poses:
        return np.zeros((0, len(feature_names())), dtype=np.float32), ()

    kinds = len(ELEMENTS) + 1
    shells = len(SHELLS) - 1
    edges = torch.as_tensor(SHELLS, dtype=torch.float32, device=resolved)
    protein_points = torch.as_tensor(receptor.coordinates, device=resolved)
    protein_slots = torch.as_tensor(receptor.element_index, device=resolved)

    width = kinds * kinds * shells
    rows = torch.zeros((len(poses), width), dtype=torch.float32, device=resolved)
    protein_offsets = protein_slots * shells

    for start in range(0, len(poses), pose_chunk):
        chunk = poses[start : start + pose_chunk]
        longest = max(pose.atoms for pose in chunk)
        batch = len(chunk)

        # Padded coordinates sit far from the protein rather than at the origin, so a padded row
        # lands outside the outermost shell and is dropped by the same comparison that drops a distant
        # real atom. The mask below is belt and braces; without the offset a pad at (0,0,0) could fall
        # inside a shell of a protein that happens to straddle the origin.
        points = torch.full((batch, longest, 3), 1e4, dtype=torch.float32, device=resolved)
        slots = torch.zeros((batch, longest), dtype=torch.long, device=resolved)
        valid = torch.zeros((batch, longest), dtype=torch.bool, device=resolved)
        for offset, pose in enumerate(chunk):
            count = pose.atoms
            points[offset, :count] = torch.as_tensor(pose.coordinates, device=resolved)
            slots[offset, :count] = torch.as_tensor(pose.element_index, device=resolved)
            valid[offset, :count] = True

        distances = torch.cdist(points, protein_points.expand(batch, -1, -1))
        # ``right=True`` is what matches ``np.digitize``'s default, and the two spellings are
        # opposites: numpy's ``right=False`` means ``bins[i-1] <= x < bins[i]`` while torch's
        # ``right=False`` means ``boundaries[i-1] < x <= boundaries[i]``. Without this the GPU put
        # every distance landing exactly on a shell edge one shell lower, and dropped a distance of
        # exactly 2.0 A entirely. Float32 coordinates make an exact hit rare, which is why the
        # bit-identity check on 28 real poses passed -- the paths agreed on that sample and did not
        # agree as a rule, which is the weaker of the two claims this file makes.
        shell = torch.bucketize(distances, edges, right=True) - 1
        inside = (shell >= 0) & (shell < shells) & valid.unsqueeze(-1)
        if not bool(inside.any()):
            continue

        batch_rows, ligand_rows, protein_columns = torch.nonzero(inside, as_tuple=True)
        flat = (
            slots[batch_rows, ligand_rows] * (kinds * shells)
            + protein_offsets[protein_columns]
            + shell[batch_rows, ligand_rows, protein_columns]
        )
        # One scatter over the whole chunk: the destination index carries the pose as well as the
        # feature, so a chunk of 256 poses is one kernel rather than 256.
        rows.view(-1).scatter_add_(
            0,
            batch_rows * width + start * width + flat,
            torch.ones_like(flat, dtype=torch.float32),
        )
    return rows.cpu().numpy(), tuple(pose.parent_id for pose in poses)


def featurize_poses(
    poses: Sequence[Pose],
    receptor: Receptor,
) -> tuple[Any, tuple[str, ...]]:
    """A feature matrix over poses, with the ids that survived, in matrix order."""

    import numpy as np

    if not poses:
        return np.zeros((0, len(feature_names())), dtype=np.float32), ()
    rows = [featurize_pose(pose, receptor) for pose in poses]
    return np.stack(rows), tuple(pose.parent_id for pose in poses)


__all__ = [
    "ELEMENTS",
    "SHELLS",
    "Pose",
    "Receptor",
    "feature_names",
    "featurize_pose",
    "featurize_poses",
    "featurize_poses_on_gpu",
    "load_receptor",
    "radius_for",
    "read_pose",
]
