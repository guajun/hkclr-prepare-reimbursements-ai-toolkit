#!/usr/bin/env python3
"""Run advisory external OCR on canonical local evidence, without changing claims."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from prepare_reimbursements.ocr_runner import run_external_ocr


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder", required=True, type=Path)
    parser.add_argument("--manifest", type=Path, help="Optional explicit typed OCR jobs; otherwise build from state.")
    parser.add_argument("--db", type=Path)
    parser.add_argument("--overrides", type=Path)
    parser.add_argument("--output", type=Path, help="Private output root under the batch generated/ocr directory.")
    parser.add_argument("--doctor-timeout", type=float, default=120)
    parser.add_argument("--run-timeout", type=float, default=600)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = run_external_ocr(args.folder, jobs_manifest=args.manifest, output_dir=args.output,
        dry_run=args.dry_run, doctor_timeout=args.doctor_timeout, run_timeout=args.run_timeout,
        db_path=args.db, overrides_path=args.overrides)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps({key: result[key] for key in ("status", "available", "reason", "run_id", "counts", "summary_path") if key in result}, ensure_ascii=False, indent=2))
    # OCR unavailability must not block the existing reimbursement workflow.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
