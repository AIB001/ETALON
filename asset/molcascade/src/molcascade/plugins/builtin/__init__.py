"""Reviewed plugins shipped with the MolCascade distribution."""

from molcascade.plugins.api import StagePlugin
from molcascade.plugins.builtin.admet import ADMETAIV2PredictorPlugin
from molcascade.plugins.builtin.aizynthfinder import AiZynthFinderRoutePlugin
from molcascade.plugins.builtin.boltz2 import Boltz2AffinityPlugin
from molcascade.plugins.builtin.chembl_check import ChemblStructureCheckPlugin
from molcascade.plugins.builtin.chemprop_model import ChempropCheckpointPlugin
from molcascade.plugins.builtin.custom_model import CustomModelPredictorPlugin
from molcascade.plugins.builtin.diversity import (
    NativeHashBudgetSelectorPlugin,
    NativeScaffoldRoundRobinSelectorPlugin,
    NativeStreamingLeaderPlugin,
    RDKitMurckoScaffoldPlugin,
    RDKitScaffoldGroupClusterPlugin,
)
from molcascade.plugins.builtin.docking import (
    GninaPlugin,
    KarmaDockPlugin,
    RDKitLigandConformerPlugin,
    UniDockPlugin,
)
from molcascade.plugins.builtin.docking.redock import RedockPlugin
from molcascade.plugins.builtin.druglikeness import RDKitDrugLikenessPlugin
from molcascade.plugins.builtin.evidence_gates import (
    NativeDerivedMetricEvidenceGatePlugin,
    NativeDockingScoreEvidenceGatePlugin,
    NativePredictionEvidenceGatePlugin,
    NativeSynthesisScoreEvidenceGatePlugin,
)
from molcascade.plugins.builtin.exporters import (
    NativeSmilesShortlistPlugin,
    RDKitSDFShortlistPlugin,
)
from molcascade.plugins.builtin.features import (
    OpenBabelPropertyPlugin,
    RDKitFingerprintPlugin,
    RDKitPropertyPlugin,
)
from molcascade.plugins.builtin.hard_gate import RDKitHardGatePlugin
from molcascade.plugins.builtin.lilly_medchem import LillyMedchemPlugin
from molcascade.plugins.builtin.md_handoff import (
    MdHandoffFromConformerPlugin,
    MdHandoffPlugin,
)
from molcascade.plugins.builtin.md_handoff_gate import MdHandoffGatePlugin
from molcascade.plugins.builtin.medchem_alerts import MedchemAlertsPlugin
from molcascade.plugins.builtin.medchem_rules import MedchemRulesPlugin
from molcascade.plugins.builtin.mordred_gate import MordredDescriptorGatePlugin
from molcascade.plugins.builtin.normalized_docking_score import (
    NormalizedDockingScorePlugin,
)
from molcascade.plugins.builtin.openadmet import OpenADMETPredictorPlugin
from molcascade.plugins.builtin.policy import NativeDecisionJoinPlugin
from molcascade.plugins.builtin.pose_strain import PoseStrainPlugin
from molcascade.plugins.builtin.property_gate import RDKitPropertyRangeGatePlugin
from molcascade.plugins.builtin.rd_filters_alerts import RdFiltersAlertsPlugin
from molcascade.plugins.builtin.ring_topology_gate import RDKitRingTopologyGatePlugin
from molcascade.plugins.builtin.scscore import ScScorePlugin
from molcascade.plugins.builtin.similarity import RDKitReferenceSimilarityPlugin
from molcascade.plugins.builtin.similarity_fpsim2 import Fpsim2ReferenceSimilarityPlugin
from molcascade.plugins.builtin.sources import (
    DelimitedSmilesSourcePlugin,
    Mol2DirectorySourcePlugin,
    RawMoleculeParquetSourcePlugin,
    SDFSourcePlugin,
    XlsxSourcePlugin,
)
from molcascade.plugins.builtin.specificity_panel import SpecificityPanelPlugin
from molcascade.plugins.builtin.standardize import RDKitStandardizePlugin
from molcascade.plugins.builtin.structural_alerts import RDKitStructuralAlertPlugin
from molcascade.plugins.builtin.synthesis import RDKitSAScorePlugin

BUILTIN_STAGE_PLUGINS: tuple[StagePlugin, ...] = (
    DelimitedSmilesSourcePlugin(),
    SDFSourcePlugin(),
    RawMoleculeParquetSourcePlugin(),
    XlsxSourcePlugin(),
    Mol2DirectorySourcePlugin(),
    RDKitStandardizePlugin(),
    RDKitHardGatePlugin(),
    ChemblStructureCheckPlugin(),
    RDKitPropertyRangeGatePlugin(),
    RDKitRingTopologyGatePlugin(),
    MordredDescriptorGatePlugin(),
    RDKitDrugLikenessPlugin(),
    MedchemRulesPlugin(),
    RDKitStructuralAlertPlugin(),
    LillyMedchemPlugin(),
    MdHandoffPlugin(),
    MdHandoffFromConformerPlugin(),
    MdHandoffGatePlugin(),
    MedchemAlertsPlugin(),
    RdFiltersAlertsPlugin(),
    NativeDecisionJoinPlugin(),
    RDKitPropertyPlugin(),
    OpenBabelPropertyPlugin(),
    RDKitFingerprintPlugin(),
    RDKitReferenceSimilarityPlugin(),
    Fpsim2ReferenceSimilarityPlugin(),
    RDKitSAScorePlugin(),
    ScScorePlugin(),
    AiZynthFinderRoutePlugin(),
    RDKitLigandConformerPlugin(),
    UniDockPlugin(),
    KarmaDockPlugin(),
    GninaPlugin(),
    NativeDockingScoreEvidenceGatePlugin(),
    NormalizedDockingScorePlugin(),
    PoseStrainPlugin(),
    RedockPlugin(),
    SpecificityPanelPlugin(),
    NativeDerivedMetricEvidenceGatePlugin(),
    ADMETAIV2PredictorPlugin(),
    ChempropCheckpointPlugin(),
    CustomModelPredictorPlugin(),
    OpenADMETPredictorPlugin(),
    Boltz2AffinityPlugin(),
    NativePredictionEvidenceGatePlugin(),
    NativeSynthesisScoreEvidenceGatePlugin(),
    RDKitMurckoScaffoldPlugin(),
    NativeStreamingLeaderPlugin(),
    RDKitScaffoldGroupClusterPlugin(),
    NativeHashBudgetSelectorPlugin(),
    NativeScaffoldRoundRobinSelectorPlugin(),
    NativeSmilesShortlistPlugin(),
    RDKitSDFShortlistPlugin(),
)

__all__ = ["BUILTIN_STAGE_PLUGINS"]
