"""Structure-based docking adapters and the 3D preparation two of them share.

Three engines run inside one tier -- Uni-Dock, KarmaDock and GNINA.  Uni-Dock
and GNINA both search a box for a pose, and a consensus between two searches
means nothing unless they start from the *same* geometry, so conformer
generation is a separate stage that runs once and each of them binds its output
as evidence rather than embedding its own ligands.

KarmaDock is deliberately outside that arrangement.  It takes SMILES and
predicts a pose rather than searching for one, so it generates its own
conformers internally and takes no box at all; handing it the shared table
would mean pretending it used geometry it never read.  What the three do share
is the target -- one receptor, one digest -- which is the part a consensus
actually depends on.

Every engine here is an isolated command-line program with its own environment,
for the same reason AiZynthFinder is: KarmaDock pins ``rdkit==2022.09.1``
against this project's ``rdkit>=2024.9``, Uni-Dock and GNINA are compiled
binaries, and an out-of-process adapter cannot re-score, re-rank, or otherwise
reach back into the tiers above it.
"""

from __future__ import annotations

from molcascade.plugins.builtin.docking.gnina import GninaConfig, GninaPlugin
from molcascade.plugins.builtin.docking.karmadock import KarmaDockConfig, KarmaDockPlugin
from molcascade.plugins.builtin.docking.ligand_prep import (
    LigandConformerConfig,
    RDKitLigandConformerPlugin,
)
from molcascade.plugins.builtin.docking.unidock import UniDockConfig, UniDockPlugin

__all__ = [
    "GninaConfig",
    "GninaPlugin",
    "KarmaDockConfig",
    "KarmaDockPlugin",
    "LigandConformerConfig",
    "RDKitLigandConformerPlugin",
    "UniDockConfig",
    "UniDockPlugin",
]
