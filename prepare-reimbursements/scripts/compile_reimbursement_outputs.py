#!/usr/bin/env python3
"""Compile reimbursement outputs from SQLite state and source evidence files."""

from __future__ import annotations

import argparse
import copy
import json
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from build_taobao_normal_reimbursement import (
    OrderItem,
    ReimbursementOrder,
    claim_totals_by_currency,
    currency_confirmation_reason,
    find_template,
    reimbursement_date_for_orders,
    validate_currency_reviews,
    write_reimbursement_workbook,
    write_review_workbook,
)
from prepare_reimbursements import state_db
from prepare_taobao_evidence import (
    PRINT_FLAT_ALL_ROOT,
    expected_evidence_files,
    write_capture_queue,
    write_checklist,
    write_print_flat_folder,
)
from sync_reimbursement_state import build_artifacts, relative_to_folder

COMPILE_SCHEMA = "prepare-reimbursements.compile-from-state.v1"
DEFAULT_DB_NAME = "reimbursement-state.sqlite3"


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def load_json_field(value: str | None, default: Any) -> Any:
    return state_db.json_loads(value, default)


def row_to_item(row: Any) -> OrderItem:
    return OrderItem(
        name=row["name"] or "",
        link=row["link"] or "",
        style=row["style"] or "",
        quantity=row["quantity"] or "",
        item_amount_rmb=row["item_amount_rmb"],
    )


def row_to_order(row: Any, items: list[OrderItem]) -> ReimbursementOrder:
    return ReimbursementOrder(
        source=row["source"],
        order_no=row["order_no"],
        taobao_order_detail_url=row["taobao_order_detail_url"] or "",
        alipay_trade_no=row["alipay_trade_no"] or "",
        alipay_detail_url=row["alipay_detail_url"] or "",
        date=row["order_date"] or "",
        datetime=row["order_datetime"] or "",
        shop=row["shop"] or "",
        status=row["status"] or "",
        item_label=row["item_label"] or "",
        amount_rmb=float(row["amount_rmb"] or 0),
        shipping_rmb=row["shipping_rmb"],
        item_count=int(row["item_count"] or len(items)),
        items=items,
        document_type=row["document_type"] or "",
        missing_receipt_reason=row["missing_receipt_reason"] or "",
        evidence_required=load_json_field(row["evidence_required_json"], []),
        claim_amount=float(row["claim_amount"]) if row["claim_amount"] is not None else None,
        claim_currency=row["claim_currency"] or "RMB",
        payment_amount=float(row["payment_amount"]) if row["payment_amount"] is not None else None,
        payment_currency=row["payment_currency"] or "",
        currency_review_status=row["currency_review_status"] or "resolved",
        currency_note=row["currency_note"] or "",
    )


def load_batch(connection: Any, batch_folder: Path) -> Any:
    row = connection.execute(
        "SELECT * FROM batches WHERE batch_folder = ?",
        (str(batch_folder),),
    ).fetchone()
    if row is None:
        raise FileNotFoundError(f"No SQLite batch state found for {batch_folder}")
    return row


def load_orders(connection: Any, batch_id: int) -> list[tuple[int, int, ReimbursementOrder]]:
    rows = connection.execute(
        """
        SELECT * FROM orders
        WHERE batch_id = ?
        ORDER BY
            CASE source
                WHEN 'taobao' THEN 1
                WHEN 'hqchip' THEN 2
                WHEN 'meituan' THEN 3
                WHEN 'jingdong' THEN 4
                WHEN 'github' THEN 5
                WHEN 'aliyun' THEN 6
                ELSE 99
            END,
            CASE WHEN order_date IS NULL OR order_date = '' THEN 1 ELSE 0 END,
            order_date,
            source_order_index,
            id
        """,
        (batch_id,),
    ).fetchall()
    orders: list[tuple[int, int, ReimbursementOrder]] = []
    for row in rows:
        item_rows = connection.execute(
            "SELECT * FROM order_items WHERE order_id = ? ORDER BY item_index",
            (row["id"],),
        ).fetchall()
        items = [row_to_item(item_row) for item_row in item_rows]
        orders.append((int(row["id"]), int(row["source_order_index"]), row_to_order(row, items)))
    return orders


def load_evidence(connection: Any, order_id: int) -> dict[str, Any]:
    rows = connection.execute(
        "SELECT * FROM evidence_files WHERE order_id = ?",
        (order_id,),
    ).fetchall()
    return {row["evidence_kind"]: row for row in rows}


def resolved_path(batch_folder: Path, path: str | None, relative_path: str | None) -> Path | None:
    if relative_path:
        return batch_folder / relative_path
    if path:
        return Path(path)
    return None


def folder_from_evidence(batch_folder: Path, order_index: int, order_no: str, evidence: dict[str, Any]) -> Path:
    for row in evidence.values():
        source_path = row["source_path"]
        if source_path:
            path = Path(source_path)
            return path if path.is_absolute() else batch_folder / path
        actual = resolved_path(batch_folder, row["actual_path"], row["relative_path"])
        if actual:
            return actual.parent
    return batch_folder / "物品" / "taobao" / f"{order_index:02d}_{order_no}"


def evidence_hits(batch_folder: Path, row: Any | None) -> list[str]:
    if row is None:
        return []
    actual = resolved_path(batch_folder, row["actual_path"], row["relative_path"])
    if actual and actual.exists():
        return [actual.name]
    return []


def evidence_warnings(row: Any | None) -> list[str]:
    if row is None:
        return []
    return load_json_field(row["warnings_json"], [])


def build_records_from_state(
    connection: Any,
    *,
    batch_folder: Path,
    orders: list[tuple[int, int, ReimbursementOrder]],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for order_id, order_index, order in orders:
        evidence = load_evidence(connection, order_id)
        order_evidence = (
            evidence.get(f"{order.source}_order_detail_screenshot")
            or evidence.get("order_detail_screenshot")
            or evidence.get("taobao_order_detail_screenshot")
        )
        payment_evidence = evidence.get("payment_record_screenshot")
        direct_evidence = evidence.get("invoice_pdf") or evidence.get("receipt_pdf")
        required_direct_kind = next(
            (kind for kind in order.evidence_required if kind in {"invoice_pdf", "receipt_pdf"}),
            "",
        )
        order_file, payment_file, combined_file = expected_evidence_files(order_index, order.order_no)
        folder = folder_from_evidence(batch_folder, order_index, order.order_no, evidence)
        direct_hits = evidence_hits(batch_folder, direct_evidence)
        records.append(
            {
                "index": order_index,
                "folder": folder,
                "order": asdict(order),
                "order_file": order_evidence["expected_filename"] if order_evidence and order_evidence["expected_filename"] else order_file,
                "payment_file": payment_evidence["expected_filename"] if payment_evidence and payment_evidence["expected_filename"] else payment_file,
                "combined_file": combined_file,
                "order_hits": evidence_hits(batch_folder, order_evidence),
                "payment_hits": evidence_hits(batch_folder, payment_evidence),
                "order_image_warnings": evidence_warnings(order_evidence),
                "payment_image_warnings": evidence_warnings(payment_evidence),
                "combined_hits": [],
                "direct_document_kind": (
                    direct_evidence["evidence_kind"] if direct_evidence else required_direct_kind
                ),
                "direct_file": (
                    direct_evidence["expected_filename"] or (direct_hits[0] if direct_hits else "")
                    if direct_evidence
                    else ""
                ),
                "direct_hits": direct_hits,
                "direct_document_warnings": evidence_warnings(direct_evidence),
            }
        )
    return records


def build_profile(batch: Any, args: argparse.Namespace) -> dict[str, str]:
    profile = copy.deepcopy(load_json_field(batch["profile_json"], {}))
    defaults = {
        "name": "Li Gaoyang",
        "bank": "HSBC",
        "account": "592-251326-833",
        "leader": "Chen Wei",
    }
    for key, value in defaults.items():
        profile.setdefault(key, value)
    for key in ("name", "bank", "account", "leader"):
        override = getattr(args, key)
        if override:
            profile[key] = override
    return {key: str(profile[key]) for key in ("name", "bank", "account", "leader")}


def build_manifest(batch: Any, profile: dict[str, str], orders: list[ReimbursementOrder]) -> dict[str, Any]:
    summary = copy.deepcopy(load_json_field(batch["summary_json"], {}))
    summary["included_orders"] = len(orders)
    summary["total_amount_rmb"] = round(sum(order.amount_rmb for order in orders), 2)
    summary["claim_totals"] = claim_totals_by_currency(orders)
    return {
        "schema": "prepare-reimbursements.taobao-normal.v1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_file": batch["source_export_path"],
        "profile": profile,
        "summary": summary,
        "orders": [asdict(order) for order in orders],
    }


def write_currency_confirmation_queue(path: Path, orders: list[ReimbursementOrder]) -> int:
    pending = [(order, currency_confirmation_reason(order)) for order in orders]
    pending = [(order, reason) for order, reason in pending if reason]
    if not pending:
        path.unlink(missing_ok=True)
        return 0
    payload = {
        "schema": "prepare-reimbursements.currency-confirmation-queue.v1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "orders": [
            {
                "source": order.source,
                "order_no": order.order_no,
                "date": order.date,
                "item_label": order.item_label,
                "purchase_amount": order.amount_rmb,
                "purchase_currency": "RMB",
                "payment_amount": order.payment_amount,
                "payment_currency": order.payment_currency,
                "proposed_claim_amount": order.amount_rmb if order.claim_amount is None else order.claim_amount,
                "proposed_claim_currency": order.claim_currency,
                "reason": reason,
            }
            for order, reason in pending
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(pending)


def write_evidence_summary(
    *,
    path: Path,
    batch_folder: Path,
    evidence_root: Path,
    checklist_path: Path,
    queue_path: Path,
    records: list[dict[str, Any]],
    print_flat: dict[str, Any],
) -> dict[str, Any]:
    summary = {
        "schema": "prepare-reimbursements.taobao-evidence-summary.v1",
        "compiled_from": "sqlite",
        "evidence_root": str(evidence_root),
        "orders": len(records),
        "order_screenshots_present": sum(1 for record in records if record["order_hits"]),
        "order_screenshots_found": sum(1 for record in records if record["order_hits"] and not record["order_image_warnings"]),
        "order_screenshot_warnings": [
            {"folder": str(record["folder"]), "warnings": record["order_image_warnings"]}
            for record in records
            if record["order_image_warnings"]
        ],
        "payment_screenshots_present": sum(1 for record in records if record["payment_hits"]),
        "payment_screenshots_found": sum(1 for record in records if record["payment_hits"] and not record["payment_image_warnings"]),
        "payment_screenshot_warnings": [
            {"folder": str(record["folder"]), "warnings": record["payment_image_warnings"]}
            for record in records
            if record["payment_image_warnings"]
        ],
        "direct_documents_present": sum(1 for record in records if record.get("direct_hits")),
        "direct_documents_found": sum(
            1
            for record in records
            if record.get("direct_hits") and not record.get("direct_document_warnings")
        ),
        "direct_document_warnings": [
            {"folder": str(record["folder"]), "warnings": record.get("direct_document_warnings") or []}
            for record in records
            if record.get("direct_document_warnings")
        ],
        "evidence_complete": sum(
            1
            for record in records
            if (
                record.get("direct_hits")
                and not record.get("direct_document_warnings")
            )
            or (
                record["order_hits"]
                and not record["order_image_warnings"]
                and record["payment_hits"]
                and not record["payment_image_warnings"]
            )
        ),
        "combined_receipts_found": sum(1 for record in records if record["combined_hits"]),
        "checklist": str(checklist_path),
        "queue": str(queue_path),
        "print_flat_folder": print_flat["folder"],
        "print_flat_scope": "all",
        "print_flat_links": len(print_flat["links"]),
        "print_flat_warnings": print_flat["warnings"],
    }
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def parse_skipped(batch: Any) -> dict[str, int]:
    summary = load_json_field(batch["summary_json"], {})
    skipped = summary.get("skipped") or {}
    return {str(key): int(value) for key, value in skipped.items() if isinstance(value, int)}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder", type=Path, required=True, help="Reimbursement batch folder")
    parser.add_argument("--db", type=Path, help="SQLite state database. Defaults to <folder>/generated/reimbursement-state.sqlite3")
    parser.add_argument("--out-dir", type=Path, help="Output directory. Defaults to <folder>/generated")
    parser.add_argument(
        "--workbook-output",
        type=Path,
        help="Final normal reimbursement workbook path. Defaults to the batch folder",
    )
    parser.add_argument("--evidence-root", type=Path, help="Evidence root. Defaults to <folder>/物品/taobao")
    parser.add_argument("--template", type=Path, help="Previous normal reimbursement workbook to use as template")
    parser.add_argument(
        "--submission-date",
        help="Optional YYYY-MM-DD assertion; must match the latest reimbursed item date",
    )
    parser.add_argument("--name")
    parser.add_argument("--bank")
    parser.add_argument("--account")
    parser.add_argument("--leader")
    parser.add_argument("--no-print-flat", action="store_true", help="Do not refresh the print-flat folder")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    batch_folder = args.folder.resolve()
    generated = (args.out_dir or batch_folder / "generated").resolve()
    generated.mkdir(parents=True, exist_ok=True)
    db_path = (args.db or batch_folder / "generated" / DEFAULT_DB_NAME).resolve()
    evidence_root = (args.evidence_root or batch_folder / "物品" / "taobao").resolve()

    with state_db.connect(db_path) as connection:
        batch = load_batch(connection, batch_folder)
        batch_id = int(batch["id"])
        order_rows = load_orders(connection, batch_id)
        orders = [order for _, _, order in order_rows]
        reimbursement_date = reimbursement_date_for_orders(orders, args.submission_date)
        currency_queue_path = generated / "currency-confirmation-queue.json"
        pending_currency_count = write_currency_confirmation_queue(currency_queue_path, orders)
        if pending_currency_count:
            try:
                validate_currency_reviews(orders)
            except ValueError as error:
                raise ValueError(f"{error}\nConfirmation queue: {currency_queue_path}") from error
        profile = build_profile(batch, args)

        manifest_path = generated / "reimbursement-manifest.json"
        review_path = generated / "reimbursement-review.xlsx"
        workbook_path = (
            args.workbook_output.resolve()
            if args.workbook_output
            else batch_folder / f"報銷清單_Reimbursement list {profile['name']} {reimbursement_date}.xlsx"
        )
        checklist_path = generated / "taobao-evidence-checklist.xlsx"
        queue_path = generated / "taobao-evidence-capture-queue.md"
        evidence_summary_path = generated / "taobao-evidence-summary.json"
        compile_summary_path = generated / "reimbursement-state-compile-summary.json"

        manifest = build_manifest(batch, profile, orders)
        manifest["summary"]["reimbursement_date"] = reimbursement_date
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        write_review_workbook(review_path, orders, parse_skipped(batch))

        try:
            template = (args.template or find_template(batch_folder)).resolve()
        except FileNotFoundError:
            template = None
        template_used = write_reimbursement_workbook(template, workbook_path, orders, profile, reimbursement_date)

        records = build_records_from_state(connection, batch_folder=batch_folder, orders=order_rows)
        write_checklist(checklist_path, records)
        write_capture_queue(queue_path, batch_folder, records)
        if args.no_print_flat:
            print_flat = {"folder": str(batch_folder / PRINT_FLAT_ALL_ROOT), "links": [], "warnings": []}
        else:
            print_flat = write_print_flat_folder(batch_folder / PRINT_FLAT_ALL_ROOT, records)
        evidence_summary = write_evidence_summary(
            path=evidence_summary_path,
            batch_folder=batch_folder,
            evidence_root=evidence_root,
            checklist_path=checklist_path,
            queue_path=queue_path,
            records=records,
            print_flat=print_flat,
        )

        compile_summary = {
            "schema": COMPILE_SCHEMA,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "database": str(db_path),
            "batch": str(batch_folder),
            "orders": len(orders),
            "items": sum(len(order.items) for order in orders),
            "claim_totals": claim_totals_by_currency(orders),
            "reimbursement_date": reimbursement_date,
            "manifest": str(manifest_path),
            "review_workbook": str(review_path),
            "reimbursement_workbook": str(workbook_path),
            "template": template_used,
            "checklist": str(checklist_path),
            "queue": str(queue_path),
            "print_flat_folder": print_flat["folder"],
            "print_flat_links": len(print_flat["links"]),
            "order_screenshot_warnings": evidence_summary["order_screenshot_warnings"],
            "payment_screenshot_warnings": evidence_summary["payment_screenshot_warnings"],
        }
        compile_summary_path.write_text(json.dumps(compile_summary, ensure_ascii=False, indent=2), encoding="utf-8")

        state_db.upsert_artifacts(
            connection,
            batch_id=batch_id,
            artifacts=build_artifacts(batch_folder, generated, evidence_summary),
        )
        connection.commit()

    print(json.dumps(compile_summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
