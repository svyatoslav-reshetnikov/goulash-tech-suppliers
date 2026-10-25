"""Метрики по последнему (или указанному) прогону и ручной разметке labels.csv. Бесплатно: ничего не запускает.

    python -m scripts.eval.score
    python -m scripts.eval.score scripts/eval/out/20261026-120000/results.json
"""

import argparse
import json
import sys
from pathlib import Path

from .metrics import LABELS, OUT_DIR, read_labels, report, score_case


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("results", nargs="?", type=Path)
    parser.add_argument("--labels", type=Path, default=LABELS)
    args = parser.parse_args()
    path = args.results or max(OUT_DIR.glob("*/results.json"), default=None)
    if path is None:
        print("Прогонов нет: сначала python -m scripts.eval.run --yes", file=sys.stderr)
        return 1
    results = json.loads(path.read_text(encoding="utf-8"))
    labels = read_labels(args.labels)
    print(f"Прогон: {path}\n")
    print(report([score_case(case, labels) for case in results]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
