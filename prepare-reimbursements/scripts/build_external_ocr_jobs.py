#!/usr/bin/env python3
"""Build typed, canonical OCR jobs and a separate private comparison sidecar."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from prepare_reimbursements.ocr_jobs import build_jobs, write_jobs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder", type=Path, required=True)
    parser.add_argument("--db", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--overrides", type=Path)
    args = parser.parse_args()
    payload = build_jobs(args.folder, db_path=args.db, manifest_path=args.manifest, overrides_path=args.overrides)
    paths = write_jobs(payload, args.folder)
    print(json.dumps({"jobs": len(payload["job_manifest"]["jobs"]), "unsupported": len(payload["unsupported"]),
                      "excluded": len(payload["excluded"]), "diagnostics": payload["diagnostics"], "outputs": paths},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
