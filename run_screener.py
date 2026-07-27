"""Headless CLI entry point - runs the pipeline and prints the report.

Useful for testing the engine without the GUI. The GUI (gui/app.py) is the intended
day-to-day interface per spec section 10.

    python run_screener.py                 # manual CSV mode, no Kite
    python run_screener.py --mode scripted # POST to Chartink
    python run_screener.py --kite          # include live quote check
    python run_screener.py --off P5 P7     # toggle parameters off
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from nse_screener.models import PARAM_IDS  # noqa: E402
from nse_screener.pipeline import run_pipeline  # noqa: E402
from nse_screener.report import format_report  # noqa: E402
from nse_screener.scoring import score_all  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Two-stage NSE equity screener")
    parser.add_argument("--mode", choices=["manual", "scripted"], default=None)
    parser.add_argument("--kite", action="store_true", help="enable the live quote check")
    parser.add_argument("--off", nargs="*", default=[], metavar="PARAM",
                        help=f"parameters to switch off, from {list(PARAM_IDS)}")
    parser.add_argument("--quiet", action="store_true", help="suppress progress output")
    args = parser.parse_args()

    invalid = [p for p in args.off if p.upper() not in PARAM_IDS]
    if invalid:
        parser.error(f"unknown parameter(s): {invalid}. Valid: {list(PARAM_IDS)}")

    toggles = {pid: pid not in {p.upper() for p in args.off} for pid in PARAM_IDS}

    def progress(message: str, pct: int) -> None:
        if not args.quiet:
            print(f"[{pct:>3}%] {message}", file=sys.stderr)

    result = run_pipeline(mode=args.mode, progress=progress, use_kite=args.kite)
    ranked = score_all(result.stocks, toggles)
    print(format_report(ranked, result.context, toggles))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
