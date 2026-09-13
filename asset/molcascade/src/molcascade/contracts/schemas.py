"""Built-in MolCascade Arrow contracts.

The schemas are intentionally chemistry-toolkit neutral.  In particular, no
RDKit binary object or pickle is allowed to cross these boundaries.
"""

from __future__ import annotations

import pyarrow as pa

from molcascade.contracts.base import DataContract


def _schema(contract_id: str, fields: list[pa.Field]) -> pa.Schema:
    """Create a schema carrying informational, non-authoritative metadata."""

    return pa.schema(fields, metadata={b"molcascade.contract": contract_id.encode("ascii")})


RAW_MOLECULE_V1 = DataContract(
    id="raw_molecule/v1",
    schema=_schema(
        "raw_molecule/v1",
        [
            pa.field("source_record_id", pa.string(), nullable=False),
            pa.field("source_kind", pa.string(), nullable=False),
            pa.field("source_uri", pa.large_string(), nullable=True),
            pa.field("source_index", pa.int64(), nullable=False),
            pa.field("generator_id", pa.string(), nullable=True),
            pa.field("batch_id", pa.string(), nullable=True),
            pa.field("source_candidate_id", pa.string(), nullable=True),
            pa.field("raw_smiles", pa.large_string(), nullable=True),
            pa.field("raw_molblock", pa.large_string(), nullable=True),
            pa.field("source_metadata_json", pa.large_string(), nullable=True),
        ],
    ),
    primary_key=("source_record_id",),
    required_columns=(
        "source_record_id",
        "source_kind",
        "source_index",
    ),
    at_least_one_nonempty=(("raw_smiles", "raw_molblock"),),
    invariants={
        "raw_immutable": (
            "raw_smiles and raw_molblock preserve source spelling and are never overwritten"
        ),
        "structure_present": "raw_smiles or raw_molblock is non-empty for every row",
        "source_record_stable": (
            "source_record_id is stable within the immutable source artifact"
        ),
        "source_index_non_negative": "source_index is zero-based and non-negative",
    },
)


RAW_MOLECULE_V2 = DataContract(
    id="raw_molecule/v2",
    schema=_schema(
        "raw_molecule/v2",
        [
            pa.field("source_record_id", pa.string(), nullable=False),
            pa.field("source_kind", pa.string(), nullable=False),
            pa.field("source_uri", pa.large_string(), nullable=True),
            pa.field("source_index", pa.int64(), nullable=False),
            pa.field("generator_id", pa.string(), nullable=True),
            pa.field("batch_id", pa.string(), nullable=True),
            pa.field("source_candidate_id", pa.string(), nullable=True),
            pa.field("raw_format", pa.string(), nullable=False),
            pa.field("raw_structure", pa.large_string(), nullable=False),
            pa.field("source_metadata_json", pa.large_string(), nullable=True),
        ],
    ),
    primary_key=("source_record_id",),
    required_columns=(
        "source_record_id",
        "source_kind",
        "source_index",
        "raw_format",
        "raw_structure",
    ),
    enums={"raw_format": ("SMILES", "MOLBLOCK", "MOL2")},
    invariants={
        "raw_immutable": (
            "raw_structure preserves source spelling and is never overwritten"
        ),
        "format_explicit": (
            "raw_format explicitly identifies SMILES, MOLBLOCK, or MOL2 syntax"
        ),
        "structure_present": (
            "raw_structure is non-null; blank payloads are rejected by source or chemistry stages"
        ),
        "source_record_stable": (
            "source_record_id is stable within the immutable source artifact"
        ),
        "source_index_non_negative": "source_index is zero-based and non-negative",
    },
)


PARENT_V1 = DataContract(
    id="parent/v1",
    schema=_schema(
        "parent/v1",
        [
            pa.field("parent_id", pa.string(), nullable=False),
            pa.field("identity_policy_id", pa.string(), nullable=False),
            pa.field("parent_smiles", pa.large_string(), nullable=False),
            pa.field("registration_key", pa.string(), nullable=False),
            pa.field("stereo_key", pa.string(), nullable=True),
            pa.field("formula", pa.string(), nullable=True),
            pa.field("duplicate_count", pa.int64(), nullable=False),
        ],
    ),
    primary_key=("parent_id",),
    required_columns=(
        "parent_id",
        "identity_policy_id",
        "parent_smiles",
        "registration_key",
        "duplicate_count",
    ),
    invariants={
        "identity_namespaced": (
            "parent_id is derived under the declared identity_policy_id namespace"
        ),
        "one_row_per_parent": "each registered parent occurs exactly once",
        "duplicate_count_positive": "duplicate_count is at least one",
        "toolkit_neutral": "no toolkit-native molecule object is serialized",
    },
)


PARENT_SOURCE_MAP_V1 = DataContract(
    id="parent_source_map/v1",
    schema=_schema(
        "parent_source_map/v1",
        [
            pa.field("source_record_id", pa.string(), nullable=False),
            pa.field("parent_id", pa.string(), nullable=False),
            pa.field("relation", pa.string(), nullable=False),
        ],
    ),
    primary_key=("source_record_id",),
    required_columns=("source_record_id", "parent_id", "relation"),
    enums={"relation": ("PRIMARY", "DUPLICATE")},
    invariants={
        "source_maps_once": "a successfully registered source record maps to one parent",
        "many_sources_allowed": "multiple source records may map to the same parent",
        "foreign_keys": (
            "source_record_id references raw_molecule/v1 or raw_molecule/v2 and "
            "parent_id references parent/v1"
        ),
    },
)


DECISION_V1 = DataContract(
    id="decision/v1",
    schema=_schema(
        "decision/v1",
        [
            pa.field("entity_id", pa.string(), nullable=False),
            pa.field("entity_kind", pa.string(), nullable=False),
            pa.field("stage_id", pa.string(), nullable=False),
            pa.field("outcome", pa.string(), nullable=False),
            pa.field("reason_code", pa.string(), nullable=False),
            pa.field("rule_id", pa.string(), nullable=True),
            pa.field("detail", pa.large_string(), nullable=True),
        ],
    ),
    primary_key=("entity_id", "entity_kind", "stage_id", "reason_code"),
    required_columns=("entity_id", "entity_kind", "stage_id", "outcome", "reason_code"),
    enums={
        "entity_kind": ("SOURCE_RECORD", "PARENT", "STATE"),
        "outcome": ("PASS", "REJECT", "WARN"),
    },
    invariants={
        "decision_explainable": "every outcome has a stable machine-readable reason_code",
        "hard_reject_terminal": "a REJECT outcome cannot be rescued by a downstream score",
        "one_reason_per_entity_stage": (
            "an entity has at most one decision row per stage and reason_code"
        ),
    },
)


PROPERTY_V1 = DataContract(
    id="property/v1",
    schema=_schema(
        "property/v1",
        [
            pa.field("parent_id", pa.string(), nullable=False),
            pa.field("calculator_id", pa.string(), nullable=False),
            pa.field("mw", pa.float64(), nullable=True),
            pa.field("clogp", pa.float64(), nullable=True),
            pa.field("tpsa", pa.float64(), nullable=True),
            pa.field("hbd", pa.int32(), nullable=True),
            pa.field("hba", pa.int32(), nullable=True),
            pa.field("rotatable_bonds", pa.int32(), nullable=True),
            pa.field("ring_count", pa.int32(), nullable=True),
            pa.field("heavy_atom_count", pa.int32(), nullable=True),
            pa.field("formal_charge", pa.int32(), nullable=True),
            pa.field("fraction_csp3", pa.float64(), nullable=True),
            pa.field("sa_score", pa.float64(), nullable=True),
            pa.field("warning_codes_json", pa.large_string(), nullable=True),
        ],
    ),
    primary_key=("parent_id", "calculator_id"),
    required_columns=("parent_id", "calculator_id"),
    invariants={
        "one_row_per_calculator": (
            "a parent has at most one row for each exact calculator/version/spec identity"
        ),
        "nullable_failure": (
            "a per-property calculation failure is null and has a warning code, never a fake zero"
        ),
        "units_fixed": "mw is Da, tpsa is square angstrom, and all units are contract-defined",
    },
)


DRUG_LIKENESS_V1 = DataContract(
    id="drug_likeness/v1",
    schema=_schema(
        "drug_likeness/v1",
        [
            pa.field("parent_id", pa.string(), nullable=False),
            pa.field("calculator_id", pa.string(), nullable=False),
            pa.field("rule_of_five_method_id", pa.string(), nullable=False),
            pa.field("qed_method_id", pa.string(), nullable=False),
            pa.field("toolkit_version", pa.string(), nullable=False),
            pa.field("mw", pa.float64(), nullable=False),
            pa.field("clogp", pa.float64(), nullable=False),
            pa.field("hbd", pa.int32(), nullable=False),
            pa.field("hba", pa.int32(), nullable=False),
            pa.field("tpsa", pa.float64(), nullable=False),
            pa.field("rotatable_bonds", pa.int32(), nullable=False),
            pa.field("aromatic_ring_count", pa.int32(), nullable=False),
            pa.field("qed_alert_count", pa.int32(), nullable=False),
            pa.field("rule_of_five_violation_count", pa.int32(), nullable=False),
            pa.field("rule_of_five_pass", pa.bool_(), nullable=False),
            pa.field("qed_weighted", pa.float64(), nullable=False),
        ],
    ),
    primary_key=("parent_id", "calculator_id"),
    invariants={
        "rule_of_five_separate": (
            "rule_of_five_method_id identifies the four explicit threshold rules; "
            "Rule-of-Five is not inferred from QED"
        ),
        "rule_of_five_pass_strict": (
            "rule_of_five_pass is true only when the violation count is zero; project "
            "policies may independently allow a configured number of violations"
        ),
        "qed_separate": (
            "qed_method_id identifies the QED implementation and weights independently "
            "of Rule-of-Five policy"
        ),
        "toolkit_explicit": "toolkit_version records the exact RDKit release",
        "evidence_not_activity": (
            "drug-likeness evidence is not evidence of potency, safety, or clinical efficacy"
        ),
        "units_fixed": "mw is Da and tpsa is square angstrom",
    },
)


FINGERPRINT_V1 = DataContract(
    id="fingerprint/v1",
    schema=_schema(
        "fingerprint/v1",
        [
            pa.field("parent_id", pa.string(), nullable=False),
            pa.field("fingerprint_spec_id", pa.string(), nullable=False),
            pa.field("bit_length", pa.int32(), nullable=False),
            pa.field("popcount", pa.int32(), nullable=False),
            pa.field("packed_bits", pa.binary(), nullable=False),
        ],
    ),
    primary_key=("parent_id", "fingerprint_spec_id"),
    invariants={
        "packed_bits": "fingerprints use packed bytes rather than Python objects",
        "length_matches": "packed_bits length and popcount match bit_length",
        "spec_namespaced": (
            "fingerprint_spec_id binds toolkit/version/type/radius/feature/chirality/bit length"
        ),
    },
)


PREDICTION_V1 = DataContract(
    id="prediction/v1",
    schema=_schema(
        "prediction/v1",
        [
            pa.field("parent_id", pa.string(), nullable=False),
            pa.field("endpoint_id", pa.string(), nullable=False),
            pa.field("model_id", pa.string(), nullable=False),
            pa.field("prediction_mean", pa.float64(), nullable=False),
            pa.field("prediction_std", pa.float64(), nullable=True),
            pa.field("interval_lower", pa.float64(), nullable=True),
            pa.field("interval_upper", pa.float64(), nullable=True),
            pa.field("calibration_id", pa.string(), nullable=True),
        ],
    ),
    primary_key=("parent_id", "endpoint_id", "model_id"),
    invariants={
        "model_identity": "model_id binds model bytes, feature spec, training split and software",
        "endpoint_separate": "different endpoints are never collapsed into one score column",
    },
)


APPLICABILITY_V1 = DataContract(
    id="applicability/v1",
    schema=_schema(
        "applicability/v1",
        [
            pa.field("parent_id", pa.string(), nullable=False),
            pa.field("model_id", pa.string(), nullable=False),
            pa.field("method_id", pa.string(), nullable=False),
            pa.field("in_domain", pa.bool_(), nullable=False),
            pa.field("raw_metric", pa.float64(), nullable=False),
            pa.field("threshold", pa.float64(), nullable=False),
            pa.field("nearest_reference_id", pa.string(), nullable=True),
            pa.field("nearest_similarity", pa.float64(), nullable=True),
        ],
    ),
    primary_key=("parent_id", "model_id", "method_id"),
    invariants={
        "ad_not_uncertainty": (
            "chemical-space applicability is stored separately from predictive uncertainty"
        ),
        "threshold_frozen": "method_id identifies the reference snapshot and frozen threshold",
    },
)


SYNTHESIS_SCORE_V1 = DataContract(
    id="synthesis_score/v1",
    schema=_schema(
        "synthesis_score/v1",
        [
            pa.field("parent_id", pa.string(), nullable=False),
            pa.field("method_id", pa.string(), nullable=False),
            pa.field("score", pa.float64(), nullable=False),
            pa.field("direction", pa.string(), nullable=False),
            pa.field("domain", pa.string(), nullable=True),
            pa.field("warning_codes_json", pa.large_string(), nullable=True),
        ],
    ),
    primary_key=("parent_id", "method_id"),
    enums={"direction": ("HIGHER_EASIER", "HIGHER_HARDER")},
    invariants={
        "method_semantics": "SA, SC and route-aware scores retain distinct method identities",
        "not_a_proof": "a synthesis score is prioritization evidence, not proof of a viable route",
    },
)


LIGAND_CONFORMER_V1 = DataContract(
    id="ligand_conformer/v1",
    schema=_schema(
        "ligand_conformer/v1",
        [
            pa.field("parent_id", pa.string(), nullable=False),
            pa.field("conformer_index", pa.int32(), nullable=False),
            pa.field("molblock", pa.large_string(), nullable=True),
            pa.field("energy_kcal_mol", pa.float64(), nullable=True),
            pa.field("embed_status", pa.string(), nullable=False),
            pa.field("method_id", pa.string(), nullable=False),
        ],
    ),
    primary_key=("parent_id", "conformer_index"),
    enums={
        "embed_status": (
            "EMBEDDED",
            "MINIMIZED",
            "EMBED_FAILED",
            "MINIMIZE_FAILED",
        )
    },
    invariants={
        "shared_geometry": (
            "engines that declare this contract read the same conformers, so their "
            "scores are comparable to each other; an engine that embeds its own "
            "geometry does not declare it and is not covered by that guarantee"
        ),
        "failure_recorded": (
            "a molecule that could not be embedded keeps a row with a null molblock "
            "and the reason in embed_status"
        ),
        "coordinates_are_3d": "a non-null molblock carries explicit 3D coordinates",
    },
)


DOCKING_SCORE_V1 = DataContract(
    id="docking_score/v1",
    schema=_schema(
        "docking_score/v1",
        [
            pa.field("parent_id", pa.string(), nullable=False),
            pa.field("engine_id", pa.string(), nullable=False),
            pa.field("receptor_id", pa.string(), nullable=False),
            pa.field("pose_rank", pa.int32(), nullable=False),
            pa.field("score", pa.float64(), nullable=False),
            pa.field("score_kind", pa.string(), nullable=False),
            pa.field("direction", pa.string(), nullable=False),
            pa.field("secondary_score", pa.float64(), nullable=True),
            pa.field("pose_molblock", pa.large_string(), nullable=True),
            pa.field("method_id", pa.string(), nullable=False),
        ],
    ),
    primary_key=("parent_id", "engine_id", "receptor_id", "pose_rank"),
    enums={
        "score_kind": (
            "VINA_KCAL_MOL",
            "VINARDO_KCAL_MOL",
            "AD4_KCAL_MOL",
            "CNN_SCORE",
            "CNN_AFFINITY",
            "KARMADOCK_MDN",
        ),
        "direction": ("LOWER_STRONGER", "HIGHER_STRONGER"),
    },
    invariants={
        "receptor_bound": (
            "receptor_id is the digest of the receptor bytes, so a score can never be "
            "compared against a pose computed on a different structure"
        ),
        "pose_rank_ordered": "pose_rank is zero-based and orders poses best-first per engine",
        "not_affinity": (
            "a docking score ranks candidates and is not a measured or predicted "
            "binding free energy"
        ),
    },
)


DERIVED_METRIC_V1 = DataContract(
    id="derived_metric/v1",
    schema=_schema(
        "derived_metric/v1",
        [
            pa.field("parent_id", pa.string(), nullable=False),
            pa.field("metric_id", pa.string(), nullable=False),
            pa.field("method_id", pa.string(), nullable=False),
            pa.field("value", pa.float64(), nullable=True),
            pa.field("units", pa.string(), nullable=False),
            pa.field("direction", pa.string(), nullable=False),
            pa.field("status", pa.string(), nullable=False),
            pa.field("status_detail", pa.string(), nullable=True),
            pa.field("source_json", pa.large_string(), nullable=True),
        ],
    ),
    primary_key=("parent_id", "metric_id", "method_id"),
    enums={
        "units": (
            "KCAL_PER_MOL",
            "KCAL_PER_MOL_PER_HEAVY_ATOM",
            "KCAL_PER_MOL_PER_HEAVY_ATOM_POW",
            "TEU",
            "COUNT",
        ),
        "direction": ("HIGHER_BETTER", "LOWER_BETTER"),
        "status": ("OK", "NOT_APPLICABLE", "OUT_OF_DOMAIN", "BACKEND_FAILED"),
    },
    invariants={
        "units_declared": (
            "units is never empty, so a threshold can be checked against the scale it "
            "was written for; the same small number means different things in "
            "kcal/mol and in torsion energy units, and nothing else in a row "
            "distinguishes them"
        ),
        "failure_recorded": (
            "a molecule the metric could not be computed for keeps a row with a null "
            "value and the reason in status, so a downstream gate sees an explicit "
            "absence rather than a missing row it might read as a pass"
        ),
        "out_of_domain_keeps_its_value": (
            "OUT_OF_DOMAIN records the number that was computed rather than nulling "
            "it: a strain below zero is evidence that the reference state search "
            "failed, and that evidence is the value itself"
        ),
        "derived_not_measured": (
            "every value here is arithmetic on earlier evidence -- a docking score "
            "divided by a size, a pose energy minus an ensemble minimum -- and "
            "inherits every limitation of the score it came from"
        ),
        "method_carries_its_parameters": (
            "method_id is a digest over the metric's own parameters, so changing an "
            "exponent, a baseline fit or a conformer seed produces a new row rather "
            "than overwriting a measurement made under different settings"
        ),
    },
)


SCAFFOLD_ASSIGNMENT_V1 = DataContract(
    id="scaffold_assignment/v1",
    schema=_schema(
        "scaffold_assignment/v1",
        [
            pa.field("parent_id", pa.string(), nullable=False),
            pa.field("scaffold_definition_id", pa.string(), nullable=False),
            pa.field("exact_scaffold_id", pa.string(), nullable=False),
            pa.field("exact_scaffold_smiles", pa.large_string(), nullable=True),
            pa.field("generic_scaffold_id", pa.string(), nullable=False),
            pa.field("generic_scaffold_smiles", pa.large_string(), nullable=True),
            pa.field("ring_system_id", pa.string(), nullable=True),
            pa.field("acyclic", pa.bool_(), nullable=False),
            pa.field("toolkit_version", pa.string(), nullable=False),
        ],
    ),
    primary_key=("parent_id", "scaffold_definition_id"),
    invariants={
        "assignment_total": "every accepted parent receives one assignment for the definition",
        "acyclic_explicit": (
            "acyclic molecules use explicit sentinel IDs rather than an empty group"
        ),
        "definition_namespaced": "chirality and toolkit/version participate in the definition ID",
    },
)


CLUSTER_ASSIGNMENT_V1 = DataContract(
    id="cluster_assignment/v1",
    schema=_schema(
        "cluster_assignment/v1",
        [
            pa.field("parent_id", pa.string(), nullable=False),
            pa.field("cluster_method_id", pa.string(), nullable=False),
            pa.field("cluster_id", pa.string(), nullable=False),
            pa.field("representative_parent_id", pa.string(), nullable=False),
            pa.field("is_representative", pa.bool_(), nullable=False),
            pa.field("similarity_to_representative", pa.float64(), nullable=False),
            pa.field("fingerprint_spec_id", pa.string(), nullable=False),
            pa.field("threshold", pa.float64(), nullable=False),
            pa.field("assignment_rank", pa.int64(), nullable=False),
            pa.field("input_order_hash", pa.string(), nullable=False),
        ],
    ),
    primary_key=("parent_id", "cluster_method_id"),
    invariants={
        "assignment_total": "every input parent receives exactly one assignment",
        "representative_member": "each representative belongs to its own cluster",
        "radius_semantics": "radius methods satisfy similarity_to_representative >= threshold",
        "no_dense_matrix": "the implementation does not materialize an N by N matrix",
    },
)


SELECTION_DECISION_V1 = DataContract(
    id="selection_decision/v1",
    schema=_schema(
        "selection_decision/v1",
        [
            pa.field("parent_id", pa.string(), nullable=False),
            pa.field("quota_policy_id", pa.string(), nullable=False),
            pa.field("selected", pa.bool_(), nullable=False),
            pa.field("basket", pa.string(), nullable=True),
            pa.field("basket_rank", pa.int64(), nullable=True),
            pa.field("global_rank", pa.int64(), nullable=True),
            pa.field("pareto_front", pa.int32(), nullable=True),
            pa.field("priority_score", pa.float64(), nullable=True),
            pa.field("reason_code", pa.string(), nullable=False),
            pa.field("tie_break_key", pa.string(), nullable=False),
            pa.field("seed", pa.int64(), nullable=False),
        ],
    ),
    primary_key=("parent_id", "quota_policy_id"),
    enums={
        "basket": (
            "EXPLOITATION",
            "SCAFFOLD_DIVERSITY",
            "NEAR_REFERENCE",
            "EXPLORATION",
        )
    },
    invariants={
        "basket_exclusive": "a selected parent belongs to exactly one basket",
        "hard_reject_terminal": "hard-rejected parents cannot enter any basket or backfill",
        "stable_ties": "ties resolve with a deterministic parent-derived key",
    },
)


SHORTLIST_EXPORT_V1 = DataContract(
    id="shortlist_export/v1",
    schema=_schema(
        "shortlist_export/v1",
        [
            pa.field("parent_id", pa.string(), nullable=False),
            pa.field("export_spec_id", pa.string(), nullable=False),
            pa.field("record_format", pa.string(), nullable=False),
            pa.field("record_name", pa.string(), nullable=False),
            pa.field("parent_smiles", pa.large_string(), nullable=False),
            pa.field("structure_record", pa.large_string(), nullable=False),
            pa.field("warning_codes_json", pa.large_string(), nullable=True),
        ],
    ),
    primary_key=("parent_id", "export_spec_id"),
    required_columns=(
        "parent_id",
        "export_spec_id",
        "record_format",
        "record_name",
        "parent_smiles",
        "structure_record",
    ),
    enums={"record_format": ("SMILES_TSV", "SDF_MOLBLOCK")},
    invariants={
        "one_record_per_parent": "each export specification emits one record per parent",
        "materialization_explicit": (
            "record_format determines how structure_record is materialized; no pickle is used"
        ),
        "handoff_only": (
            "the contract is a shortlist handoff and does not execute MD or free-energy "
            "calculations; docking evidence, when present, travels as scores in "
            "docking_score/v1 and is not a claim of binding"
        ),
    },
)


MD_SYSTEM_INPUT_V1 = DataContract(
    id="md_system_input/v1",
    schema=_schema(
        "md_system_input/v1",
        [
            pa.field("parent_id", pa.string(), nullable=False),
            pa.field("method_id", pa.string(), nullable=False),
            # Nullable for the same reason derived_metric/v1's value is: a molecule
            # whose geometry could not be produced keeps a row saying so, because a
            # missing row is read downstream as a molecule that was fine.
            pa.field("molblock", pa.large_string(), nullable=True),
            pa.field("coordinate_source", pa.string(), nullable=False),
            pa.field("hydrogens", pa.string(), nullable=False),
            pa.field("heavy_atom_count", pa.int32(), nullable=True),
            pa.field("hydrogen_count", pa.int32(), nullable=True),
            pa.field("formal_charge", pa.int32(), nullable=True),
            # The isomeric SMILES re-derived from the coordinates that are in this
            # row, which is not the same string as the name the molecule is filed
            # under whenever embedding settled an unspecified centre.
            pa.field("stereo_smiles", pa.large_string(), nullable=True),
            pa.field("parent_smiles", pa.large_string(), nullable=False),
            pa.field("protonation_state_id", pa.string(), nullable=False),
            # Null unless coordinate_source is DOCKED_POSE. A pose is only meaningful
            # against the receptor it was scored in, so the digest travels with it.
            pa.field("receptor_id", pa.string(), nullable=True),
            pa.field("status", pa.string(), nullable=False),
            pa.field("status_detail", pa.string(), nullable=True),
        ],
    ),
    primary_key=("parent_id", "method_id"),
    enums={
        # TWO_D_DEPICTION is a legal value on purpose. A producer that has only a
        # flat depiction must be able to say so; what it must not be able to do is
        # stay silent, which is the state this contract exists to abolish.
        "coordinate_source": (
            "DOCKED_POSE",
            "EMBEDDED_CONFORMER",
            "TWO_D_DEPICTION",
            "NONE",
        ),
        "hydrogens": ("EXPLICIT_ALL", "POLAR_ONLY", "IMPLICIT", "UNKNOWN"),
        "status": ("OK", "NO_GEOMETRY", "UNREADABLE"),
    },
    invariants={
        "declared_not_inferred": (
            "every field here is a statement the producer had to make rather than a "
            "property a consumer may infer from the molblock; a simulation stack "
            "given a structure cannot tell a deliberate neutral form from an "
            "accidental one, and a flat depiction parameterises and simulates as "
            "readily as a docked pose"
        ),
        "two_d_is_not_a_structure": (
            "TWO_D_DEPICTION means the coordinates are a drawing -- every z is zero "
            "and the layout is arbitrary -- so a record carrying it is fit for "
            "depiction and for nothing that integrates a force field; the value "
            "exists so that the fact is recorded rather than discovered by reading "
            "coordinates nobody thought to read"
        ),
        "implicit_hydrogens_are_not_a_molecule": (
            "IMPLICIT means the record names heavy atoms only; a force-field build "
            "from it produces a topology with no hydrogens, which is chemically "
            "wrong and which every downstream validator accepts"
        ),
        "pose_is_bound_to_its_receptor": (
            "receptor_id is non-null whenever coordinate_source is DOCKED_POSE, "
            "because a pose re-used against different receptor bytes -- a "
            "re-protonated site, a different chain, a repaired residue -- is a pose "
            "in a different potential and the number computed from it is not "
            "comparable to the one that selected it"
        ),
        "stereochemistry_is_what_was_built": (
            "stereo_smiles describes the one isomer these coordinates are, while "
            "parent_smiles may name a set of them; distance geometry settles "
            "unspecified centres from a seeded hash, so the two strings disagreeing "
            "is ordinary rather than exceptional and is the reason both are carried"
        ),
        "handoff_not_result": (
            "this contract hands a structure to a simulation stack and asserts "
            "nothing about binding; it records what will be simulated, not what the "
            "simulation found"
        ),
    },
)

BUILTIN_CONTRACTS = (
    RAW_MOLECULE_V1,
    RAW_MOLECULE_V2,
    PARENT_V1,
    PARENT_SOURCE_MAP_V1,
    DECISION_V1,
    PROPERTY_V1,
    DRUG_LIKENESS_V1,
    FINGERPRINT_V1,
    PREDICTION_V1,
    APPLICABILITY_V1,
    SYNTHESIS_SCORE_V1,
    LIGAND_CONFORMER_V1,
    DOCKING_SCORE_V1,
    DERIVED_METRIC_V1,
    SCAFFOLD_ASSIGNMENT_V1,
    CLUSTER_ASSIGNMENT_V1,
    SELECTION_DECISION_V1,
    SHORTLIST_EXPORT_V1,
    MD_SYSTEM_INPUT_V1,
)

__all__ = [
    "APPLICABILITY_V1",
    "BUILTIN_CONTRACTS",
    "CLUSTER_ASSIGNMENT_V1",
    "DECISION_V1",
    "DERIVED_METRIC_V1",
    "DOCKING_SCORE_V1",
    "DRUG_LIKENESS_V1",
    "FINGERPRINT_V1",
    "LIGAND_CONFORMER_V1",
    "PARENT_SOURCE_MAP_V1",
    "PARENT_V1",
    "PREDICTION_V1",
    "PROPERTY_V1",
    "RAW_MOLECULE_V1",
    "RAW_MOLECULE_V2",
    "SCAFFOLD_ASSIGNMENT_V1",
    "SELECTION_DECISION_V1",
    "SHORTLIST_EXPORT_V1",
    "SYNTHESIS_SCORE_V1",
]
