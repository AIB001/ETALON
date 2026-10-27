import pytest

pytest.importorskip('rdkit')
from rdkit import Chem, DataStructs
from rdkit.Chem import rdFingerprintGenerator
from etalon.data.similarity import catalog_neighbors


def test_neighbors_match_independent_pairwise_scores_and_keep_identity():
    rows = [dict(id='a', smiles='CCO'), dict(id='b', smiles='OCC'),
            dict(id='c', smiles='CCC'), dict(id='d', smiles='CCN'),
            dict(id='missing', smiles=None)]
    result = catalog_neighbors(rows, top_k=2)
    generator = rdFingerprintGenerator.GetMorganGenerator(radius=2, fpSize=2048,
                                                          includeChirality=True)
    for row, observed in zip(rows, result['rows']):
        fp = generator.GetFingerprint(Chem.MolFromSmiles(row['smiles']))
        pairs = [(other['id'], DataStructs.TanimotoSimilarity(fp,
            generator.GetFingerprint(Chem.MolFromSmiles(other['smiles']))))
            for other in rows[:4] if other['id'] != row['id']]
        pairs.sort(key=lambda pair: -pair[1])
        assert observed['neighbors'] == [dict(id=name, similarity=score)
                                        for name, score in pairs[:2]]
    assert result['rows'][0]['neighbors'][0] == dict(id='b', similarity=1.0)
    assert result['excluded'] == [dict(id='missing', index=4, reason='invalid_or_missing_smiles')]
    assert result['valid_count'] == 4


def test_empty_single_and_duplicate_catalogs():
    assert catalog_neighbors([])['rows'] == []
    assert catalog_neighbors([dict(id='one', smiles='C')])['rows'][0]['neighbors'] == []
    with pytest.raises(ValueError, match='unique'):
        catalog_neighbors([dict(id='x', smiles='C'), dict(id='x', smiles='N')])
