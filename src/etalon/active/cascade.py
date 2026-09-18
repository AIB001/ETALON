"""Execute a versioned single component or custom cascade as a learnable endpoint.

Each query runs on ONLY the selected molecule in an action-specific workspace. This intentionally
simple bridge is suitable for pilots; it does not pretend to amortize large GPU batches or to
search arbitrary graphs automatically. New recipes can be registered between learning rounds.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import stat
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from etalon.active.schema import Action, Candidate, Endpoint, Evaluation, canonical, digest
from etalon.authority.grant import SpendAuthorization
from etalon.boundary.infra import load
from etalon.boundary.screen import Screen


@dataclass(frozen=True)
class Readout:
    stage_id: str
    contract_id: str
    value_column: str
    filters: dict[str, Any] = field(default_factory=dict)


def _file_hash(path: Path) -> str:
    if not stat.S_ISREG(path.stat().st_mode):
        raise OSError(f"protocol input is not a regular file: {path}")
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            sha.update(chunk)
    return sha.hexdigest()


def _absolute_input_path(path: str | Path) -> Path:
    """Normalize a path name without replacing a symlink with its current target.

    Scientific plugins read the configured name. Pinning only its resolved target would
    miss a later symlink retarget while continuing to hash the old, unchanged file.
    """
    return Path(os.path.abspath(path))  # noqa: PTH100 - resolving symlinks would break input identity.


def _configuration_input_paths(configuration: Mapping[str, Any]) -> set[Path]:
    paths: set[Path] = set()

    def inspect(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if (key.endswith("_path") or key == "path") and isinstance(item, str) and item:
                    path = Path(item)
                    if not path.is_absolute():
                        raise ValueError(f"protocol input {key} must be an absolute path")
                    paths.add(_absolute_input_path(path))
                inspect(item)
        elif isinstance(value, list):
            for item in value:
                inspect(item)

    inspect({key: value for key, value in configuration.items() if key not in {"library", "ingest"}})
    return paths


@dataclass(frozen=True)
class CascadeRecipe:
    configuration: str
    readout_json: str
    infrastructure_commit: str
    input_files: tuple[tuple[str, str], ...] = ()

    @classmethod
    def freeze(cls, config: Mapping[str, Any], readout: Readout, *,
               files: tuple[str | Path, ...] = ()) -> CascadeRecipe:
        """Pin config, explicit readout, infrastructure and declared external file contents.

        Absolute *_path/path entries are pinned automatically. Additional weights or other
        scientific resources not named this way must be supplied in ``files``. A changed file
        requires a newly frozen recipe and a NEW endpoint id, not relabelling old scores.
        """
        infra = load("molcascade")
        from molcascade.cascade.models import CascadeConfig

        validated = CascadeConfig.model_validate(dict(config)).model_dump(mode="json")
        if validated["library"].get("path") is not None:
            raise ValueError("active recipes take their library from selected actions; leave library.path unset")
        if not all((readout.stage_id, readout.contract_id, readout.value_column)):
            raise ValueError("readout stage, contract and value column must be explicit")
        paths = {_absolute_input_path(path) for path in files} | _configuration_input_paths(validated)
        pinned = tuple((str(path), _file_hash(path)) for path in sorted(paths))
        return cls(canonical(validated), canonical(asdict(readout)), infra.source_commit, pinned)

    @property
    def protocol_id(self) -> str:
        return "molcascade-recipe/1:" + digest(asdict(self))

    def endpoint(self, identifier: str, *, target: str, quantity: str, units: str,
                 cost: float, direction: str = "minimize", noise: float = 0.1) -> Endpoint:
        return Endpoint(identifier, target, quantity, units, self.protocol_id, cost,
                        direction=direction, noise=noise, requires_handoff=False)


class CascadeExecutor:
    """A dispatch table of explicitly authorized recipe endpoints, never the default cascade."""

    def __init__(self, workspace: str | Path, recipes: Mapping[str, CascadeRecipe]) -> None:
        self.workspace = Path(workspace).resolve()
        self.recipes = dict(recipes)

    @classmethod
    def from_journal(cls, workspace: str | Path, store: Any) -> CascadeExecutor:
        """Rehydrate explicitly bound recipes without launching a tool or relaxing trial limits.

        A reconstructed dispatcher may include retired recipes for historical inspection;
        CampaignStore.reserve and the controller enforce their query eligibility.
        """
        from etalon.active.protocols import ProtocolRegistry

        return cls(workspace, ProtocolRegistry(store).recipes())

    def register(self, endpoint: Endpoint, recipe: CascadeRecipe) -> None:
        if endpoint.protocol != recipe.protocol_id:
            raise ValueError("endpoint does not identify this recipe")
        if endpoint.id in self.recipes and self.recipes[endpoint.id] != recipe:
            raise ValueError("an existing endpoint recipe cannot change; use a new endpoint id")
        self.recipes[endpoint.id] = recipe

    def __call__(self, action: Action, candidate: Candidate, endpoint: Endpoint,
                 grant: SpendAuthorization | None) -> Evaluation:
        if (not isinstance(action.id, str) or not action.id or action.id in {".", ".."}
                or any(char in action.id for char in ("/", "\\", "\x00"))):
            raise ValueError("action id must identify one nonempty workspace path component")
        if (action.candidate_id != candidate.id or action.endpoint_id != endpoint.id
                or type(action.round_id) is not int or action.round_id < 1):
            raise ValueError("action candidate, endpoint and live round must match the requested execution")
        if type(action.replicate) is not int or action.replicate != 0:
            raise ValueError("a fixed-seed cascade action cannot claim an independent replica")
        recipe = self.recipes[endpoint.id]
        if endpoint.protocol != recipe.protocol_id or endpoint.requires_handoff or grant is not None:
            raise ValueError("cascade endpoint protocol/authorization mismatch")
        if endpoint.max_replicates != 1:
            raise ValueError("fixed-seed cascades are not independent replicas; provide a replica-aware executor")
        if load("molcascade").source_commit != recipe.infrastructure_commit:
            raise ValueError("MolCascade infrastructure changed after recipe registration")
        identity = {"mode": "live_molcascade", "protocol_id": recipe.protocol_id,
                    "action_id": action.id, "candidate_id": candidate.id}

        def changed_input(files: tuple[tuple[str, str], ...]) -> str | None:
            for name, expected in files:
                try:
                    if _file_hash(Path(name)) == expected:
                        continue
                except OSError:
                    pass
                return name
            return None

        changed = changed_input(recipe.input_files)
        if changed:
            return Evaluation(candidate.id, endpoint.id, None, endpoint.units, 0.0, status="blocked",
                              provenance={**identity, "error": "protocol input changed or missing", "path": changed,
                                          "failure_code": "PROTOCOL_INPUT_CHANGED"})
        from etalon.active.graph import execution_lineage, inspect_recipe, verify_execution_plan

        try:
            graph = inspect_recipe(recipe)
        except (ValueError, OSError, ImportError) as error:
            return Evaluation(candidate.id, endpoint.id, None, endpoint.units, 0.0, status="blocked",
                              provenance={**identity, "error": str(error), "failure_code": "PROTOCOL_COMPILE_INVALID"})
        workspace = self.workspace / action.id
        workspace.mkdir(parents=True, exist_ok=False)
        config_path, library = workspace / "cascade.json", workspace / "selected.csv"
        config_path.write_text(recipe.configuration, encoding="utf-8")
        with library.open("x", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["id", "smiles"])
            writer.writerow([candidate.id, candidate.smiles])
        inputs = (*recipe.input_files, (str(library), _file_hash(library)))
        screen = Screen(workspace)
        try:
            plan = screen.plan(config_path, library)
            verified = verify_execution_plan(recipe, graph, plan, library)
        except (ValueError, OSError, ImportError, KeyError) as error:
            return Evaluation(candidate.id, endpoint.id, None, endpoint.units, 0.0, status="blocked",
                              provenance={**identity, "error": str(error), "failure_code": "PROTOCOL_PLAN_MISMATCH"})
        changed = changed_input(inputs)
        if changed:
            return Evaluation(candidate.id, endpoint.id, None, endpoint.units, 0.0, status="blocked",
                              provenance={**identity, "error": "protocol input changed before dispatch", "path": changed,
                                          "failure_code": "PROTOCOL_INPUT_CHANGED"})
        result = screen.run(plan, run_id="query-" + action.id)
        provenance = {**identity,
                      "plan": plan.as_dict(), "run": result.as_dict(),
                      "execution_plan_verification": verified,
                      "cost_basis": "endpoint quote; not measured", "workspace": str(workspace)}

        def missing(reason: str, code: str, *, status: str = "failed") -> Evaluation:
            return Evaluation(candidate.id, endpoint.id, None, endpoint.units, endpoint.cost,
                              status=status, provenance={**provenance, "error": reason, "failure_code": code})

        if result.revision_id != plan.revision_id or result.run_id != "query-" + action.id:
            return missing("execution result does not identify the verified plan/action", "PROTOCOL_PLAN_MISMATCH", status="invalid")
        try:
            provenance["evidence_graph"] = execution_lineage(graph, result.stages)
        except ValueError as error:
            return missing(str(error), "PROTOCOL_GRAPH_MISMATCH", status="invalid")
        changed = changed_input(inputs)
        if changed:
            return missing(f"protocol input changed or missing after execution: {changed}",
                           "PROTOCOL_INPUT_CHANGED", status="invalid")

        if result.failed:
            return missing("one or more recipe components failed", "CASCADE_STAGE_FAILED")
        if (result.status != "SUCCEEDED"
                or not all(node["available"] for node in provenance["evidence_graph"]["stages"])):
            return missing("the requested recipe did not report complete successful execution",
                           "CASCADE_INCOMPLETE", status="invalid")
        parents: dict[str, str] = {}
        for stage in result.committed:
            try:
                parent_rows = screen.read(stage.artifact_id, contract_id="parent/v1")
            except KeyError:
                continue
            except Exception as error:
                return missing(f"{type(error).__name__}: {error}", "ARTIFACT_READ_FAILED", status="invalid")
            stage_parents: set[str] = set()
            for row in parent_rows:
                identifier, smiles = row.get("parent_id"), row.get("parent_smiles")
                if not isinstance(identifier, str) or not identifier or not isinstance(smiles, str) or not smiles:
                    return missing("registered parent identity or SMILES is absent/invalid", "CHEMICAL_STATE_MISMATCH")
                if identifier in stage_parents:
                    return missing("a stage emitted duplicate parent identities", "CHEMICAL_STATE_CARDINALITY")
                if identifier in parents and parents[identifier] != smiles:
                    return missing("the same parent identity has conflicting SMILES across stages", "CHEMICAL_STATE_MISMATCH")
                stage_parents.add(identifier)
                parents[identifier] = smiles
        if len(parents) != 1:
            return missing("selected input did not produce exactly one registered chemical state", "CHEMICAL_STATE_CARDINALITY")
        parent_id, smiles = next(iter(parents.items()))
        from rdkit import Chem

        expected_mol, actual_mol = Chem.MolFromSmiles(candidate.smiles), Chem.MolFromSmiles(smiles)
        if (expected_mol is None or actual_mol is None
                or Chem.MolToSmiles(expected_mol) != Chem.MolToSmiles(actual_mol)):
            return missing("registration changed the chemical state; register that state explicitly before learning",
                           "CHEMICAL_STATE_MISMATCH")
        readout = Readout(**json.loads(recipe.readout_json))
        matches = [s for s in result.committed if s.stage_id == readout.stage_id]
        if len(matches) != 1 or not provenance["evidence_graph"]["readout"]["available"]:
            return missing("readout stage is absent or ambiguous", "READOUT_STAGE_MISSING")
        try:
            rows = screen.read(matches[0].artifact_id, contract_id=readout.contract_id)
        except KeyError:
            return missing("readout contract has no rows for the selected molecule", "READOUT_CARDINALITY")
        except Exception as error:
            return missing(f"{type(error).__name__}: {error}", "ARTIFACT_READ_FAILED", status="invalid")
        rows = [r for r in rows if r.get("parent_id") == parent_id
                and all(r.get(key) == value for key, value in readout.filters.items())]
        if len(rows) != 1 or rows[0].get(readout.value_column) is None:
            return missing("readout must identify exactly one non-null value for the selected molecule", "READOUT_CARDINALITY")
        scalar = rows[0][readout.value_column]
        try:
            if isinstance(scalar, (bool, str, bytes)):
                raise ValueError("readout must be a numeric scalar, not a boolean or string")
            value = float(scalar)
        except (ValueError, TypeError, OverflowError):
            return missing("readout does not contain a valid numeric scalar", "READOUT_INVALID")
        if not math.isfinite(value) or rows[0].get("status", "OK") != "OK":
            return missing("readout value is invalid or its status is not OK", "READOUT_INVALID")
        changed = changed_input(inputs)
        if changed:
            return missing(f"protocol input changed or missing before admission: {changed}",
                           "PROTOCOL_INPUT_CHANGED", status="invalid")
        return Evaluation(candidate.id, endpoint.id, value, endpoint.units, endpoint.cost,
                          provenance={**provenance, "molcascade_parent_id": parent_id,
                                      "artifact_id": matches[0].artifact_id, "readout": asdict(readout)})
