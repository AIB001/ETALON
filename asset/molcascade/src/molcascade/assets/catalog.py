"""The declared set of downloadable assets.  Membership performs no I/O.

Every digest here was computed from bytes that were actually downloaded and
cross-checked against the Git object id the host announced independently.  None
of them is copied from a README.  If a digest in this file is wrong, the
corresponding backend refuses to run, which is the intended failure direction.

URLs use the GitHub Git-data API rather than ``raw.githubusercontent.com``.
The API addresses content by its object id, so the URL names the exact bytes
rather than "whatever this branch points at today", and it keeps working when a
branch is renamed or a tag is moved.
"""

from __future__ import annotations

from collections.abc import Iterator

from molcascade.assets.models import (
    AssetFile,
    AssetKind,
    AssetSpec,
    Citation,
    PayloadTrust,
)

_SCSCORE_COMMIT = "37090a6aa8220408f572de20224c33c221312d16"
_RD_FILTERS_COMMIT = "11a3b31f5b3669834bb916d8ed4422e69e455211"


def _github_blob(owner: str, repo: str, sha1: str) -> str:
    return f"https://api.github.com/repos/{owner}/{repo}/git/blobs/{sha1}"


SCSCORE = AssetSpec(
    id="scscore",
    kind=AssetKind.WEIGHTS,
    display_name="SCScore published weights (Coley et al.)",
    summary=(
        "Feed-forward weights trained on twelve million Reaxys reactions to predict "
        "synthetic complexity on a 1-5 scale. Three published variants differ in "
        "fingerprint width and in whether counts or bits were used during training."
    ),
    version=f"git {_SCSCORE_COMMIT[:12]}",
    homepage="https://github.com/connorcoley/scscore",
    license_spdx="MIT",
    trust=PayloadTrust.DATA,
    files=(
        AssetFile(
            name="models/full_reaxys_model_1024bool/model.ckpt-10654.as_numpy.json.gz",
            sha256="fa90bd552307b2efd05eb1e53033bc9eadadfd5bdca682e541040982af99536b",
            size_bytes=6_178_760,
            url=_github_blob(
                "connorcoley", "scscore", "9348494e097900ac7cc2c8a5ee3f186fabf8cbc4"
            ),
            git_blob_sha1="9348494e097900ac7cc2c8a5ee3f186fabf8cbc4",
            summary="1024-bit binary Morgan fingerprint; the variant the paper reports on.",
        ),
        AssetFile(
            name="models/full_reaxys_model_1024uint8/model.ckpt-10654.as_numpy.json.gz",
            sha256="3e21fe1d2d67abfe7fa1bd1049a2df9d23d846fce1e4202c6094155348f2155d",
            size_bytes=6_179_046,
            url=_github_blob(
                "connorcoley", "scscore", "26178a3510923e3a44f7d3bbab907e82d8625f09"
            ),
            git_blob_sha1="26178a3510923e3a44f7d3bbab907e82d8625f09",
            summary="1024-wide fingerprint trained on counts; pair with fingerprint='counts'.",
        ),
        AssetFile(
            name="models/full_reaxys_model_2048bool/model.ckpt-10654.as_numpy.json.gz",
            sha256="fd5ce6011ced12552180346b38c6d25a31f8e7b11b627119d44eaac90eac4a44",
            size_bytes=9_014_614,
            url=_github_blob(
                "connorcoley", "scscore", "dd799fde3a0da3ba7d3c7bc2613ef9d06f39ffdf"
            ),
            git_blob_sha1="dd799fde3a0da3ba7d3c7bc2613ef9d06f39ffdf",
            summary="2048-bit binary fingerprint; slower, slightly finer resolution.",
        ),
        AssetFile(
            name="LICENSE",
            sha256="df8e2cbb5c5f0b36aa6eabcd32bd80c8dbacb6a00b5d2269675716a8e19138aa",
            size_bytes=1_069,
            url=_github_blob(
                "connorcoley", "scscore", "7f9c0ca9cb1f2ecc00a456fbe4a2ffbab1b8946a"
            ),
            git_blob_sha1="7f9c0ca9cb1f2ecc00a456fbe4a2ffbab1b8946a",
            summary="Upstream MIT licence text.",
        ),
        AssetFile(
            name="standalone_model_numpy.py",
            sha256="07eebba3c799760b1634541b7e8adb494c12c61f142de82795c4d17972e1027e",
            size_bytes=5_037,
            url=_github_blob(
                "connorcoley", "scscore", "ad5037b8f22c245741d16c9538a150914dac6638"
            ),
            git_blob_sha1="ad5037b8f22c245741d16c9538a150914dac6638",
            summary=(
                "The reference implementation, vendored for reading and for test "
                "comparison. MolCascade does not import or execute it."
            ),
        ),
    ),
    citations=(
        Citation(
            title="SCScore: Synthetic Complexity Learned from a Reaction Corpus",
            authors="Coley, C. W.; Rogers, L.; Green, W. H.; Jensen, K. F.",
            venue="Journal of Chemical Information and Modeling 58(2), 252-261",
            year=2018,
            doi="10.1021/acs.jcim.7b00622",
            url="https://pubs.acs.org/doi/10.1021/acs.jcim.7b00622",
        ),
    ),
    used_by=("synthesis.scscore@0.1.0",),
    notes=(
        "The weights are a gzipped JSON array of floating-point matrices. MolCascade "
        "reads them with its own parser and evaluates the network in numpy, so nothing "
        "in the file is executed and no TensorFlow install is required.\n\n"
        "`standalone_model_numpy.py` is included for provenance only. It is read by a "
        "human and used as the specification the MolCascade implementation is tested "
        "against; it is never imported at runtime."
    ),
)


RD_FILTERS_ALERTS = AssetSpec(
    id="rd_filters",
    kind=AssetKind.RULES,
    display_name="rd_filters structural alert collection (Walters)",
    summary=(
        "1,251 SMARTS alerts assembled from eight published medicinal-chemistry "
        "filter sets -- BMS, Dundee, Glaxo, Inpharmatica, LINT, MLSMR, PAINS and "
        "SureChEMBL -- each carrying the occurrence count above which it fires."
    ),
    version=f"git {_RD_FILTERS_COMMIT[:12]}",
    homepage="https://github.com/PatWalters/rd_filters",
    license_spdx="MIT",
    # SMARTS strings and integers. MolCascade compiles them with RDKit's own
    # parser; nothing in the file is executed, and the upstream Python below is
    # never imported.
    trust=PayloadTrust.DATA,
    files=(
        AssetFile(
            name="data/alert_collection.csv",
            sha256="92f292322b94e150bbb58f6726d034b7dbf3fd3aa7f87311028cb9ca736f837f",
            size_bytes=116_948,
            url=_github_blob(
                "PatWalters", "rd_filters", "216c57f997dab6c8ef97d46701564df991e85807"
            ),
            git_blob_sha1="216c57f997dab6c8ef97d46701564df991e85807",
            summary=(
                "rule_id, rule_set_name, description, smarts, max. A rule fires when a "
                "molecule matches it more than 'max' times; every shipped rule uses 0."
            ),
        ),
        AssetFile(
            name="data/rules.json",
            sha256="60911b2d7dc92e342fc7c159a46a0e8ec877ab177bbd8fa1b05cb2d322cef4ee",
            size_bytes=465,
            url=_github_blob(
                "PatWalters", "rd_filters", "6f66dcf8a15b0626161c1411baa5a74e2b43a4a2"
            ),
            git_blob_sha1="6f66dcf8a15b0626161c1411baa5a74e2b43a4a2",
            summary=(
                "The upstream default configuration: property windows, and Inpharmatica "
                "as the only rule set enabled by default."
            ),
        ),
        AssetFile(
            name="LICENSE",
            sha256="2007d43a9ee2091bbf79ac4f356cea359bfca19aaa2e2ef400b17ecc7198674b",
            size_bytes=1_072,
            url=_github_blob(
                "PatWalters", "rd_filters", "a8759e9d78dec3e3dda904824585f6f397057824"
            ),
            git_blob_sha1="a8759e9d78dec3e3dda904824585f6f397057824",
            summary="Upstream MIT licence text (Copyright 2018 Patrick Walters).",
        ),
        AssetFile(
            name="rd_filters.py",
            sha256="49d7c7b07009111c1177840bbbdbc5026c30b931ec6d08a83f185565c001629d",
            size_bytes=8_234,
            url=_github_blob(
                "PatWalters", "rd_filters", "42b10e9e16edc25b9471260d8b9f883d75a66c8f"
            ),
            git_blob_sha1="42b10e9e16edc25b9471260d8b9f883d75a66c8f",
            summary=(
                "The reference implementation, vendored so the matching semantics "
                "MolCascade reproduces can be checked against it. Never imported."
            ),
        ),
    ),
    citations=(
        Citation(
            title="rd_filters: filter compounds using RDKit and a set of structural alerts",
            authors="Walters, W. P.",
            venue="GitHub: PatWalters/rd_filters",
            year=2018,
            url="https://github.com/PatWalters/rd_filters",
            note=(
                "The tool itself has no paper. The alert sets it collects are published "
                "separately and are cited below."
            ),
        ),
        Citation(
            title=(
                "New Substructure Filters for Removal of Pan Assay Interference "
                "Compounds (PAINS) from Screening Libraries and for Their Exclusion "
                "in Bioassays"
            ),
            authors="Baell, J. B.; Holloway, G. A.",
            venue="Journal of Medicinal Chemistry 53(7), 2719-2740",
            year=2010,
            doi="10.1021/jm901137j",
            url="https://pubs.acs.org/doi/10.1021/jm901137j",
            note="Source of the 481 PAINS rules.",
        ),
        Citation(
            title=(
                "Evaluating the Utility of Compound Filters: Lessons from a "
                "Large-Scale Analysis"
            ),
            authors="Bruns, R. F.; Watson, I. A.",
            venue="Journal of Medicinal Chemistry 55(22), 9763-9772",
            year=2012,
            doi="10.1021/jm301008n",
            url="https://pubs.acs.org/doi/10.1021/jm301008n",
            note="Source of the BMS ('Bruns-Watson') rule set.",
        ),
        Citation(
            title=(
                "A Knowledge-Based Approach in Designing Combinatorial or Medicinal "
                "Chemistry Libraries for Drug Discovery"
            ),
            authors="Rishton, G. M.; Hann, M. M.; and the Glaxo Wellcome group",
            venue="Journal of Combinatorial Chemistry 1(1), 55-68",
            year=1999,
            doi="10.1021/cc9800071",
            url="https://pubs.acs.org/doi/10.1021/cc9800071",
            note=(
                "Source of the Glaxo 'hard filters'. Reported by Hann et al.; the "
                "Dundee and SureChEMBL sets derive from the same tradition of "
                "reactive-group and unwanted-chemistry lists."
            ),
        ),
    ),
    used_by=("chemistry.rd_filters_alerts@0.1.0",),
    notes=(
        "MolCascade reads the CSV and compiles each SMARTS into an RDKit "
        "`FilterCatalog` entry whose minimum match count is `max + 1`, which is the "
        "same test the upstream script applies (`len(GetSubstructMatches) > max`). "
        "It differs from upstream in one deliberate way: upstream stops at the first "
        "matching rule and returns a single string, while MolCascade reports every "
        "rule set that matched and lets each set carry its own ignore/warn/reject "
        "action, so PAINS can warn while a reactive-group set rejects.\n\n"
        "An alert is a substructure match, not experimental evidence. Baell and "
        "Walters have both written at length about over-applying these lists; the "
        "adapter therefore defaults every set to `warn` except the reactive-chemistry "
        "sets, and never silently deletes a molecule without recording the rule that "
        "removed it."
    ),
)


BUILTIN_ASSET_SPECS: tuple[AssetSpec, ...] = (SCSCORE, RD_FILTERS_ALERTS)

_BY_ID = {spec.id: spec for spec in BUILTIN_ASSET_SPECS}
if len(_BY_ID) != len(BUILTIN_ASSET_SPECS):
    raise RuntimeError("duplicate asset id in the built-in asset catalogue")


def asset_spec(asset_id: str) -> AssetSpec:
    try:
        return _BY_ID[asset_id]
    except KeyError as error:
        raise KeyError(f"unknown asset: {asset_id}") from error


def iter_assets() -> Iterator[AssetSpec]:
    return iter(BUILTIN_ASSET_SPECS)


__all__ = [
    "BUILTIN_ASSET_SPECS",
    "RD_FILTERS_ALERTS",
    "SCSCORE",
    "asset_spec",
    "iter_assets",
]
