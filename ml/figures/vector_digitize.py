"""Thin shim: the digitizer lives in the service module ml/pdf-dataset/figures.py ([faktura-patch])."""
import sys
from pathlib import Path

_SVC = str(Path(__file__).resolve().parents[1] / "pdf-dataset")
if _SVC not in sys.path:
    sys.path.insert(0, _SVC)
from figures import *  # noqa: E402,F401,F403
from figures import digitize_pdf, digitize_page, fit_axis, parse_num  # noqa: E402,F401

if __name__ == "__main__":
    for f in digitize_pdf(sys.argv[1]):
        print(f["page"], f["figure_label"], [(s["kind"], len(s["points"])) for p in f["panels"] for s in p["series"]])
