#!/usr/bin/env python3
"""Run the authoritative analysis and render a circulation PDF.

The empirical work lives in ``build_full_data_blp_analysis.py``. This wrapper
deliberately contains no second copy of the estimators: it runs that pipeline,
then renders either its generated report or a caller-supplied manuscript.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ANALYSIS = ROOT / "rapidapi_io_static" / "scripts" / "build_full_data_blp_analysis.py"
GENERATED_REPORT = (
    ROOT / "rapidapi_io_static" / "full_results" / "report" / "data_access_contracts_submission_zh.md"
)
MANUSCRIPT = ROOT / "rapidapi_io_static" / "scripts" / "build_submission_manuscript_zh.py"
DEFAULT_PDF = (
    ROOT / "rapidapi_io_static" / "full_results" / "report" / "data_access_contracts_io.pdf"
)


def run(command: list[str]) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=ROOT, check=True)


def run_analysis(python: str) -> None:
    run([python, str(ANALYSIS)])
    run([python, str(MANUSCRIPT)])


def render_pdf(source: Path, output: Path) -> None:
    pandoc = shutil.which("pandoc")
    xelatex = shutil.which("xelatex")
    if not pandoc or not xelatex:
        missing = [name for name, path in [("pandoc", pandoc), ("xelatex", xelatex)] if not path]
        raise SystemExit(f"Missing PDF dependencies: {', '.join(missing)}")

    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        pandoc,
        str(source),
        "--from=markdown+tex_math_dollars+raw_tex",
        "--pdf-engine=xelatex",
        "--resource-path",
        str(source.parent),
        "--number-sections",
        "-V",
        "documentclass=article",
        "-V",
        "classoption=12pt",
        "-V",
        "geometry:margin=1in",
        "-V",
        "linestretch=1.35",
        "-V",
        "mainfont=Times New Roman",
        "-V",
        "CJKmainfont=Songti SC",
        "-V",
        "colorlinks=true",
        "-V",
        "linkcolor=black",
        "-V",
        "urlcolor=blue",
        "-o",
        str(output),
    ]
    run(command)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-analysis", action="store_true")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--source", type=Path, default=GENERATED_REPORT)
    parser.add_argument("--output", type=Path, default=DEFAULT_PDF)
    parser.add_argument("--no-pdf", action="store_true")
    args = parser.parse_args()

    if not args.skip_analysis:
        run_analysis(args.python)
    source = args.source.resolve()
    if not source.exists():
        raise SystemExit(f"Manuscript not found: {source}")
    if not args.no_pdf:
        render_pdf(source, args.output.resolve())
        print(f"PDF: {args.output.resolve()}")
    print(f"Report: {source}")


if __name__ == "__main__":
    main()
