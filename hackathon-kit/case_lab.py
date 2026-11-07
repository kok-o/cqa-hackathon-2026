"""Run and verify the self-contained SQL recipes on synthetic examples."""

from pathlib import Path

import duckdb


def main():
    path = Path(__file__).with_suffix(".sql")
    expected = {
        "ordered_funnel": [(3, 3, 2, 1)],
        "rfm": [("0001", "KZT", 9, 1, 22000), ("0001", "USD", 7, 1, 4500), ("0002", "KZT", 4, 1, 5000)],
        "join_cardinality": [(6, 11, 0, 1)],
    }
    with duckdb.connect() as db:
        for statement in db.extract_statements(path.read_text(encoding="utf-8")):
            result = db.execute(statement.query)
            if statement.type != duckdb.StatementType.SELECT:
                continue
            rows = result.fetchall()
            name = rows[0][0]
            observed = [row[1:] for row in rows]
            if observed != expected.pop(name):
                raise RuntimeError(f"{name}: unexpected result {observed!r}")
            print(" | ".join(column[0] for column in result.description[1:]))
            for row in observed:
                print(" | ".join(str(cell) for cell in row))
            print()
    if expected:
        raise RuntimeError(f"Missing checks: {', '.join(expected)}")
    print("OK: ordered funnel, RFM and join-cardinality checks")


if __name__ == "__main__":
    main()
