"""Independent chemistry work; global identity reduction stays in standardize."""
from collections import Counter
import pyarrow as pa
import pyarrow.parquet as pq

from molcascade.chemistry.identity import ParentStandardizer, StandardizationFailure
from molcascade.contracts import DECISION_V1, PARENT_V1
from molcascade.errors import PluginError
from molcascade.parallel import ShardTask, ShardOutcome, iter_shard_batches

REGISTRATION_SCHEMA = pa.schema([
    pa.field('source_record_id', pa.string(), nullable=False),
    pa.field('raw_format', pa.string(), nullable=False),
    *[pa.field(f.name, f.type) for f in PARENT_V1.schema if f.name != 'duplicate_count'],
])


def standardize_shard(task: ShardTask) -> ShardOutcome:
    # Imported here to avoid a registry/import cycle during process spawning.
    from molcascade.plugins.builtin.standardize import RDKitStandardizePlugin, StandardizePluginConfig
    config = StandardizePluginConfig.model_validate(task.config)
    standardizer = ParentStandardizer(config.identity_policy)
    formats = Counter()
    rejects = total = decision_count = 0
    with pq.ParquetWriter(task.output_paths['registrations'], REGISTRATION_SCHEMA) as records, \
         pq.ParquetWriter(task.output_paths['decisions'], DECISION_V1.schema) as decisions:
        for batch in iter_shard_batches(task, batch_size=min(config.batch_size, 4096)):
            output, events = [], []
            for row in batch.to_pylist():
                total += 1
                source = row.get('source_record_id')
                if not isinstance(source, str) or not source:
                    raise PluginError('invalid source_record_id', code='STANDARDIZE_SOURCE_ID_INVALID')
                v2 = 'raw_structure' in row
                fmt = row.get('raw_format', 'UNSPECIFIED') if v2 else RDKitStandardizePlugin._v1_raw_format(row)
                fmt = fmt if isinstance(fmt, str) else 'UNSPECIFIED'
                formats[fmt] += 1
                rec = {'source_record_id': source, 'raw_format': fmt}

                def event(outcome, reason, **detail):
                    events.append(dict(entity_id=source, entity_kind='SOURCE_RECORD',
                        stage_id=task.stage_id, outcome=outcome, reason_code=reason, rule_id=None,
                        detail=RDKitStandardizePlugin._audit_detail(fmt, **detail)))

                try:
                    parent = standardizer.standardize(**(
                        dict(raw_format=row.get('raw_format'), raw_structure=row.get('raw_structure')) if v2
                        else dict(raw_smiles=row.get('raw_smiles'), raw_molblock=row.get('raw_molblock'))))
                except StandardizationFailure as exc:
                    rejects += 1
                    event('REJECT', exc.reason_code, message=exc.detail)
                else:
                    rec.update({f.name: getattr(parent, f.name) for f in PARENT_V1.schema if f.name != 'duplicate_count'})
                    event('PASS', 'STANDARDIZED', parent_id=parent.parent_id)
                    for notice in parent.notices:
                        event('WARN', notice.code, message=notice.detail)
                output.append(rec)
            records.write_table(pa.Table.from_pylist(output, schema=REGISTRATION_SCHEMA))
            decisions.write_table(pa.Table.from_pylist(events, schema=DECISION_V1.schema))
            decision_count += len(events)
    return ShardOutcome(rows_in=total, rows_out={'registrations': total, 'decisions': decision_count},
                        metadata={'format_counts': dict(formats), 'reject_count': rejects})
