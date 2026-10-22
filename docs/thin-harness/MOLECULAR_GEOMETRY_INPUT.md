# Molecular geometry bindings / 1

Use `etalon-molecular-geometry/1` as the `geometry` constraint binding inside
the existing `etalon-scientific-input/1` envelope. ResearchJournal continues to
store the ordinary input, consumption, method, result, claim and checkpoint
artifacts. This is a domain contract, not a new execution phase or scheduler.

| Layer | Required meaning | Consumer proof |
|---|---|---|
| Identity | Exact ordered graph including charge, isotope and stereo; input atom count; pinned receptor frame | Reject changed graph/order/frame before execution |
| Chemical assumptions | Exact input tautomer/protonation; donor/acceptor typing source; receptor-state assumptions | Refuse unsupported acceptor types; retain uncertainty |
| Hard fixed | Explicit atom indices, reference is each execution's input | Full-precision coordinate displacement <= declared tolerance |
| Hard mobility | Acyclic single-bond axis, complete downstream component, input-relative angular interval in degrees | Optimize only those angles; no silent extra Cartesian/H motion |
| Hard invariants | Bond lengths, bond angles and ring internal geometry | Compare raw full-precision output to input; SDF rounding checked separately |
| Soft position | `flat_bottom_A` and `k_kcal_mol_A2` | Report penalty and observed displacement; radius is never called a maximum |
| Soft contact | Named ligand/protein atoms, distance interval, units and evidence | Report distance/penalty; distance alone does not certify hydrogen bonding |
| Direction | Explicit donor/H/acceptor geometry, chemical typing, H provenance, degree target and radian-based coefficient | Report angles, penalties and sensitivity to allowed variables |
| Excluded information | Named interaction plus reason | Keep as evidence/context without pretending it affected optimization |

The current consumer supports a heuristic linear D-H-A penalty. Hydrogens may
be experimentally observed or explicitly constructed; constructed planar donor
hydrogens are assumptions, never promoted to measured coordinates. An optional
acceptor-axis term is limited to graph-typed aromatic N with two covalent heavy
neighbors. No ether lone-pair geometry or full protein force field is inferred.

Every subconstraint lists evidence IDs; the surrounding binding connects those
conditioning observations and explicit host assumptions to source hashes.
Evaluation-only coordinates cannot enter a binding through an interpretation.
The same contract used in an ablation must disclose disabled direction terms
in its subconsumption receipt. A consumed but zero-sensitivity constraint is
reported as ineffective for the allowed motion, not as scientific confirmation.

Implementation: `research.geometry_input.validate_geometry` validates the
declaration without importing molecular tools. `snd1_torsion_workflow` performs
graph cuts, chemical typing, bounded rotations and geometric verification.
The underlying soft position/contact/pocket terms and scientific-input compiler
are reused. Existing Cartesian input `/1` remains unchanged and readable.

SND1 example: phenyl--S, S--N, N--heteroaryl rotations +/-10 degrees and
phenyl--methoxy-O +/-20 degrees. Attached atoms move as rigid graph components.
The reported N--Arg NE contact remains distance-only because the ligand N is a
donor under the declared graph. N2/Arg NH2 and O/Asn ND2 use explicitly assumed
donor H. This scope does not establish binding free energy or transferable gain.
