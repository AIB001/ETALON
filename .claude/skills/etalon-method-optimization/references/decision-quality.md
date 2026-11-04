# Improve the decision, not only the fitted score

Choose the part that addresses the current result. Keep the scientific endpoint
and resource envelope clear, and use the smallest informative calculation.

Observation aggregation: distinguish another reading of the same execution from
a new replicate, pose, chemical state or protocol. Reusing a receipt should not
change the data weight. A best-of-repeat score has a different meaning from a
replicate mean. Use paired examples to learn a conversion between protocols;
different selected populations alone cannot identify that conversion. Separate
scores remain useful when a common scale is not justified.

Prediction: label-dependent preprocessing, including size correction, protocol
alignment and calibration, belongs inside the training fold. Select model settings
without reusing the final evaluation estimate as their evidence. Predictions
saved before a new batch give a practical prospective check. Use groups or
scaffolds when related examples would otherwise overstate generalization.

Acquisition: ask what a measurement could change in the current hypothesis or
shortlist, and what it costs. Better average proxy error is only one possible
reason to buy information. The host can choose exploration, confirmation,
alternative states, a different tool or stopping; no acquisition formula is
required by the framework.

Selection: compare selectors on the same observations, and acquisition methods
with a common selector when distinguishing their contributions matters. Inspect
which candidates move because of new data, calibration, priors or diversity.
Past nominees can be reconsidered without permanently preserving an early model's
mistakes. An evaluation-only label must not decide which nominee to retain.

Callable behavior: normalize the documented input type once; return no requests
at zero budget and at most the available unique candidates. Test the branch you
changed with a meaningful small input. A frozen output replay alone does not
exercise sparse data, failure or exhausted-budget behavior. Small optional helpers
can remove these mechanics while leaving the method's choice untouched.

`etalon.research.method_tools` provides `observation_records` for dataframe/row
normalization, `bounded_acquisition` to retain the author's unique proposals
within remaining slots, and `crossfit_predictions` for a caller-supplied complete
prediction pipeline. Folds, features, model and protocol are yours to choose.
Fit label-dependent transforms inside its callback; a prefit closure can still
leak information and is not made valid by using the helper.

Use existing observation/result references and a short explanation of the next
choice. Save a revised method only when that helps continued use; the framework
does not require a new research graph, review form or promotion step.
