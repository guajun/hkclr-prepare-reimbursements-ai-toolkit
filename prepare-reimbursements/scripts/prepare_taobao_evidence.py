#!/usr/bin/env python3
"""Prepare Taobao evidence folders and capture checklists from a manifest."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def existing_any(folder: Path, patterns: list[str]) -> list[Path]:
    hits: list[Path] = []
    for pattern in patterns:
        hits.extend(folder.glob(pattern))
    return sorted(set(hits), key=lambda path: path.name)


def make_order_folder(evidence_root: Path, index: int, order_no: str) -> Path:
    folder = evidence_root / f"{index:02d}_{order_no}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def write_folder_note(folder: Path, order: dict[str, Any], index: int) -> tuple[str, str, str]:
    order_no = order["order_no"]
    order_file = f"{index:02d}_{order_no}_taobao_order_detail.png"
    payment_file = f"{index:02d}_{order_no}_payment_record.png"
    combined_file = f"{index:02d}_{order_no}_combined_receipt.png"
    note_path = folder / "_放截图到这里.txt"
    note_path.write_text(
        "\n".join(
            [
                f"订单号: {order_no}",
                f"日期: {order['date']}",
                f"店铺: {order['shop']}",
                f"金额: RMB {order['amount_rmb']}",
                f"物品: {order['item_label']}",
                "",
                "需要放入:",
                f"1. 淘宝订单详情截图: {order_file}",
                f"2. 支付宝付款记录截图: {payment_file}",
                f"3. 可选合成凭证: {combined_file}",
            ]
        ),
        encoding="utf-8",
    )
    return order_file, payment_file, combined_file


def build_records(orders: list[dict[str, Any]], evidence_root: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for index, order in enumerate(orders, 1):
        folder = make_order_folder(evidence_root, index, order["order_no"])
        order_file, payment_file, combined_file = write_folder_note(folder, order, index)
        order_hits = [
            path
            for path in existing_any(folder, ["*order*.*", "*taobao*.*", "*淘宝*.*", "*订单*.*"])
            if path.name != "_放截图到这里.txt"
        ]
        payment_hits = [
            path
            for path in existing_any(folder, ["*payment*.*", "*alipay*.*", "*支付宝*.*", "*付款*.*"])
            if path.name != "_放截图到这里.txt"
        ]
        combined_hits = [
            path
            for path in existing_any(folder, ["*combined*.*", "*receipt*.*", "*凭证*.*", "*合成*.*"])
            if path.name != "_放截图到这里.txt"
        ]
        records.append(
            {
                "index": index,
                "folder": folder,
                "order": order,
                "order_file": order_file,
                "payment_file": payment_file,
                "combined_file": combined_file,
                "order_hits": [path.name for path in order_hits],
                "payment_hits": [path.name for path in payment_hits],
                "combined_hits": [path.name for path in combined_hits],
            }
        )
    return records


def style_worksheet(worksheet: Any, widths: list[int], header_color: str = "4F81BD") -> None:
    for cell in worksheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor=header_color)
        cell.alignment = Alignment(wrap_text=True, vertical="center")
    for index, width in enumerate(widths, 1):
        worksheet.column_dimensions[get_column_letter(index)].width = width
    for row in worksheet.iter_rows():
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    worksheet.freeze_panes = "A2"


def write_checklist(path: Path, records: list[dict[str, Any]]) -> None:
    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.title = "evidence-checklist"
    headers = [
        "No.",
        "Order No.",
        "Date",
        "Shop",
        "Item Label",
        "Amount RMB",
        "Item Count",
        "Folder",
        "Taobao Order Screenshot Filename",
        "Payment Screenshot Filename",
        "Order Screenshot Found",
        "Payment Screenshot Found",
        "Combined Receipt Found",
        "First Item Link",
    ]
    worksheet.append(headers)
    for record in records:
        order = record["order"]
        first_link = ""
        if order.get("items"):
            first_link = order["items"][0].get("link") or ""
        worksheet.append(
            [
                record["index"],
                order["order_no"],
                order["date"],
                order["shop"],
                order["item_label"],
                order["amount_rmb"],
                order["item_count"],
                str(record["folder"]),
                record["order_file"],
                record["payment_file"],
                "YES" if record["order_hits"] else "NO",
                "YES" if record["payment_hits"] else "NO",
                "YES" if record["combined_hits"] else "NO",
                first_link,
            ]
        )
        if first_link:
            worksheet.cell(worksheet.max_row, 14).hyperlink = first_link
            worksheet.cell(worksheet.max_row, 14).style = "Hyperlink"
    style_worksheet(worksheet, [6, 24, 12, 22, 40, 12, 10, 72, 42, 42, 18, 18, 18, 50])

    items = workbook.create_sheet("items")
    items.append(["Order No.", "Order Index", "Item Name", "Style", "Quantity", "Item Amount RMB", "Product Link"])
    for record in records:
        order = record["order"]
        for item in order.get("items", []):
            items.append(
                [
                    order["order_no"],
                    record["index"],
                    item.get("name"),
                    item.get("style"),
                    item.get("quantity"),
                    item.get("item_amount_rmb"),
                    item.get("link"),
                ]
            )
            link = item.get("link") or ""
            if link:
                items.cell(items.max_row, 7).hyperlink = link
                items.cell(items.max_row, 7).style = "Hyperlink"
    style_worksheet(items, [24, 10, 64, 30, 10, 16, 60], "70AD47")
    workbook.save(path)


def write_capture_queue(path: Path, batch_folder: Path, records: list[dict[str, Any]]) -> None:
    lines = [
        "# Taobao Evidence Capture Queue",
        "",
        f"Generated: {datetime.now().isoformat(timespec='seconds')}",
        f"Batch: `{batch_folder}`",
        "",
        "For each order, capture:",
        "",
        "1. Taobao order detail screenshot showing order number, items, shop, date, and paid amount.",
        "2. Alipay payment record screenshot showing date/time, merchant/order reference, and amount.",
        "3. Optional combined receipt image after pasting the narrow payment record into the order screenshot.",
        "",
    ]
    for record in records:
        order = record["order"]
        lines.extend(
            [
                f"## {record['index']:02d}. {order['order_no']}",
                "",
                f"- Date: {order['date']}",
                f"- Shop: {order['shop']}",
                f"- Amount: RMB {order['amount_rmb']}",
                f"- Item label: {order['item_label']}",
                f"- Folder: `{record['folder']}`",
                f"- Save Taobao screenshot as: `{record['order_file']}`",
                f"- Save payment screenshot as: `{record['payment_file']}`",
                f"- Optional combined image: `{record['combined_file']}`",
            ]
        )
        if order.get("items"):
            lines.append("- Product links:")
            for item in order["items"][:5]:
                lines.append(f"  - {item.get('name') or ''} / {item.get('style') or ''}: {item.get('link') or ''}")
            if len(order["items"]) > 5:
                lines.append(f"  - ... {len(order['items']) - 5} more items in the checklist workbook")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder", type=Path, required=True, help="Reimbursement batch folder")
    parser.add_argument("--manifest", type=Path, help="Manifest path. Defaults to <folder>/generated/reimbursement-manifest.json")
    parser.add_argument("--evidence-root", type=Path, help="Evidence root. Defaults to <folder>/物品/taobao")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    batch_folder = args.folder.resolve()
    generated = batch_folder / "generated"
    manifest_path = (args.manifest or generated / "reimbursement-manifest.json").resolve()
    evidence_root = (args.evidence_root or batch_folder / "物品" / "taobao").resolve()
    evidence_root.mkdir(parents=True, exist_ok=True)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    records = build_records(manifest["orders"], evidence_root)

    checklist_path = generated / "taobao-evidence-checklist.xlsx"
    queue_path = generated / "taobao-evidence-capture-queue.md"
    summary_path = generated / "taobao-evidence-summary.json"

    write_checklist(checklist_path, records)
    write_capture_queue(queue_path, batch_folder, records)

    summary = {
        "evidence_root": str(evidence_root),
        "orders": len(records),
        "order_screenshots_found": sum(1 for record in records if record["order_hits"]),
        "payment_screenshots_found": sum(1 for record in records if record["payment_hits"]),
        "combined_receipts_found": sum(1 for record in records if record["combined_hits"]),
        "checklist": str(checklist_path),
        "queue": str(queue_path),
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
