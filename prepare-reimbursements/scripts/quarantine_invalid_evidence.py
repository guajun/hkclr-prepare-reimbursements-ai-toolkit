#!/usr/bin/env python3
"""Move invalid evidence screenshots out of active evidence folders."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from prepare_taobao_evidence import build_records

REPORT_SCHEMA = "prepare-reimbursements.evidence-quarantine.v1"


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def load_manifest(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def unique_destination(destination: Path) -> Path:
    if not destination.exists():
        return destination
    stem = destination.stem
    suffix = destination.suffix
    for index in range(2, 1000):
        candidate = destination.with_name(f"{stem}.{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"Could not find a free quarantine destination for {destination}")


def collect_targets(records: list[dict[str, Any]], quarantine_root: Path) -> list[dict[str, Any]]:
    targets: list[dict[str, Any]] = []
    for record in records:
        order = record["order"]
        checks = [
            ("taobao_order_detail_screenshot", record["order_hits"], record["order_image_warnings"]),
            ("payment_record_screenshot", record["payment_hits"], record["payment_image_warnings"]),
        ]
        for evidence_kind, hits, warnings in checks:
            if not warnings:
                continue
            for filename in hits:
                source = Path(str(record["folder"])) / filename
                if not source.exists() or not source.is_file():
                    continue
                destination = unique_destination(
                    quarantine_root
                    / f"{record['index']:02d}_{order['order_no']}"
                    / evidence_kind
                    / source.name
                )
                targets.append(
                    {
                        "index": record["index"],
                        "order_no": order["order_no"],
                        "evidence_kind": evidence_kind,
                        "source": str(source),
                        "destination": str(destination),
                        "warnings": warnings,
                    }
                )
    return targets


def apply_quarantine(targets: list[dict[str, Any]]) -> None:
    for target in targets:
        source = Path(str(target["source"]))
        destination = Path(str(target["destination"]))
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder", type=Path, required=True, help="Reimbursement batch folder")
    parser.add_argument("--manifest", type=Path, help="Manifest path. Defaults to <folder>/generated/reimbursement-manifest.json")
    parser.add_argument("--evidence-root", type=Path, help="Evidence root. Defaults to <folder>/物品/taobao")
    parser.add_argument("--quarantine-root", type=Path, help="Quarantine root. Defaults to <folder>/generated/quarantine/evidence/<timestamp>")
    parser.add_argument("--apply", action="store_true", help="Move invalid files. Without this flag, only write a dry-run report")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    batch_folder = args.folder.resolve()
    generated = batch_folder / "generated"
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    manifest_path = (args.manifest or generated / "reimbursement-manifest.json").resolve()
    evidence_root = (args.evidence_root or batch_folder / "物品" / "taobao").resolve()
    quarantine_root = (args.quarantine_root or generated / "quarantine" / "evidence" / timestamp).resolve()

    manifest = load_manifest(manifest_path)
    records = build_records(manifest["orders"], evidence_root, write_notes=False)
    targets = collect_targets(records, quarantine_root)
    if args.apply:
        apply_quarantine(targets)

    report = {
        "schema": REPORT_SCHEMA,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "batch": str(batch_folder),
        "manifest": str(manifest_path),
        "evidence_root": str(evidence_root),
        "quarantine_root": str(quarantine_root),
        "mode": "apply" if args.apply else "dry-run",
        "targets": targets,
        "target_count": len(targets),
        "next_step": "Run scripts\\prepare_taobao_evidence.py, then scripts\\sync_reimbursement_state.py.",
    }
    generated.mkdir(parents=True, exist_ok=True)
    report_path = generated / "evidence-quarantine-report.json"
    report["report"] = str(report_path)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
