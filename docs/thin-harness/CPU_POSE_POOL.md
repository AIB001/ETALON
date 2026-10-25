# CPU pose pool as a selection input

`research.pose_pool.diagnose_cpu_pool` accepts the saved CPU pool, conditioning
receptor, their pinned byte identities and the already released pocket center.
It returns an ordinary JSON diagnostic suitable for the existing Artifact API.
It does not accept gold coordinates, CNN scores, affinity labels or verifier
outcomes. Only the empirical `minimizedAffinity` SDF property is used.

The fields cover empirical score gaps, pocket contacts, close heavy-atom pairs,
distance-only polar pairs and symmetry-aware pool diversity in the fixed
receptor frame. Polar pairs do not establish directional hydrogen bonds; the
close-pair count does not replace PoseBusters. Single-pose score gaps are unknown,
not zero. Malformed poses, changed identities and exceeded bounds fail without
silently dropping a slot. Input files are rehashed after extraction.

This information becomes available **after CPU search, before optional CNN**.
The caller must record its MethodUse and branch decision before starting CNN,
including the diagnostic Artifact, exact resource/workset part, policy version
and retained comparator. `decision_order_verified` remains false in a standalone
diagnostic: the execution ledger must prove ordering. Existing input-only rules
keep their original pre-search contract; this is a different candidate workflow.

`examples/analyze_cpu_pose_pools.py` reuses completed task receipts and seals a
conditioning-only descriptor file before separately joining already-seen
outcomes. It never launches docking, fits a rule or changes the default. Record
diagnostic CPU cost in addition to search/CNN cost. A full-alternatives audit and
the selected path's attributed cost remain separate.

Subsequent prospective evaluation needs newly frozen independent cases, fixed
verifier/quality standards and case-grouped statistics. Neither a retrospective
descriptor nor a better-looking subgroup proves improved branch selection.
