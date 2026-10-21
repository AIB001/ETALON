# Independent ranking, placement and existing-panel calibration

Use only the diagnostic needed. These synthetic score rows illustrate actual
signatures; replace them with justified control labels and observed scores for a
scientific question. No screening is executed by these numerical helpers.

```python
from dataclasses import asdict
from etalon.campaign.calibrate import PanelMember, separation, enrichment

panel = [PanelMember("active", True, "synthetic control"),
         PanelMember("inactive", False, "synthetic control")]
scores = [{"parent_id": "active", "engine_id": "fixture", "score": -5.0,
           "direction": "LOWER_STRONGER"},
          {"parent_id": "inactive", "engine_id": "fixture", "score": -7.0,
           "direction": "LOWER_STRONGER"}]
ranking = separation(scores, panel)
print([asdict(row) for row in ranking])
# This example's inactive outranks its active; do not use this ranking to continue.
population = [{"parent_id": f"p{i}", "engine_id": "fixture", "score": score,
               "direction": "LOWER_STRONGER"}
              for i, score in enumerate((-9.0, -7.0, -5.0, -3.0))]
print([asdict(row) for row in enrichment(scores, panel, population)])
```

`enrichment` takes population **rows**, not an engine-to-score-array mapping.
It counts molecules after per-molecule pose aggregation, preserving score
direction. Keep missing, nonfinite and mixed-direction evidence explicit. A
ranking diagnostic is not gate retention; the synthetic panel is not independent
validation, and numerical output does not establish a good prospective cutoff.

To diagnose an existing panel run with retention information:

```python
from etalon.boundary.screen import Screen
from etalon.campaign.calibrate import calibrate

screen = Screen.open_existing("/absolute/panel-workspace")
result = screen.state("existing-panel-run")
if result is None:
    raise ValueError("panel run is absent; no calibration verdict")
report = calibrate(screen, result, panel, required_recall=1.0)
print(report.as_dict())
```

Use that run's actual parent IDs and evidence in `panel`. This read-only adapter
does not rerun the panel or issue a gate token. Only a downstream sweep that needs
a gate authorization should call the corresponding authorization operation.
Unknown recall and refusal remain valid reasons to stop or revise future work.
`examples/data_design_methods.py` executes the two numerical helpers independently.
