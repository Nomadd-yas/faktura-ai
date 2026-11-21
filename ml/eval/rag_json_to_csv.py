#!/usr/bin/env python3
"""dataset.json сервиса извлечения (domain=rag) -> 7 CSV области RAG (колонки строго по schema.json).

    python ml/eval/rag_json_to_csv.py OUT_DIR D020.dataset.json [D004.dataset.json ...]
Пустое -> "", true/false, списки -> "a; b", числа -> как есть. Для оценки: ml/eval/score_rag.py.
"""
import csv
import json
import sys
from pathlib import Path

SCHEMA = Path(__file__).resolve().parents[2] / "data" / "caseA_drive" / "submission_template" / "schema.json"


def cell(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, list):
        return "; ".join(cell(x) for x in v if x is not None)
    if isinstance(v, float) and v == int(v) and abs(v) < 1e15:
        return repr(v)
    return str(v)


def main():
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    tables = json.loads(SCHEMA.read_text(encoding="utf-8"))["tables"]
    rows = {t: [] for t in tables}
    for p in sys.argv[2:]:
        d = json.loads(Path(p).read_text(encoding="utf-8"))
        d = d.get("dataset", d)
        for t in tables:
            rows[t] += d.get(t) or []
    for t, cols in tables.items():
        with open(out / f"{t}.csv", "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(cols)
            for r in rows[t]:
                w.writerow([cell(r.get(c)) for c in cols])
    print({t: len(v) for t, v in rows.items()})


if __name__ == "__main__":
    main()
