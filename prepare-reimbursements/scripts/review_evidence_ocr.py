#!/usr/bin/env python3
"""Read OCR candidates first and list focused visual-review reasons."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from prepare_reimbursements.ocr_review import read_evidence_excerpt, review_outputs
from prepare_reimbursements.ocr_runner import run_external_ocr


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder", type=Path, required=True)
    parser.add_argument("--overrides", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-persist", action="store_true")
    parser.add_argument("--visual-budget", type=int, default=3)
    parser.add_argument("--run-timeout", type=float, default=600)
    parser.add_argument("--evidence-id", help="Read one private OCR result's compact text without inference")
    args = parser.parse_args()
    folder = args.folder.resolve()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if args.evidence_id:
        print(json.dumps(read_evidence_excerpt(folder, args.evidence_id), ensure_ascii=False, indent=2))
        return 0
    run = run_external_ocr(folder, overrides_path=args.overrides, dry_run=args.dry_run, run_timeout=args.run_timeout)
    print(json.dumps(review_outputs(folder, run, persist=not args.no_persist,
                                    visual_budget=args.visual_budget), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
