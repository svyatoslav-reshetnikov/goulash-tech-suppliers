"""Запуск проверки модели.

    python -m scripts.model_check.run --provider yandex --runs 3
    python -m scripts.model_check.run --provider gigachat --runs 3

Нужны ключи в .env (см. .env.example). Результаты печатаются таблицей и сохраняются в
scripts/model_check/out/ (каталог не попадает в репозиторий).
"""

import argparse
import json
import statistics
import sys
import time
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from app.config import MissingSettingError, get_settings

from .cases import CASES, CaseResult, tool_calling
from .clients import get_client

OUT_DIR = Path(__file__).parent / "out"


def run_case(name: str, client, tool_mode: str) -> CaseResult:
    if name == "tools":
        return tool_calling(client, tool_mode)
    return CASES[name](client)


def summarize(name: str, results: list[CaseResult]) -> dict:
    ok = [r for r in results if r.error is None]
    notes = Counter(n for r in results for n in r.notes)
    errors = [r.error for r in results if r.error]
    return {
        "case": name,
        "passed": f"{sum(r.passed for r in results)}/{len(results)}",
        "score": round(statistics.mean(r.score for r in ok), 2) if ok else 0.0,
        "seconds": round(statistics.median(r.seconds for r in ok), 1) if ok else 0.0,
        "tokens": f"{round(statistics.mean(r.prompt_tokens for r in ok))}/{round(statistics.mean(r.completion_tokens for r in ok))}" if ok else "-",
        "issues": errors[0] if errors else "; ".join(f"{n} (x{c})" for n, c in notes.most_common(3)) or "-",
    }


def print_table(model: str, rows: list[dict]) -> None:
    print(f"\nМодель: {model}\n")
    print("| Сценарий | Пройдено | Балл | Время, с (медиана) | Токены вх/исх | Замечания |")
    print("|---|---|---|---|---|---|")
    for r in rows:
        print(f"| {r['case']} | {r['passed']} | {r['score']} | {r['seconds']} | {r['tokens']} | {r['issues']} |")


def main() -> int:
    parser = argparse.ArgumentParser(description="Проверка модели на задачах агента")
    parser.add_argument("--provider", choices=["yandex", "gigachat"], default="yandex")
    parser.add_argument("--runs", type=int, default=3, help="сколько раз повторить каждый сценарий")
    parser.add_argument("--cases", nargs="+", choices=list(CASES), default=list(CASES))
    parser.add_argument("--tool-mode", choices=["native", "json"], default="native",
                        help="native — вызов функций средствами API, json — запасной режим JSON-действий")
    args = parser.parse_args()

    try:
        client = get_client(args.provider, get_settings())
    except MissingSettingError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1

    all_results: dict[str, list[CaseResult]] = {}
    for name in args.cases:
        all_results[name] = []
        for i in range(args.runs):
            print(f"{name}: прогон {i + 1}/{args.runs}...", file=sys.stderr, flush=True)
            all_results[name].append(run_case(name, client, args.tool_mode))

    print_table(client.name, [summarize(n, r) for n, r in all_results.items()])

    OUT_DIR.mkdir(exist_ok=True)
    out_file = OUT_DIR / f"{args.provider}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out_file.write_text(
        json.dumps({n: [asdict(r) for r in rs] for n, rs in all_results.items()}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nПодробные результаты: {out_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
