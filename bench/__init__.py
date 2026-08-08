"""Phase 14: the benchmark harness. A ten-query set with hand-verified,
dated ground truth (`bench/queries/`), a scorer (`bench/metrics.py`), a
loader that enforces the staleness/tuning-vs-held-out disciplines
(`bench/loader.py`), and a runner that drives the real pipeline end to end
and scores the result (`bench/runner.py`).
"""
