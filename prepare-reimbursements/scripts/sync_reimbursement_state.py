#!/usr/bin/env python3
"""Sync a reimbursement batch manifest and evidence files into SQLite state."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from prepare_reimbursements import state_db
from prepare_taobao_evidence import build_records

DEFAULT_DB_NAME = "reimbursement-state.sqlite3"
DEFAULT_SNAPSHOT_NAME = "reimbursement-state.snapshot.json"


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def relative_to_folder(path: Path | None, folder: Path) -> str | None:
    if not path:
        return None
    try:
        return os.path.relpath(path, folder)
    except ValueError:
        return str(path)


def image_dimensions(path: Path) -> tuple[int, int] | tuple[None, None]:
    try:
        with Image.open(path) as image:
            return image.size
    except (OSError, UnidentifiedImageError):
        return None, None


def file_metadata(path: Path | None, batch_folder: Path) -> dict[str, Any]:
    if not path or not path.exists() or not path.is_file():
        return {
            "actual_path": str(path) if path else None,
            "relative_path": relative_to_folder(path, batch_folder) if path else None,
            "file_name": path.name if path else None,
            "file_size": None,
            "sha256": None,
            "width": None,
            "height": None,
        }
    width, height = image_dimensions(path)
    return {
        "actual_path": str(path),
        "relative_path": relative_to_folder(path, batch_folder),
        "file_name": path.name,
        "file_size": path.stat().st_size,
        "sha256": state_db.sha256_file(path),
        "width": width,
        "height": height,
    }


def first_existing(folder: Path, names: list[str]) -> Path | None:
    for name in names:
        path = folder / name
        if path.exists():
            return path
    return None


def evidence_record(
    *,
    batch_folder: Path,
    evidence_kind: str,
    expected_filename: str,
    folder: Path,
    hits: list[str],
    warnings: list[str],
    capture_method: str,
) -> dict[str, Any]:
    actual = first_existing(folder, [expected_filename, *hits])
    metadata = file_metadata(actual, batch_folder)
    return {
        "evidence_kind": evidence_kind,
        "expected_filename": expected_filename,
        **metadata,
        "raw_path": None,
        "source_path": relative_to_folder(folder, batch_folder),
        "capture_method": capture_method,
        "validation_status": "valid" if actual and not warnings else "missing" if not actual else "warning",
        "warnings": warnings,
        "details": {
            "folder": str(folder),
            "hits": hits,
        },
    }


def payment_raw_path(record: dict[str, Any]) -> Path | None:
    folder = Path(str(record["folder"]))
    raw_path = folder / "_raw_payment_screenshots" / record["payment_file"]
    return raw_path if raw_path.exists() else None


def build_artifacts(batch_folder: Path, generated: Path, summary: dict[str, Any] | None) -> list[dict[str, Any]]:
    candidates = {
        "taobao_manifest": generated / "reimbursement-manifest.json",
        "reimbursement_review_workbook": generated / "reimbursement-review.xlsx",
        "taobao_evidence_checklist": generated / "taobao-evidence-checklist.xlsx",
        "taobao_capture_queue": generated / "taobao-evidence-capture-queue.md",
        "taobao_evidence_summary": generated / "taobao-evidence-summary.json",
        "alipay_normalize_report": generated / "alipay-payment-screenshot-normalize-report.json",
        "alipay_contact_sheet": generated / "alipay-payment-screenshot-contact-sheet.png",
    }
    artifacts: list[dict[str, Any]] = []
    for kind, path in candidates.items():
        if not path.exists() or not path.is_file():
            continue
        artifacts.append(
            {
                "artifact_kind": kind,
                "path": str(path),
                "relative_path": relative_to_folder(path, batch_folder),
                "file_size": path.stat().st_size,
                "sha256": state_db.sha256_file(path),
            }
        )

    for workbook in sorted(generated.glob("報銷清單_Reimbursement list*.xlsx")):
        artifacts.append(
            {
                "artifact_kind": "reimbursement_workbook",
                "path": str(workbook),
                "relative_path": relative_to_folder(workbook, batch_folder),
                "file_size": workbook.stat().st_size,
                "sha256": state_db.sha256_file(workbook),
            }
        )

    print_flat_folder = (summary or {}).get("print_flat_folder")
    if print_flat_folder:
        print_flat_warnings = (summary or {}).get("print_flat_warnings", [])
        artifacts.append(
            {
                "artifact_kind": "taobao_print_flat_folder",
                "path": str(print_flat_folder),
                "relative_path": relative_to_folder(Path(print_flat_folder), batch_folder),
                "details": {
                    "links": (summary or {}).get("print_flat_links", 0),
                    "warning_count": len(print_flat_warnings),
                    "warnings_sample": print_flat_warnings[:5],
                },
            }
        )
    return artifacts


def sync_batch(
    *,
    batch_folder: Path,
    db_path: Path,
    manifest_path: Path,
    summary_path: Path | None,
    evidence_root: Path,
    snapshot_path: Path | None,
) -> dict[str, Any]:
    manifest = load_json(manifest_path)
    summary = load_json(summary_path) if summary_path and summary_path.exists() else {}
    generated = batch_folder / "generated"

    with state_db.connect(db_path) as connection:
        batch_id = state_db.upsert_batch(
            connection,
            batch_folder=batch_folder,
            reimbursement_type="normal",
            source_manifest_path=manifest_path,
            source_export_path=Path(manifest["source_file"]) if manifest.get("source_file") else None,
            profile=manifest.get("profile") or {},
            summary=manifest.get("summary") or {},
        )

        records = build_records(manifest["orders"], evidence_root, write_notes=False)
        order_ids: dict[str, int] = {}
        validation_rows: list[dict[str, Any]] = []
        item_count = 0
        evidence_count = 0

        for record in records:
            order = record["order"]
            order_id = state_db.upsert_order(
                connection,
                batch_id=batch_id,
                index=int(record["index"]),
                order=order,
            )
            order_ids[str(order["order_no"])] = order_id
            item_count += state_db.replace_items(connection, order_id=order_id, items=order.get("items") or [])

            order_evidence = evidence_record(
                batch_folder=batch_folder,
                evidence_kind="taobao_order_detail_screenshot",
                expected_filename=record["order_file"],
                folder=Path(str(record["folder"])),
                hits=list(record["order_hits"]),
                warnings=list(record["order_image_warnings"]),
                capture_method="taobao_detail_browser_screenshot",
            )
            state_db.upsert_evidence(connection, order_id=order_id, evidence=order_evidence)
            evidence_count += 1

            payment_evidence = evidence_record(
                batch_folder=batch_folder,
                evidence_kind="payment_record_screenshot",
                expected_filename=record["payment_file"],
                folder=Path(str(record["folder"])),
                hits=list(record["payment_hits"]),
                warnings=list(record["payment_image_warnings"]),
                capture_method="alipay_detail_browser_screenshot",
            )
            raw_path = payment_raw_path(record)
            if raw_path:
                payment_evidence["raw_path"] = relative_to_folder(raw_path, batch_folder)
            state_db.upsert_evidence(connection, order_id=order_id, evidence=payment_evidence)
            evidence_count += 1

            warnings = list(record["order_image_warnings"]) + list(record["payment_image_warnings"])
            validation_rows.append(
                {
                    "order_id": order_id,
                    "scope": "taobao_order_evidence",
                    "status": "valid" if not warnings and record["order_hits"] and record["payment_hits"] else "warning",
                    "warnings": warnings,
                    "details": {
                        "order_hits": record["order_hits"],
                        "payment_hits": record["payment_hits"],
                        "combined_hits": record["combined_hits"],
                    },
                }
            )

        state_db.replace_validation_results(
            connection,
            batch_id=batch_id,
            tool="scripts/prepare_taobao_evidence.py",
            results=validation_rows,
        )
        artifact_count = state_db.replace_artifacts(
            connection,
            batch_id=batch_id,
            artifacts=build_artifacts(batch_folder, generated, summary),
        )

        snapshot_written = None
        if snapshot_path:
            payload = state_db.snapshot(connection, batch_id=batch_id, db_path=db_path)
            snapshot_path.parent.mkdir(parents=True, exist_ok=True)
            snapshot_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            snapshot_written = str(snapshot_path)

        connection.commit()

    return {
        "database": str(db_path),
        "snapshot": snapshot_written,
        "batch": str(batch_folder),
        "orders": len(manifest["orders"]),
        "items": item_count,
        "evidence_records": evidence_count,
        "artifacts": artifact_count,
        "order_screenshot_warnings": summary.get("order_screenshot_warnings", []),
        "payment_screenshot_warnings": summary.get("payment_screenshot_warnings", []),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder", type=Path, required=True, help="Reimbursement batch folder")
    parser.add_argument("--db", type=Path, help="SQLite state database. Defaults to <folder>/generated/reimbursement-state.sqlite3")
    parser.add_argument("--manifest", type=Path, help="Manifest path. Defaults to <folder>/generated/reimbursement-manifest.json")
    parser.add_argument("--summary", type=Path, help="Evidence summary path. Defaults to <folder>/generated/taobao-evidence-summary.json")
    parser.add_argument("--evidence-root", type=Path, help="Evidence root. Defaults to <folder>/物品/taobao")
    parser.add_argument(
        "--snapshot",
        type=Path,
        help="Review-friendly JSON snapshot. Defaults to <folder>/generated/reimbursement-state.snapshot.json",
    )
    parser.add_argument("--no-snapshot", action="store_true", help="Do not write the JSON snapshot")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    batch_folder = args.folder.resolve()
    generated = batch_folder / "generated"
    db_path = (args.db or generated / DEFAULT_DB_NAME).resolve()
    manifest_path = (args.manifest or generated / "reimbursement-manifest.json").resolve()
    summary_path = (args.summary or generated / "taobao-evidence-summary.json").resolve()
    evidence_root = (args.evidence_root or batch_folder / "物品" / "taobao").resolve()
    snapshot_path = None if args.no_snapshot else (args.snapshot or generated / DEFAULT_SNAPSHOT_NAME).resolve()

    result = sync_batch(
        batch_folder=batch_folder,
        db_path=db_path,
        manifest_path=manifest_path,
        summary_path=summary_path,
        evidence_root=evidence_root,
        snapshot_path=snapshot_path,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
