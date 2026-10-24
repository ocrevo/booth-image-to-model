"""POC scoring: code metrics (metrics.py) and the score.json assembler (score.py). See docs/poc/03-scoring-spec.md.

`aggregate_panel` is the single implementation that turns judge.v1 scoring responses into panel scores; the panel tool imports it
from here instead of keeping a second copy.
"""
from .score import aggregate_panel, validate_judge, InputError  # noqa: F401
