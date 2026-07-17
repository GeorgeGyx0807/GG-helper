#!/usr/bin/env python3
"""Measure local LanceDB ingestion and hot-search latency without user data."""

import argparse
import json
import math
import resource
import statistics
import tempfile
import time
from pathlib import Path

from poppy.features.vector_store import LanceVectorStore


def percentile(values, percentile):
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * percentile) - 1))
    return ordered[index]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--chunks", type=int, default=100_000)
    parser.add_argument("--documents", type=int, default=100)
    parser.add_argument("--dimension", type=int, default=384)
    parser.add_argument("--queries", type=int, default=50)
    args = parser.parse_args()
    chunks_per_document = math.ceil(args.chunks / args.documents)
    with tempfile.TemporaryDirectory(prefix="poppy-vector-benchmark-") as directory:
        store = LanceVectorStore(Path(directory) / "vectors")
        started = time.perf_counter()
        chunk_id = 1
        for document_index in range(args.documents):
            rows = []
            for row_index in range(chunks_per_document):
                if chunk_id > args.chunks:
                    break
                vector = [0.0] * args.dimension
                vector[(document_index + row_index) % args.dimension] = 1.0
                rows.append({"chunk_id": chunk_id, "vector": vector})
                chunk_id += 1
            store.replace_document(
                f"document-{document_index}", "benchmark", rows, "benchmark-384", args.dimension
            )
        ingest_seconds = time.perf_counter() - started
        query_vector = [0.0] * args.dimension
        query_vector[7] = 1.0
        latencies = []
        for _ in range(args.queries):
            started = time.perf_counter()
            hits = store.search(query_vector, "benchmark-384", args.dimension, limit=20, source_ids=["benchmark"])
            latencies.append((time.perf_counter() - started) * 1000)
        maximum_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        print(json.dumps({
            "chunks": args.chunks,
            "dimension": args.dimension,
            "ingest_seconds": round(ingest_seconds, 3),
            "query_p50_ms": round(statistics.median(latencies), 3),
            "query_p95_ms": round(percentile(latencies, 0.95), 3),
            "maximum_rss_bytes": int(maximum_rss),
            "hits": len(hits),
            "vector_error": store.last_error,
        }, ensure_ascii=False))


if __name__ == "__main__":
    main()
