"""Generate a self-contained, offline drag-and-drop pipeline configuration UI."""

from __future__ import annotations

import base64
import hashlib
import html
import json
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from molcascade import __version__
from molcascade.backends import (
    Availability,
    BackendStatus,
    Capability,
    create_backend_registry,
)
from molcascade.config.models import PipelineConfig
from molcascade.io.atomic import atomic_write_bytes
from molcascade.plugins.builtin.admet import DEFAULT_MODELS_REFERENCE
from molcascade.plugins.manifest import PluginKind
from molcascade.plugins.registry import PluginRegistry, create_builtin_registry
from molcascade.ui.assets import png_data_uri

_LAYERS = (
    {
        "slot": "source",
        "kind": "source",
        "title": "01  Ingest",
        "short_title": "Ingest",
        "default_id": "source",
        "color": "#5b7cfa",
        "description": (
            "Stream CSV, XLSX, SMILES, SDF, MOL2 directories, or Parquet with "
            "bounded memory."
        ),
        "policy_hint": "Exactly one enabled source starts the graph.",
    },
    {
        "slot": "standardize",
        "kind": "standardizer",
        "title": "02  Standardize",
        "short_title": "Standardize",
        "default_id": "standardize",
        "color": "#8b5cf6",
        "description": (
            "Register parent structures under a fixed identity policy — largest "
            "organic fragment kept, charges reionised, metals disconnected — and "
            "remove exact duplicates. Only the batch size is editable here; the "
            "policy itself is not reachable from this card."
        ),
        "policy_hint": (
            "Standardization establishes the identity boundary for every downstream metric."
        ),
    },
    {
        "slot": "hard_gate",
        "kind": "gate",
        "title": "03  Chemistry gates",
        "short_title": "Chemistry gates",
        "default_id": "chemistry_gate",
        "color": "#ef5f75",
        "description": (
            "Combine structural validity, chemical-stability/reactivity alerts, property "
            "windows, Rule of Five/QED, and PAINS."
        ),
        "policy_hint": (
            "Put independent criteria in parallel, then use a Policy join to combine "
            "their decisions."
        ),
    },
    {
        "slot": "featurize",
        "kind": "featurizer",
        "title": "04  Descriptors",
        "short_title": "Descriptors",
        "default_id": "descriptor",
        "color": "#2f9eeb",
        "description": (
            "Calculate physicochemical properties and fingerprints as retained evidence "
            "tables."
        ),
        "policy_hint": (
            "Multiple descriptor cards may share one parent input without filtering "
            "each other."
        ),
    },
    {
        "slot": "predict",
        "kind": "predictor",
        "title": "05  ADMET & activity",
        "short_title": "ADMET & activity",
        "default_id": "prediction",
        "color": "#00a6a6",
        "description": (
            "Run local, versioned prediction models only when their training domain is "
            "documented."
        ),
        "policy_hint": (
            "Keep endpoints as parallel evidence; join only through an explicit reviewed "
            "policy."
        ),
    },
    {
        "slot": "applicability",
        "kind": "applicability",
        "title": "06  Applicability",
        "short_title": "Applicability",
        "default_id": "applicability",
        "color": "#12b886",
        "description": (
            "Record model-domain support, uncertainty, maximum similarity to a "
            "lead/reference set, or uniqueness."
        ),
        "policy_hint": (
            "Applicability is evidence about a prediction, not a substitute for that "
            "endpoint."
        ),
    },
    {
        "slot": "synthesis",
        "kind": "synthesis",
        "title": "07  Synthesis evidence",
        "short_title": "Synthesis",
        "default_id": "synthesis",
        "color": "#37b24d",
        "description": (
            "Prioritize synthetic accessibility or imported retrosynthesis route and "
            "step-count evidence without docking."
        ),
        "policy_hint": (
            "SA scores are proxies; route feasibility and step counts require a "
            "retrosynthesis backend."
        ),
    },
    {
        "slot": "scaffold",
        "kind": "scaffolder",
        "title": "08  Scaffolds",
        "short_title": "Scaffolds",
        "default_id": "scaffold",
        "color": "#74b816",
        "description": (
            "Assign exact and generic Murcko frameworks, including an explicit acyclic "
            "channel."
        ),
        "policy_hint": "Retain scaffold assignments for diversity-aware quotas and audit reports.",
    },
    {
        "slot": "cluster",
        "kind": "clusterer",
        "title": "09  Similarity & diversity",
        "short_title": "Similarity & diversity",
        "default_id": "cluster",
        "color": "#a0b323",
        "description": (
            "Group related molecules or prioritize novelty with bounded-memory local "
            "algorithms."
        ),
        "policy_hint": "Large-library methods must avoid a dense all-pairs similarity matrix.",
    },
    {
        "slot": "select",
        "kind": "selector",
        "title": "10  Budget selection",
        "short_title": "Selection",
        "default_id": "selection",
        "color": "#f59f00",
        "description": (
            "Apply deterministic budgets, scaffold caps, and explainable backfill policies."
        ),
        "policy_hint": (
            "Selection is the final budget decision, after evidence and gate policies "
            "are explicit."
        ),
    },
    {
        "slot": "export",
        "kind": "exporter",
        "title": "11  Shortlist handoff",
        "short_title": "Handoff",
        "default_id": "shortlist",
        "color": "#667085",
        "description": (
            "Export a verified local shortlist, carrying whatever docking scores the "
            "run produced, for downstream software."
        ),
        "policy_hint": (
            "MolCascade ends at an auditable shortlist. A docking score that travels "
            "with it is evidence, not a claim of binding; MD and free-energy "
            "calculations are a later step, elsewhere."
        ),
    },
)


_CURATED_DEFAULTS: dict[str, dict[str, Any]] = {
    "source.delimited_smiles@0.1.0": {
        "path": "molecules.csv",
        "delimiter": ",",
        "has_header": True,
        "smiles_column": "smiles",
        "candidate_id_column": None,
        "metadata_columns": [],
        "batch_size": 25_000,
    },
    "source.sdf@0.1.0": {"path": "molecules.sdf", "batch_size": 25_000},
    "source.raw_molecule_parquet@0.1.0": {"path": "molecules.parquet", "batch_size": 65_536},
    "source.xlsx@0.1.0": {
        "path": "molecules.xlsx",
        "sheet_name": None,
        "has_header": True,
        "smiles_column": "smiles",
        "candidate_id_column": None,
        "metadata_columns": [],
        "batch_size": 25_000,
    },
    "source.mol2_directory@0.1.0": {
        "path": "molecules",
        "recursive": True,
        "candidate_id_mode": "RELATIVE_PATH",
        "batch_size": 25_000,
    },
    "chemistry.rdkit_standardize@0.1.0": {"batch_size": 25_000},
    "chemistry.rdkit_hard_gate@0.1.0": {"batch_size": 25_000},
    "chemistry.rdkit_property_range_gate@0.1.0": {"batch_size": 25_000},
    "features.rdkit_properties@0.1.0": {
        "batch_size": 25_000,
        "include_sa_score": False,
    },
    "features.openbabel_properties@0.1.0": {
        "batch_size": 25_000,
        "include_sa_score": False,
        "allow_copyleft_backend": False,
    },
    "features.rdkit_fingerprint@0.1.0": {
        "batch_size": 25_000,
        "kind": "morgan",
        "bit_length": 2048,
        "radius": 2,
        "include_chirality": True,
    },
    "prediction.admet_ai_v2@0.1.0": {
        "schema_version": 1,
        # Not blank. ADMET-AI v2 ships its checkpoints inside the wheel, and this
        # reference resolves to them wherever the wheel is installed -- which a
        # hand-typed absolute path does not, once the config leaves this laptop.
        # The digests below stay blank on purpose: they are the actual trust
        # decision and nobody should inherit them from a template.
        "models_dir": DEFAULT_MODELS_REFERENCE,
        "expected_model_manifest_sha256": "",
        "expected_package_code_sha256": "",
        "endpoints": [
            {
                "output_column": "Caco2_Wang",
                "endpoint_id": "admet-ai-v2/Caco2_Wang",
            }
        ],
        "batch_size": 256,
        "num_workers": 0,
        "allow_unsafe_model_deserialization": False,
        "expected_checkpoints": [],
        "max_model_files": 10_000,
        "max_model_bytes": 20 * 1024 * 1024 * 1024,
        "max_package_code_files": 10_000,
        "max_package_code_bytes": 2 * 1024 * 1024 * 1024,
    },
    "prediction.numeric_evidence_gate@0.1.0": {
        "schema_version": 1,
        "endpoint_id": "admet-ai-v2/HIA_Hou",
        "model_id": None,
        "semantics_label": (
            "Illustrative HIA probability policy; verify endpoint semantics and choose "
            "a project-specific threshold before execution."
        ),
        "minimum": 0.5,
        "maximum": None,
        "batch_size": 16_384,
    },
    "synthesis.numeric_evidence_gate@0.1.0": {
        "schema_version": 1,
        "method_id": None,
        "expected_direction": "HIGHER_HARDER",
        "minimum": None,
        "maximum": 6.0,
        "batch_size": 16_384,
    },
    "scaffold.rdkit_murcko@0.1.0": {"batch_size": 25_000, "include_chirality": False},
    "cluster.native_streaming_leader@0.1.0": {
        "batch_size": 8_192,
        "similarity_threshold": 0.65,
        "fingerprint_bits": 2_048,
        "fingerprint_radius": 2,
        "include_chirality": True,
        "max_clusters": 100_000,
    },
    "cluster.rdkit_scaffold_groups@0.1.0": {
        "batch_size": 8_192,
        "fingerprint_bits": 2_048,
        "fingerprint_radius": 2,
        "include_chirality": False,
        "max_clusters": 250_000,
    },
    "select.native_hash_budget@0.1.0": {
        "target_count": 45_000,
        "seed": 20_260_823,
        "batch_size": 25_000,
    },
    "select.native_scaffold_round_robin@0.1.0": {
        "target_count": 45_000,
        "max_per_scaffold": 25,
        "include_chirality": False,
        "seed": 20_260_823,
        "batch_size": 25_000,
    },
    "export.native_smiles_shortlist@0.1.0": {
        "filename": "shortlist.smi",
        "include_header": True,
        "batch_size": 25_000,
    },
    "export.rdkit_sdf_shortlist@0.1.0": {
        "filename": "shortlist.sdf",
        "force_v3000": False,
        "batch_size": 8_192,
    },
}


_SCIENCE_NOTES: dict[str, str] = {
    "prediction.admet_ai_v2@0.1.0": (
        "Optional local model evidence only. Inspect and pin both installed package "
        "code and the complete model tree before acknowledging Torch checkpoint trust; "
        "uncertainty and applicability are separate modules."
    ),
    "chemistry.rdkit_drug_likeness@0.1.0": (
        "Rule of Five + QED: independent thresholds, annotation only by default."
    ),
    "chemistry.rdkit_structural_alerts@0.1.0": (
        "Substructure triage flags — not experimental proof. PAINS defaults to WARN."
    ),
    "synthesis.rdkit_sa_score@0.1.0": (
        "Synthetic accessibility proxy (higher = harder) — not retrosynthesis steps."
    ),
    "prediction.numeric_evidence_gate@0.1.0": (
        "Turns one exact endpoint/model into a complete fail-closed decision stream. "
        "The sample threshold is illustrative and must be reviewed for the endpoint."
    ),
    "synthesis.numeric_evidence_gate@0.1.0": (
        "Turns one exact synthesis score into a complete fail-closed decision stream. "
        "SA ≤ 6 is a configurable project triage policy, not proof of a route."
    ),
    "applicability.rdkit_reference_similarity@0.1.0": (
        "Maximum lead similarity or uniqueness requires an explicit local reference set; "
        "the exact backend is intended for small, curated lead sets."
    ),
}


_UI_DISPLAY_NAMES: dict[str, str] = {
    "prediction.admet_ai_v2@0.1.0": "ADMET-AI v2 local endpoint panel",
    "prediction.numeric_evidence_gate@0.1.0": "Prediction endpoint threshold gate",
    "chemistry.rdkit_drug_likeness@0.1.0": "RDKit Rule of Five + QED",
    "chemistry.rdkit_structural_alerts@0.1.0": (
        "RDKit substructure triage flags"
    ),
    "synthesis.rdkit_sa_score@0.1.0": "RDKit synthetic accessibility proxy",
    "synthesis.numeric_evidence_gate@0.1.0": "Synthesis-score threshold gate",
    "applicability.rdkit_reference_similarity@0.1.0": (
        "RDKit lead similarity / uniqueness"
    ),
}


_CAPABILITY_SLOTS: dict[Capability, str] = {
    Capability.SOURCE: "source",
    Capability.STANDARDIZE: "standardize",
    Capability.GATE: "hard_gate",
    Capability.DESCRIPTORS: "featurize",
    Capability.FINGERPRINT: "featurize",
    Capability.PREDICT: "predict",
    Capability.APPLICABILITY: "applicability",
    Capability.SYNTHESIS: "synthesis",
    Capability.DOCK: "dock",
    Capability.SCAFFOLD: "scaffold",
    Capability.CLUSTER: "cluster",
    Capability.SELECT: "select",
    Capability.EXPORT: "export",
}


def _plugin_default(plugin: Any) -> dict[str, Any]:
    curated = _CURATED_DEFAULTS.get(plugin.descriptor.key)
    if curated is not None:
        return curated
    model = getattr(plugin, "config_model", None)
    if isinstance(model, type) and issubclass(model, BaseModel):
        try:
            return model().model_dump(mode="json", exclude_none=False)
        except Exception:
            return {}
    return {}


@lru_cache(maxsize=1)
def _backend_statuses() -> tuple[BackendStatus, ...]:
    """Probe once per short-lived builder process instead of once per registry view."""

    return tuple(create_backend_registry().probe_all())


def _default_pipeline(plugin_rows: list[dict[str, Any]]) -> PipelineConfig:
    """Build a compilable graph that also demonstrates retained DAG branches."""

    plugins = {row["key"]: row for row in plugin_rows}
    stages: list[dict[str, Any]] = []

    def add(
        stage_id: str,
        slot: str,
        key: str,
        *,
        inputs: list[dict[str, str]] | None = None,
    ) -> bool:
        plugin = plugins.get(key)
        if plugin is None or not plugin["selectable"]:
            return False
        stages.append(
            {
                "id": stage_id,
                "slot": slot,
                "plugin": key,
                "inputs": inputs or [],
                "config": plugin["default_config"],
                "enabled": True,
            }
        )
        return True

    def edge(
        source_stage: str,
        *,
        request_port: str = "primary",
        source_port: str = "primary",
    ) -> list[dict[str, str]]:
        return [
            {
                "request_port": request_port,
                "stage": source_stage,
                "port": source_port,
            }
        ]

    add("source", "source", "source.delimited_smiles@0.1.0")
    add(
        "standardize",
        "standardize",
        "chemistry.rdkit_standardize@0.1.0",
        inputs=edge("source"),
    )

    gate_candidates = (
        ("structural_gate", "chemistry.rdkit_hard_gate@0.1.0"),
        ("property_gate", "chemistry.rdkit_property_range_gate@0.1.0"),
        ("drug_likeness", "chemistry.rdkit_drug_likeness@0.1.0"),
        ("structural_alerts", "chemistry.rdkit_structural_alerts@0.1.0"),
    )
    join_key = next(
        (
            key
            for key, plugin in plugins.items()
            if key.startswith("policy.native_decision_join@")
            and plugin["selectable"]
        ),
        None,
    )
    installed_gates = [
        (stage_id, key)
        for stage_id, key in gate_candidates
        if key in plugins and plugins[key]["selectable"]
    ]
    gate_anchor = "standardize"
    if join_key and installed_gates:
        # The default makes the canvas semantics concrete: all chemistry
        # criteria receive the same registered parent population and their
        # complete decision streams converge at an executable ALL join.
        # Switching the tier to Serial in the browser removes this join and
        # rewrites the same nodes as a survivor chain.
        branches = list(installed_gates)
        branch_anchor = "standardize"
        for stage_id, key in branches:
            add(stage_id, "hard_gate", key, inputs=edge(branch_anchor))
        join_inputs = [
            {
                "request_port": "parents",
                "stage": branch_anchor,
                "port": "primary",
            }
        ]
        join_inputs.extend(
            {
                "request_port": f"decision_{stage_id}",
                "stage": stage_id,
                "port": "decisions",
            }
            for stage_id, _ in branches
        )
        if branches:
            add("chemistry_policy", "hard_gate", join_key, inputs=join_inputs)
            gate_anchor = "chemistry_policy"
        else:
            gate_anchor = branch_anchor
    else:
        for stage_id, key in installed_gates:
            add(stage_id, "hard_gate", key, inputs=edge(gate_anchor))
            gate_anchor = stage_id

    evidence_anchor = gate_anchor
    if add(
        "properties",
        "featurize",
        "features.rdkit_properties@0.1.0",
        inputs=edge(evidence_anchor),
    ):
        evidence_anchor = "properties"
    if add(
        "fingerprint",
        "featurize",
        "features.rdkit_fingerprint@0.1.0",
        inputs=edge(evidence_anchor),
    ):
        evidence_anchor = "fingerprint"
    if add(
        "synthetic_accessibility",
        "synthesis",
        "synthesis.rdkit_sa_score@0.1.0",
        inputs=edge(evidence_anchor),
    ):
        evidence_anchor = "synthetic_accessibility"
        if add(
            "synthesis_policy",
            "synthesis",
            "synthesis.numeric_evidence_gate@0.1.0",
            inputs=[
                {
                    "request_port": "parents",
                    "stage": "synthetic_accessibility",
                    "port": "primary",
                },
                {
                    "request_port": "synthesis_scores",
                    "stage": "synthetic_accessibility",
                    "port": "synthesis_scores",
                },
            ],
        ):
            evidence_anchor = "synthesis_policy"
    if add(
        "scaffolds",
        "scaffold",
        "scaffold.rdkit_murcko@0.1.0",
        inputs=edge(evidence_anchor),
    ):
        evidence_anchor = "scaffolds"
    diversity_anchor = evidence_anchor
    if add(
        "diversity_groups",
        "cluster",
        "cluster.rdkit_scaffold_groups@0.1.0",
        inputs=edge(evidence_anchor),
    ):
        diversity_anchor = "diversity_groups"
    selection_anchor = diversity_anchor
    if add(
        "final_select",
        "select",
        "select.native_scaffold_round_robin@0.1.0",
        inputs=edge(diversity_anchor),
    ):
        selection_anchor = "final_select"
    add(
        "shortlist",
        "export",
        "export.native_smiles_shortlist@0.1.0",
        inputs=edge(selection_anchor),
    )
    nodes_by_slot: dict[str, list[dict[str, Any]]] = {}
    for stage in stages:
        nodes_by_slot.setdefault(stage["slot"], []).append(stage)
    node_layout: dict[str, dict[str, Any]] = {}
    tier_modes = {layer["slot"]: "serial" for layer in _LAYERS}
    tier_modes["hard_gate"] = "parallel_all"
    next_tier_x = 100
    ordered_slots = sorted(
        nodes_by_slot,
        key=lambda slot: next(
            index for index, layer in enumerate(_LAYERS) if layer["slot"] == slot
        ),
    )
    for slot in ordered_slots:
        slot_stages = nodes_by_slot[slot]
        base_x = next_tier_x
        criteria = [
            stage
            for stage in slot_stages
            if not plugins[stage["plugin"]]["is_policy_join"]
        ]
        joins = [
            stage
            for stage in slot_stages
            if plugins[stage["plugin"]]["is_policy_join"]
        ]
        is_parallel = slot == "hard_gate" and len(criteria) > 1
        for index, stage in enumerate(criteria):
            node_layout[stage["id"]] = {
                "x": base_x if is_parallel else base_x + index * 290,
                "y": 140 + (index * 190 if is_parallel else 0),
                "tier": slot,
                "mode": tier_modes[slot],
            }
        for index, stage in enumerate(joins):
            node_layout[stage["id"]] = {
                "x": base_x + 300 + index * 290,
                "y": 140 + max(0, len(criteria) - 1) * 95 + index * 170,
                "tier": slot,
                "mode": tier_modes[slot],
            }
        tier_node_ids = [stage["id"] for stage in slot_stages]
        rightmost = max(node_layout[stage_id]["x"] for stage_id in tier_node_ids)
        next_tier_x = int(rightmost) + 238 + 2 * 28 + 84

    # Read off the graph rather than asserted.  The default screen has no
    # docking tier today, so the literal and the computation agree -- but the
    # moment someone adds one to the defaults above, a literal would keep
    # reporting the old answer, and this key ends up in the provenance a
    # methods section is written from.
    docking_included = any(
        plugins[stage["plugin"]]["kind"] == PluginKind.DOCK.value for stage in stages
    )

    return PipelineConfig.model_validate(
        {
            "schema_version": 1,
            "name": "molcascade_default_screen",
            "description": "Local, modular, and auditable hierarchical molecular screening",
            "stages": stages,
            "metadata": {
                "created_by": f"MolCascade {__version__} offline builder",
                "final_parent_target": 45_000,
                "random_seed": 20_260_823,
                "docking_included": docking_included,
                "builder_view": "node_canvas/v2",
                "builder_layout": {
                    "schema_version": 2,
                    "viewport": {"x": -30, "y": 80, "zoom": 0.70},
                    "tier_modes": tier_modes,
                    "nodes": node_layout,
                },
            },
        }
    )


def build_builder_payload(
    *,
    plugins: PluginRegistry | None = None,
) -> dict[str, Any]:
    """Create the finite JSON payload consumed by the first-party browser UI."""

    registry = plugins or create_builtin_registry()
    backend_statuses = _backend_statuses()
    by_plugin = {
        status.spec.plugin_ref: status
        for status in backend_statuses
        if status.spec.plugin_ref is not None
    }
    plugin_rows: list[dict[str, Any]] = []
    for entry in registry.entries():
        plugin = entry.plugin
        descriptor = entry.descriptor
        status = by_plugin.get(descriptor.key)
        availability = status.availability if status else Availability.AVAILABLE
        config_model = getattr(plugin, "config_model", None)
        schema: dict[str, Any] = {}
        if isinstance(config_model, type) and issubclass(config_model, BaseModel):
            schema = config_model.model_json_schema(mode="validation")
        selectable = entry.trusted and availability in {
            Availability.AVAILABLE,
            Availability.DEGRADED,
        }
        plugin_rows.append(
            {
                "key": descriptor.key,
                "id": descriptor.id,
                "version": descriptor.version,
                "kind": descriptor.kind.value,
                "display_name": _UI_DISPLAY_NAMES.get(
                    descriptor.key,
                    descriptor.display_name or descriptor.id,
                ),
                "description": descriptor.description or "",
                "science_note": _SCIENCE_NOTES.get(descriptor.key, ""),
                "input_contracts": list(descriptor.inputs),
                "output_ports": dict(descriptor.output_ports),
                "cardinality": descriptor.cardinality.value,
                "determinism": descriptor.determinism.value,
                "config_schema": schema,
                "default_config": _plugin_default(plugin),
                "availability": availability.value,
                "availability_label": {
                    Availability.AVAILABLE: "Ready locally",
                    Availability.DEGRADED: "Available with warnings",
                    Availability.UNAVAILABLE: "Not installed",
                    Availability.BLOCKED: "Blocked by policy",
                }[availability],
                "status_reason": status.reason if status else "Built into MolCascade",
                "license_spdx": status.spec.license_spdx if status else "MolCascade",
                "selectable": selectable,
                "is_policy_join": descriptor.tier_neutral_policy,
            }
        )
    executable_keys = {row["key"] for row in plugin_rows}
    alternatives: list[dict[str, Any]] = []
    for status in backend_statuses:
        spec = status.spec
        slot = _CAPABILITY_SLOTS.get(spec.capability)
        if slot is None or (spec.plugin_ref and spec.plugin_ref in executable_keys):
            continue
        local_state = {
            Availability.AVAILABLE: "Detected locally",
            Availability.DEGRADED: "Detected with warnings",
            Availability.UNAVAILABLE: "Not detected locally",
            Availability.BLOCKED: "Blocked by local policy",
        }[status.availability]
        alternatives.append(
            {
                "backend_id": spec.id,
                "slot": slot,
                "display_name": spec.display_name,
                "description": spec.notes or (
                    "Reviewed local backend candidate; no executable MolCascade adapter "
                    "is registered yet."
                ),
                "availability": status.availability.value,
                "availability_label": f"{local_state} · adapter pending",
                "status_reason": status.reason,
                "license_spdx": spec.license_spdx,
                "tier": spec.tier.value,
                "interface": spec.interface.value,
                "selectable": False,
                "adapter_status": "Research option only — cannot be added or exported",
            }
        )
    default = _default_pipeline(plugin_rows)
    return {
        "builder_schema_version": 3,
        "molcascade_version": __version__,
        "layers": list(_LAYERS),
        "plugins": plugin_rows,
        "alternatives": alternatives,
        "default_pipeline": default.model_dump(mode="json"),
        "pipeline_schema": PipelineConfig.model_json_schema(mode="validation"),
        "export_filename": "molcascade-pipeline.json",
        "semantics": {
            "execution_order": "Stages run in exported array order.",
            "parallel_branch": (
                "Parallel (ALL) sends one retained parent population to every criterion "
                "and converges all decision outputs through a policy join."
            ),
            "serial_tier": (
                "Serial feeds each criterion only the survivors emitted by the previous "
                "criterion and therefore needs no branch join."
            ),
            "join_policy": (
                "A Policy join is an executable node; visual grouping alone never "
                "combines results."
            ),
        },
    }


def _asset(name: str) -> str:
    return files("molcascade.ui.assets").joinpath(name).read_text(encoding="utf-8")


def _csp_hash(content: str) -> str:
    digest = hashlib.sha256(content.encode("utf-8")).digest()
    return "sha256-" + base64.b64encode(digest).decode("ascii")


def render_config_builder(payload: dict[str, Any]) -> str:
    css = _asset("builder.css")
    mark = png_data_uri("molcascade-mark.png")
    rules_javascript = _asset("builder_rules.js")
    javascript = _asset("builder.js")
    payload_text = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    escaped_payload = html.escape(payload_text, quote=True)
    csp = (
        "default-src 'none'; "
        f"script-src '{_csp_hash(rules_javascript)}' '{_csp_hash(javascript)}'; "
        f"style-src '{_csp_hash(css)}'; "
        "img-src data:; connect-src 'none'; object-src 'none'; base-uri 'none'; "
        "form-action 'none'; frame-ancestors 'none'"
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta http-equiv="Content-Security-Policy" content="{html.escape(csp, quote=True)}">
  <title>MolCascade Pipeline Builder</title>
  <style>{css}</style>
</head>
<body>
  <a class="skip-link" href="#pipeline-canvas">Skip to pipeline</a>
  <header class="topbar">
    <div class="brand">
      <img class="brand-mark" src="{mark}" alt="" width="189" height="120">
      <div><h1>MolCascade</h1><small>Offline screening workflow builder</small></div>
    </div>
    <div class="top-actions">
      <label class="top-field" for="pipeline-name"><span>Pipeline</span>
        <input id="pipeline-name" autocomplete="off"></label>
      <label class="top-field target-field" for="final-target"><span>Final parents</span>
        <input id="final-target" type="number" min="1" step="1"></label>
      <button id="restore-default" class="button ghost" type="button">Restore default</button>
      <button id="preview-config" class="button ghost" type="button">Review config</button>
      <button id="download-config" class="button secondary" type="button">Download</button>
      <button id="save-config" class="button primary" type="button">Save config…</button>
    </div>
  </header>
  <main class="app-grid">
    <aside class="panel library-panel">
      <div class="panel-head">
        <div class="eyebrow">Component library</div>
        <h2>Screening modules</h2>
        <p>Drag an executable module onto the canvas. Researched alternatives are
          visible but cannot enter an executable configuration.</p>
        <label class="search-field" for="plugin-search">
          <span>Search</span>
          <input id="plugin-search" type="search" placeholder="Name, method, or contract">
        </label>
        <label class="search-field compact" for="palette-filter">
          <span>Show</span>
          <select id="palette-filter">
            <option value="all">Executable + research</option>
            <option value="executable">Executable only</option>
            <option value="research">Research options only</option>
          </select>
        </label>
      </div>
      <div id="palette" class="palette"></div>
    </aside>
    <section class="workspace" aria-labelledby="workspace-title">
      <h2 id="workspace-title" class="visually-hidden">Pipeline canvas</h2>
      <div class="canvas-toolbar">
        <div class="canvas-title">
          <span class="canvas-kicker">Node canvas</span>
          <strong>Build the screening flow</strong>
          <span id="canvas-help">Drag modules, move nodes, then connect output and
            input ports.</span>
        </div>
        <div class="tier-mode-control">
          <label for="tier-mode"><span id="mode-tier-name">Chemistry gates</span> flow</label>
          <select id="tier-mode" aria-describedby="tier-mode-help">
            <option value="parallel_all">Parallel (ALL)</option>
            <option value="serial">Serial</option>
          </select>
          <span id="tier-mode-help" class="visually-hidden">Parallel sends the same
            population to all criteria and requires every criterion. Serial filters in order.</span>
        </div>
        <div class="canvas-actions" aria-label="Canvas controls">
          <button id="auto-layout" class="tool-button" type="button">Arrange</button>
          <button id="fit-view" class="tool-button" type="button">Fit</button>
          <button id="reset-view" class="tool-button" type="button">Reset view</button>
          <span id="zoom-level" class="zoom-level" aria-live="polite">100%</span>
        </div>
      </div>
      <div id="pipeline-canvas" class="pipeline-canvas" tabindex="0"
           aria-label="Pipeline canvas. Drag modules here and connect their ports."
           aria-describedby="canvas-help">
        <div id="graph-scene" class="graph-scene">
          <svg id="graph-edges" class="graph-edges" width="5000" height="2200"
               viewBox="0 0 5000 2200" aria-hidden="true">
            <g id="edge-layer"></g>
            <path id="connection-preview" class="connection-preview" d=""></path>
          </svg>
          <div id="tier-guides" class="tier-guides" aria-hidden="true"></div>
          <div id="node-layer" class="node-layer"></div>
        </div>
        <div id="canvas-empty" class="canvas-empty" hidden>
          <strong>Drop a module to begin</strong>
          <span>Only locally executable adapters can be placed on this canvas.</span>
        </div>
        <div class="canvas-legend">
          <span><i class="legend-dot ready"></i>Executable</span>
          <span><i class="legend-line primary"></i>Primary population</span>
          <span><i class="legend-line decision"></i>Decision to policy</span>
          <span><i class="legend-line evidence"></i>Side evidence</span>
          <span>Wheel to zoom · drag empty space to pan</span>
        </div>
        <div id="connector-toast" class="connector-toast" role="status"
             aria-live="polite" hidden></div>
        <div class="graph-summary" aria-live="polite">
          <span id="stage-count" class="summary-chip"></span>
          <span id="branch-count" class="summary-chip"></span>
          <span class="summary-chip safe">Offline</span>
        </div>
      </div>
      <div id="errors" class="errors" aria-live="polite"></div>
    </section>
    <aside class="panel inspector">
      <div class="panel-head">
        <div class="eyebrow">Inspector</div>
        <h2>Node inspector</h2>
        <p>Edit the selected executable module, explicit connections, and validated parameters.</p>
      </div>
      <div id="inspector-body" class="inspector-body"></div>
    </aside>
  </main>
  <div id="preview-modal" class="modal-backdrop" role="dialog" aria-modal="true"
       aria-labelledby="preview-title">
    <div class="modal">
      <div class="modal-head">
        <div>
          <div class="eyebrow">CLI-ready configuration</div>
          <h2 id="preview-title">Review exported JSON</h2>
        </div>
        <button id="close-preview" class="icon-button" type="button"
                aria-label="Close preview">×</button>
      </div>
      <textarea id="config-preview" aria-label="Pipeline JSON" readonly></textarea>
      <div class="next-step">
        <strong>Next step</strong>
        <code>molcascade --vs --config molcascade-pipeline.json --molecules molecules.csv</code>
      </div>
    </div>
  </div>
  <textarea id="builder-payload" hidden>{escaped_payload}</textarea>
  <script>{rules_javascript}</script>
  <script>{javascript}</script>
</body>
</html>
"""


def generate_config_builder(
    output: str | Path,
    *,
    plugins: PluginRegistry | None = None,
    overwrite: bool = True,
) -> Path:
    """Atomically create the standalone HTML selected by ``--generate config``."""

    destination = Path(output).expanduser()
    if destination.suffix.casefold() not in {".html", ".htm"}:
        raise ValueError("configuration builder output must end in .html or .htm")
    payload = build_builder_payload(plugins=plugins)
    document = render_config_builder(payload).encode("utf-8")
    return atomic_write_bytes(destination, document, overwrite=overwrite)


__all__ = [
    "build_builder_payload",
    "generate_config_builder",
    "render_config_builder",
]
