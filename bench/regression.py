"""Phase 14 CI regression check (phase doc: "Fails on: sentence binding
below 100%, recall dropping more than 10 points from baseline, cost per run
rising more than 50%, or contradiction firing rate reaching zero").

Pure comparison logic over two `QueryScore` sets — today's freshly-scored
results (`bench.runner --tuning --cached-only`, run by `bench.yml`) against
`bench/results/baseline.json` (the numbers `docs/benchmark.md` currently
documents, promoted there by hand once a real run's numbers are trusted).
No Postgres, no network — this only ever reads JSON already on disk.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from bench.runner import RESULTS_DIR, QueryScore

BASELINE_PATH = Path(__file__).parent / "results" / "baseline.json"

RECALL_DROP_TOLERANCE = 0.10
COST_INCREASE_TOLERANCE = 0.50
# The trap query's id in the checked-in query set (bench/queries/q07.yaml) —
# named explicitly rather than inferred from `role`, since a regression
# check should fail loud if the trap ever moves without this being updated.
TRAP_QUERY_ID = "q07"


def _load_scores(path: Path) -> dict[str, QueryScore]:
    scores: dict[str, QueryScore] = {}
    for f in sorted(path.glob("*.json")):
        data = json.loads(f.read_text())
        scores[data["query_id"]] = QueryScore(**data)
    return scores


def load_latest_results(*, results_dir: Path = RESULTS_DIR) -> dict[str, QueryScore]:
    dated_dirs = sorted((d for d in results_dir.iterdir() if d.is_dir()), reverse=True)
    if not dated_dirs:
        raise FileNotFoundError(f"no dated result directories under {results_dir}")
    return _load_scores(dated_dirs[0])


def load_baseline(*, baseline_path: Path = BASELINE_PATH) -> dict[str, QueryScore]:
    if not baseline_path.exists():
        raise FileNotFoundError(
            f"no baseline at {baseline_path} — run once against real data and "
            "promote a results snapshot to bench/results/baseline.json first"
        )
    data = json.loads(baseline_path.read_text())
    return {qid: QueryScore(**payload) for qid, payload in data.items()}


def check_regression(current: dict[str, QueryScore], baseline: dict[str, QueryScore]) -> list[str]:
    """Returns a list of human-readable failure reasons — empty means green."""
    failures: list[str] = []

    for qid, score in current.items():
        if score.sentence_binding_rate < 1.0:
            failures.append(
                f"{qid}: sentence_binding_rate={score.sentence_binding_rate:.2f} < 1.0 "
                "(Phase 11's own enforcement is broken — a bug, not a tuning target)"
            )

        base = baseline.get(qid)
        if base is None:
            continue  # a query added since the baseline was promoted; nothing to compare

        if base.competitor_recall - score.competitor_recall > RECALL_DROP_TOLERANCE:
            failures.append(
                f"{qid}: competitor_recall dropped from {base.competitor_recall:.2f} to "
                f"{score.competitor_recall:.2f} (> {RECALL_DROP_TOLERANCE:.0%} tolerance)"
            )

        if base.cost_usd > 0 and score.cost_usd > base.cost_usd * (1 + COST_INCREASE_TOLERANCE):
            failures.append(
                f"{qid}: cost_usd rose from ${base.cost_usd:.4f} to ${score.cost_usd:.4f} "
                f"(> {COST_INCREASE_TOLERANCE:.0%} increase — a silently broken prompt cache "
                "looks exactly like this)"
            )

    trap = current.get(TRAP_QUERY_ID)
    trap_baseline = baseline.get(TRAP_QUERY_ID)
    if (
        trap is not None
        and trap_baseline is not None
        and trap_baseline.contradiction_fired
        and not trap.contradiction_fired
    ):
        failures.append(
            f"{TRAP_QUERY_ID}: contradiction_fired is False — the trap query's contradiction "
            "detector stopped firing"
        )

    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m bench.regression")
    parser.add_argument("--results-dir", type=Path, default=RESULTS_DIR)
    parser.add_argument("--baseline", type=Path, default=BASELINE_PATH)
    args = parser.parse_args(argv)

    current = load_latest_results(results_dir=args.results_dir)
    baseline = load_baseline(baseline_path=args.baseline)
    failures = check_regression(current, baseline)

    if failures:
        print(f"REGRESSION: {len(failures)} failure(s)")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"OK: {len(current)} quer(y/ies) checked against baseline, no regressions")
    return 0


if __name__ == "__main__":
    sys.exit(main())
