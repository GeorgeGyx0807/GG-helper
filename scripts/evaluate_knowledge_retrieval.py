#!/usr/bin/env python3
"""Evaluate Poppy retrieval against a user-curated JSONL question set.

Each line contains `question`, `expected_paths`, and optionally `expected_quotes`,
`scope_kind`, and `scope_id`. The script never sends document text to a model.
"""

import argparse
import json
import statistics
import time
from pathlib import Path

from poppy.features.document_index import DocumentIndex
from poppy.storage import AppPaths, DesktopDatabase


def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--data-dir", type=Path, default=AppPaths.default().root)
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()
    cases = [json.loads(line) for line in args.dataset.read_text(encoding="utf-8").splitlines() if line.strip()]
    database = DesktopDatabase(AppPaths(args.data_dir).ensure().database)
    index = DocumentIndex(database)
    grants = database.list_grants()
    recalls, reciprocal_ranks, quote_scores, latencies = [], [], [], []
    details = []
    for case in cases:
        started = time.perf_counter()
        rows = index.search(
            case["question"],
            grants,
            limit=args.limit,
            scope={"kind": case.get("scope_kind", "all"), "id": case.get("scope_id", "")},
        )
        latencies.append((time.perf_counter() - started) * 1000)
        expected = {str(Path(path).expanduser().resolve()) for path in case.get("expected_paths", [])}
        ranks = [rank for rank, row in enumerate(rows, start=1) if str(Path(row["path"]).resolve()) in expected]
        recalls.append(1.0 if ranks else 0.0)
        reciprocal_ranks.append(1.0 / min(ranks) if ranks else 0.0)
        quotes = [str(item).casefold() for item in case.get("expected_quotes", [])]
        if quotes:
            evidence = "\n".join(str(row.get("content") or "") for row in rows).casefold()
            quote_scores.append(sum(quote in evidence for quote in quotes) / len(quotes))
        details.append({"question": case["question"], "hit": bool(ranks), "rank": min(ranks) if ranks else None})
    report = {
        "questions": len(cases),
        "recall_at_20": round(statistics.fmean(recalls), 4) if recalls else 0.0,
        "mrr": round(statistics.fmean(reciprocal_ranks), 4) if reciprocal_ranks else 0.0,
        "quote_recall": round(statistics.fmean(quote_scores), 4) if quote_scores else None,
        "latency_p50_ms": round(statistics.median(latencies), 3) if latencies else 0.0,
        "latency_p95_ms": round(percentile(latencies, 0.95), 3) if latencies else 0.0,
        "details": details,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
