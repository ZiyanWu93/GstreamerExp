"""Evaluation dimensions — one module per dimension.

The five dimensions form an exhaustive, irreducible set of axes for
evaluating a teleoperation video pipeline. Every measurement we make
serves exactly one dimension; every research question reduces to a
sub-question within one of them.

| Dimension   | Question it answers                                    |
|-------------|--------------------------------------------------------|
| throughput  | Did the data get there, and at what byte cost?         |
| quality     | How visually intelligible was what arrived?            |
| latency     | How fresh was what arrived?                            |
| adaptation  | How well did the controller match output to capacity?  |
| stability   | How predictably did the pipeline behave over time?     |

Each module exports the same three functions — `analyze_run`,
`analyze_experiment`, `render` — so the `evaluate.py` driver can
loop over them without knowing what each dimension measures.
"""

from analysis.dimensions import (
    throughput, quality, latency, adaptation, stability,
)
from metrics import DIMENSIONS as _METRIC_DIMENSIONS

# Order is the order they appear in the comprehensive report.
DIMENSION_MODULES = [throughput, quality, latency, adaptation, stability]

# Structural invariant: this module list and metrics.DIMENSIONS must
# stay in lockstep. A metric tagged with dimension X must have a
# corresponding analysis module named X, and vice versa. Caught at
# import time so a missing or extra module surfaces immediately.
_module_names = tuple(m.name for m in DIMENSION_MODULES)
if set(_module_names) != set(_METRIC_DIMENSIONS):
    raise AssertionError(
        f"dimension drift: analysis modules {_module_names} differ from "
        f"metrics.DIMENSIONS {_METRIC_DIMENSIONS}")
