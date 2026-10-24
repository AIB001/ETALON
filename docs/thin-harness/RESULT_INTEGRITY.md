# Scientific result integrity and SND1 reassessment

`etalon.research.result_integrity` checks the completeness and identity of already
produced pose evidence. It does not execute docking, determine affinity, or
certify scientific truth. `examples/snd1_scale_screen.py` freezes its actual
PoseBusters configuration and implementation before a new batch.

## Typed completeness

`freeze_check_contract(config, version=..., implementation=...)` derives the
required public check names from the configuration's selected binary outputs,
renaming rules, suffixes, and normalization. PoseBusters 0.6.5's actual `dock`
configuration produces 22 names. Requirements never come from the subset of
fields present in a result row.

`validate_verifier` requires the exact check set, strict Boolean values, one
unique row for every independently counted native pose, consistent aggregate
validity, and valid reference-dependent RMSD fields. A complete measurement may
be physically rejected. Missing, malformed, failed, or incompatible evidence
has `status=unknown` and nullable completeness, rather than a measured false
validity. Top1 extraction preserves that distinction.

`result_binding` records the compiled input digest, exact method, frozen protocol,
ligand, pool, receptor, reference, and checker contract. Native cache validation
reuses the original receipt's artifact hashes and additionally checks its
request, attempt, stage, completed execution evidence, and expected output.
Cached result metadata, rankings, verifier identity, top1 observations, and
integrity report must match verified inputs. An existing result cannot launch a
missing native attempt. Legacy results require explicit versioned reassessment.

The ordinary evaluation contract accepts
`integrity_required_checks(input_name)` as required checks over an archived
integrity summary. Unknown completeness consequently produces `insufficient`
under the existing contract implementation. This is an optional domain adapter;
no new journal, scientific oracle, scheduler, or adoption rule is introduced.

## Candidate-relative confirmation

`confirmation_eligibility` consumes a candidate confirmation freeze and the
activated series visibility overlay. The freeze must bind an actual historical
declaration's file identity, complete candidate/protocol digest, panel, and
timestamp. The batch checks that candidate digest against its actual frozen
implementation and protocol. An include flag alone cannot establish eligibility.

A candidate frozen before a feedback disclosure may retain its historical
candidate-relative prospective role. A new candidate formed after disclosure
must treat the exposed series as seen. The source chemical split remains
unchanged. The timestamp comparison verifies supplied historical declaration
bytes; it does not independently attest wall-clock chronology.

For the 2026-10-24 P3 overlay, 67 confirmation parents in 12 series have admitted
related feedback, while 61 parents in 26 series remain untouched. Only the 12
listed exact tasks were physically executed by P3. An untouched inventory does
not by itself establish a new candidate freeze, execution, or scientific result.

## Versioned replay

`examples/reassess_snd1_results.py` reads an immutable completed-originals
manifest, its copied raw files, the old protocol/runtime, and an exact visibility
overlay. It checks original request/receipt/file identities and reconstructs an
explicit retrospective result binding. It writes new verifier and result
versions in a separate directory, preserving source splits, raw observations,
failures, and unresolved attempts. It never calls the docking/CNN executor.

An optional `--checker-smoke` performs one actual PoseBusters replay on a cached
pool and records its wall/CPU cost separately. Later code revisions may use
`--existing-checker-smoke PATH`; the original replay retains its identity and
cost, and the later invocation records zero new verifier executions. Reanalysis
processing costs are separate from the historical native and verifier costs.

The first 2026-10-24 freeze contains 1,896 original results, with 1,260
development and 636 calibration tasks. A separate final completion freeze covers
all 2,304 results, preserving 1,536 development and 768 calibration tasks. These
are retrospective observations, not new independent confirmation. Public source tests use explicitly synthetic
fixtures. The real deployment's exact paths, runtime pins, original closure,
and per-version costs live in the integration handoff, outside this repository.
