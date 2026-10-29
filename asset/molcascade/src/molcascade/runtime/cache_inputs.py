"""Content identities for external inputs with an explicit immutable contract.

Do not strip arbitrary paths: most plugin paths still name mutable inputs.
Only the registered-bundle adapters pin their inputs sufficiently to separate
the locator from identity. Their current locators are verified before lookup.
"""
from pathlib import Path

from molcascade.artifacts.hashing import sha256_file
from molcascade.artifacts.store import LocalArtifactStore
from molcascade.errors import PluginError


REGISTERED_INPUT_PLUGINS = frozenset({
    'source.registered_parents', 'docking.registered_conformers',
})


def configuration_identity(plugin_id, config):
    identity = dict(config)
    if plugin_id not in REGISTERED_INPUT_PLUGINS:
        return identity
    from rdkit import rdBase

    # artifact_id names the complete immutable bundle, including its producer.
    identity.pop('path', None)
    selection = identity.pop('selection_path', None)
    if selection is not None:
        suffix = Path(selection).suffix.lower()
        # Parsing is semantic: equal bytes interpreted as CSV/TSV need not agree.
        identity['selection_format'] = ('parquet' if suffix == '.parquet'
            else 'tsv' if suffix in {'.tsv', '.tab'} else 'csv')
    identity['input_identity_schema'] = 'molcascade.registered-content/v1'
    identity['runtime_rdkit_version'] = rdBase.rdkitVersion
    return identity


def verify_cache_inputs(stage):
    """Check the supplied locations even when a cached result already exists."""
    if stage.descriptor.id not in REGISTERED_INPUT_PLUGINS:
        return
    config = stage.config
    LocalArtifactStore(config['path'], create=False).verify(config['artifact_id'])
    selection = config.get('selection_path')
    if selection is not None and sha256_file(Path(selection)) != config['selection_sha256']:
        raise PluginError('parent selection changed', code='REGISTERED_SELECTION_CHANGED')
