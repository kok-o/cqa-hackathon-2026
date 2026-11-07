"""Compare exact grouped reductions on synthetic CSV data in separate processes."""

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from collections import Counter
from contextlib import nullcontext
from pathlib import Path


def prepare_csv(path, rows):
    import duckdb

    from data_tools import literal

    with duckdb.connect(config={"threads": 2}) as db:
        db.execute(
            f"""
            COPY (
                SELECT printf('seg_%03d', i % 100) AS segment,
                       CASE WHEN i % 19 <> 0 THEN (i % 20001) - 10000 END AS amount_minor,
                       md5(i::VARCHAR) AS payload1,
                       md5((i + 1)::VARCHAR) AS payload2,
                       md5((i + 2)::VARCHAR) AS payload3
                FROM range({rows}) AS r(i)
            ) TO {literal(path)} (FORMAT CSV, HEADER)
            """
        )


def pandas_reduction(path, batch_size):
    import pandas as pd

    def reduce(frame):
        return frame.groupby("segment", sort=False)["amount_minor"].agg(["count", "sum"])

    counts = Counter()
    sums = Counter()
    options = {
        "usecols": ["segment", "amount_minor"],
        "dtype": {"segment": "string", "amount_minor": "Int64"},
        "keep_default_na": False,
        "na_values": {"amount_minor": [""]},
        "engine": "c",
    }
    start = time.perf_counter()
    batches = (
        nullcontext([pd.read_csv(path, **options)])
        if batch_size is None
        else pd.read_csv(path, chunksize=batch_size, **options)
    )
    with batches as chunks:
        for chunk in chunks:
            for segment, count, total in reduce(chunk).itertuples():
                counts[segment] += int(count)
                sums[segment] += int(total)
    result = [(segment, counts[segment], sums[segment]) for segment in sorted(counts)]
    return result, time.perf_counter() - start


def polars_reduction(path):
    import polars as pl

    start = time.perf_counter()
    result = (
        pl.scan_csv(path, infer_schema=False, schema_overrides={"amount_minor": pl.Int64})
        .group_by("segment")
        .agg(pl.col("amount_minor").count().alias("n"), pl.col("amount_minor").sum().alias("s"))
        .sort("segment")
        .collect(engine="streaming")
        .rows()
    )
    return result, time.perf_counter() - start


def duckdb_reduction(path, threads):
    import duckdb

    start = time.perf_counter()
    with duckdb.connect(config={"threads": threads}) as db:
        result = db.execute(
            """
            SELECT segment, COUNT(amount_minor), COALESCE(SUM(amount_minor), 0)
            FROM read_csv(?, types = {'segment': 'VARCHAR', 'amount_minor': 'BIGINT'})
            GROUP BY segment ORDER BY segment
            """,
            [str(path)],
        ).fetchall()
    return result, time.perf_counter() - start


def peak_rss_mib():
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class MemoryCounters(ctypes.Structure):
            _fields_ = [
                ("size", wintypes.DWORD),
                ("page_faults", wintypes.DWORD),
                ("peak_working_set", ctypes.c_size_t),
                ("remaining_counters", ctypes.c_size_t * 7),
            ]

        process = ctypes.WinDLL("kernel32", use_last_error=True).GetCurrentProcess
        process.restype = wintypes.HANDLE
        get_memory = ctypes.WinDLL("psapi", use_last_error=True).GetProcessMemoryInfo
        get_memory.argtypes = [wintypes.HANDLE, ctypes.POINTER(MemoryCounters), wintypes.DWORD]
        get_memory.restype = wintypes.BOOL
        counters = MemoryCounters()
        counters.size = ctypes.sizeof(counters)
        if not get_memory(process(), ctypes.byref(counters), counters.size):
            raise ctypes.WinError(ctypes.get_last_error())
        return counters.peak_working_set / 1024**2

    import resource

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / (1024**2 if sys.platform == "darwin" else 1024)


def run_worker(engine, path, chunksize, threads):
    if engine == "pandas":
        result, elapsed = pandas_reduction(path, None)
    elif engine == "pandas-chunks":
        result, elapsed = pandas_reduction(path, chunksize)
    elif engine == "polars":
        result, elapsed = polars_reduction(path)
    else:
        result, elapsed = duckdb_reduction(path, threads)
    print(json.dumps({"groups": result, "seconds": elapsed, "peak_mib": peak_rss_mib()}))


def compare(args):
    engines = ("pandas", "pandas-chunks", "polars", "duckdb")
    measurements = {engine: [] for engine in engines}
    expected = None
    expected_non_null = args.rows - (args.rows + 18) // 19
    env = dict(os.environ, POLARS_MAX_THREADS=str(args.threads))
    with tempfile.TemporaryDirectory(prefix="hackathon-bench-", dir=args.temp_dir) as scratch:
        csv = Path(scratch) / "synthetic.csv"
        prepare_csv(csv, args.rows)
        print(f"{args.rows:,} rows, {csv.stat().st_size / 1024**2:.1f} MiB CSV, {args.repeat} runs per engine")
        for repeat in range(args.repeat):
            start = repeat % len(engines)
            for engine in engines[start:] + engines[:start]:
                command = [
                    sys.executable, __file__, "--worker", engine, str(csv),
                    "--chunksize", str(args.chunksize), "--threads", str(args.threads),
                ]
                result = json.loads(subprocess.check_output(command, env=env, text=True))
                if expected is None:
                    expected = result["groups"]
                if result["groups"] != expected or sum(row[1] for row in result["groups"]) != expected_non_null:
                    raise RuntimeError(f"{engine}: exact aggregate differs")
                measurements[engine].append(result)

    print(f"{'engine':<16} {'median seconds':>15} {'max peak MiB':>15}")
    for engine, results in measurements.items():
        elapsed = statistics.median(row["seconds"] for row in results)
        memory = max(row["peak_mib"] for row in results)
        print(f"{engine:<16} {elapsed:>15.3f} {memory:>15.1f}")
    print("OK: all per-segment counts and integer sums match")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=500_000)
    parser.add_argument("--chunksize", type=int, default=50_000)
    parser.add_argument("--repeat", type=int, default=2)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--temp-dir", type=Path)
    parser.add_argument("--worker", nargs=2, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if min(args.rows, args.chunksize, args.repeat, args.threads) < 1:
        parser.error("rows, chunksize, repeat and threads must be positive")
    if args.worker:
        engine, path = args.worker
        run_worker(engine, Path(path), args.chunksize, args.threads)
    else:
        compare(args)


if __name__ == "__main__":
    main()
