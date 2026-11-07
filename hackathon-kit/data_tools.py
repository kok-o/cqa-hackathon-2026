"""Local CSV/Parquet inspection, staging and key-based partitioning."""

import argparse
import json
import os
import tempfile
from pathlib import Path

import duckdb


def identifier(name):
    return '"' + name.replace('"', '""') + '"'


def literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def source_relation(db, source, delimiter):
    if not source.is_file():
        raise ValueError(f"Expected a local file: {source}")
    if source.suffix.lower() == ".parquet":
        return db.read_parquet(str(source))
    if any(source.name.lower().endswith(suffix) for suffix in (".csv", ".tsv", ".csv.gz", ".tsv.gz")):
        separator = delimiter or ("\t" if ".tsv" in source.name.lower() else ",")
        return db.read_csv(str(source), delimiter=separator, header=True, all_varchar=True)
    raise ValueError("Supported inputs: .csv, .tsv, .csv.gz, .tsv.gz, .parquet")


def require_columns(relation, names):
    missing = set(names) - set(relation.columns)
    if missing:
        raise ValueError(f"Missing columns: {', '.join(sorted(missing))}")


def inspect(db, relation, args):
    require_columns(relation, [column for column in (args.key, args.time) if column])
    if args.limit:
        relation = relation.limit(args.limit)
    relation.create_view("source_data")
    fields = ["COUNT(*)"]
    fields.extend(f"COUNT({identifier(column)})" for column in relation.columns)
    if args.key:
        fields.append(f"COUNT(DISTINCT {identifier(args.key)})")
    if args.time:
        timestamp = f"TRY_CAST({identifier(args.time)} AS TIMESTAMPTZ)"
        fields.extend([f"COUNT({timestamp})", f"MIN({timestamp})", f"MAX({timestamp})"])
    result = db.execute(f"SELECT {', '.join(fields)} FROM source_data").fetchone()
    rows, *counts = result
    width = len(relation.columns)
    report = {
        "source": str(args.source),
        "file_bytes": args.source.stat().st_size,
        "scope": f"first {args.limit} rows" if args.limit else "full file",
        "rows_inspected": rows,
        "columns": {
            name: {"dtype": str(dtype), "nulls": rows - count}
            for name, dtype, count in zip(relation.columns, relation.types, counts[:width], strict=True)
        },
    }
    non_null = dict(zip(relation.columns, counts[:width], strict=True))
    if args.key:
        report["key"] = {
            "column": args.key,
            "distinct_non_null": counts[width],
            "duplicate_rows_beyond_first": non_null[args.key] - counts[width],
        }
        width += 1
    if args.time:
        parsed, earliest, latest = counts[width:]
        report["time"] = {
            "column": args.time,
            "unparseable_non_null": non_null[args.time] - parsed,
            "min_utc": earliest,
            "max_utc": latest,
        }
    print(json.dumps(report, indent=2, ensure_ascii=True, default=str))


def stage(db, relation, args):
    if args.output.suffix.lower() != ".parquet":
        raise ValueError("The output filename must end in .parquet")
    if args.output.exists():
        raise FileExistsError(args.output)
    relation.create_view("source_data")
    casts = {}
    for setting in args.types:
        name, separator, dtype = setting.partition("=")
        if not separator or not name or not dtype or name in casts:
            raise ValueError(f"Expected one COLUMN=TYPE per column, got: {setting}")
        casts[name] = duckdb.sqltype(dtype)
    require_columns(relation, casts)
    if casts:
        replacements = [
            f"CAST({identifier(name)} AS {dtype}) AS {identifier(name)}"
            for name, dtype in casts.items()
        ]
        relation = db.sql(f"SELECT * REPLACE ({', '.join(replacements)}) FROM source_data")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".staging-", dir=args.output.parent) as scratch:
        temporary = Path(scratch) / "data.parquet"
        relation.write_parquet(str(temporary), compression="zstd")
        # Publish only the complete file and protect any existing result.
        os.link(temporary, args.output)
    print(args.output.resolve())


def shard(db, relation, args):
    require_columns(relation, [args.key])
    if args.output.exists():
        raise FileExistsError(args.output)
    if any(column.lower() == "bucket" for column in relation.columns):
        raise ValueError("Input already has a column called bucket")
    relation.create_view("source_data")
    key = identifier(args.key)
    missing = db.execute(f"SELECT COUNT(*) FROM source_data WHERE {key} IS NULL").fetchone()[0]
    if missing:
        raise ValueError(f"{missing} null keys. Resolve or quarantine them before hashing.")

    # MD5 hex is reproducible for identical UTF-8 key strings.
    bucket = f"CAST('0x' || LEFT(md5(CAST({key} AS VARCHAR)), 8) AS UBIGINT) % {args.buckets}"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".sharding-", dir=args.output.parent) as scratch:
        temporary = Path(scratch) / "parts"
        db.execute(
            f"""
            COPY (SELECT *, {bucket} AS bucket FROM source_data)
            TO {literal(temporary)} (FORMAT PARQUET, PARTITION_BY (bucket), COMPRESSION ZSTD)
            """
        )
        if not temporary.exists():
            raise ValueError("The source has no rows")
        temporary.rename(args.output)
    print(args.output.resolve())


def positive_int(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("Use a positive integer")
    return number


def make_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("source", type=Path)
    common.add_argument("--delimiter", help="CSV separator; defaults to comma or tab for .tsv")
    common.add_argument("--threads", type=positive_int, default=2)
    common.add_argument("--memory", default="2GB", help="DuckDB buffer manager limit")
    common.add_argument("--spill-dir", type=Path, help="Existing directory on a local SSD")
    commands = parser.add_subparsers(dest="command", required=True)

    profile = commands.add_parser("inspect", parents=[common], help="Rows, schema, nulls and key audit")
    profile.add_argument("--key")
    profile.add_argument("--time", help="Timestamp column, interpreted in UTC when no zone is present")
    profile.add_argument("--limit", type=positive_int, help="Inspect only the first N rows")
    profile.set_defaults(run=inspect)

    convert = commands.add_parser("parquet", parents=[common], help="Stage a typed Parquet file")
    convert.add_argument("output", type=Path)
    convert.add_argument("--type", action="append", dest="types", default=[], metavar="COLUMN=TYPE")
    convert.set_defaults(run=stage)

    partition = commands.add_parser("shard", parents=[common], help="Write Hive-style Parquet hash buckets")
    partition.add_argument("output", type=Path)
    partition.add_argument("--key", required=True)
    partition.add_argument("--buckets", type=positive_int, default=16)
    partition.set_defaults(run=shard)
    return parser


def main():
    parser = make_parser()
    args = parser.parse_args()
    try:
        with tempfile.TemporaryDirectory(prefix="datakit-spill-", dir=args.spill_dir) as scratch:
            config = {
                "threads": args.threads,
                "memory_limit": args.memory,
                "temp_directory": scratch,
                "TimeZone": "UTC",
            }
            with duckdb.connect(config=config) as db:
                relation = source_relation(db, args.source, args.delimiter)
                args.run(db, relation, args)
    except (ValueError, OSError, duckdb.Error) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
