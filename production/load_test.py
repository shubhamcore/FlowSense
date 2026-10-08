"""Small repeatable smoke/load test for the control plane.

Usage:
  python -m production.load_test --url http://localhost:8000/healthz --requests 200 --concurrency 20
"""
from __future__ import annotations

import argparse
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.request import Request, urlopen


def one(url: str) -> tuple[float, int, str]:
    started = time.perf_counter()
    try:
        with urlopen(Request(url, headers={"Accept": "application/json"}), timeout=10) as response:
            response.read()
            return time.perf_counter() - started, response.status, ""
    except Exception as exc:
        return time.perf_counter() - started, 0, str(exc)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8000/healthz")
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=10)
    args = parser.parse_args()
    started = time.perf_counter()
    results = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [pool.submit(one, args.url) for _ in range(args.requests)]
        for future in as_completed(futures):
            results.append(future.result())
    durations = sorted(x[0] for x in results)
    ok = sum(x[1] == 200 for x in results)
    p95 = durations[min(len(durations) - 1, int(len(durations) * .95))] if durations else 0
    total = time.perf_counter() - started
    print({"url": args.url, "requests": args.requests, "concurrency": args.concurrency, "success": ok, "errors": args.requests - ok, "rps": round(args.requests / max(total, .001), 2), "p50_ms": round(statistics.median(durations) * 1000, 2) if durations else None, "p95_ms": round(p95 * 1000, 2) if durations else None})
    if ok != args.requests:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
