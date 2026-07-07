#!/usr/bin/env python3
"""Build a normal reimbursement package from an edited Taobao order export."""

from __future__ import annotations

import argparse
import copy
import json
import re
import shutil
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, date
from pathlib import Path
from typing import Any

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter


DEFAULT_DOC_TYPE = "淘寶截圖加付款紀錄 Taobao capture screen & payment record"
DEFAULT_MISSING_REASON = "商家未提供"
DEFAULT_EVIDENCE = ["taobao_order_detail_screenshot", "payment_record_screenshot"]

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


@dataclass
class OrderItem:
    name: str
    link: str
    style: str
    quantity: str
    item_amount_rmb: float | None


@dataclass
class ReimbursementOrder:
    source: str
    order_no: str
    date: str
    datetime: str
    shop: str
    status: str
    item_label: str
    amount_rmb: float
    shipping_rmb: float | None
    item_count: int
    items: list[OrderItem]
    document_type: str
    missing_receipt_reason: str
    evidence_required: list[str]


def cell_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def parse_money(value: Any) -> float | None:
    text = cell_text(value)
    if not text:
        return None
    text = text.replace("￥", "").replace(",", "").replace("RMB", "").strip()
    try:
        return float(text)
    except ValueError:
        return None


def parse_datetime(value: Any) -> tuple[str, str]:
    if isinstance(value, datetime):
        return value.date().isoformat(), value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.isoformat(), value.isoformat()
    text = cell_text(value)
    if not text:
        return "", ""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.date().isoformat(), parsed.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
    return text[:10], text


def compact_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def clean_product_name(text: str) -> str:
    text = cell_text(text)
    text = re.sub(r"【优惠价】", "", text)
    text = re.sub(r"\[[^\]]{1,20}\]", "", text)
    return compact_spaces(text)


def shorten_label(text: str, max_chars: int = 24) -> str:
    text = clean_product_name(text)
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "..."


def make_item_label(items: list[OrderItem]) -> str:
    if not items:
        return ""
    names = [clean_product_name(item.name) for item in items if item.name]
    if not names:
        return ""
    unique_names = []
    for name in names:
        if name not in unique_names:
            unique_names.append(name)
    label = shorten_label(unique_names[0])
    if len(unique_names) > 1 or len(items) > 1:
        label += f" 等{len(items)}项"
    return label


def row_has_order_level_fields(row: list[Any]) -> bool:
    # B, C, D, J, K in the Taobao export indicate an order's first row even if
    # the user blanked out the order number to exclude it.
    return any(cell_text(row[index]) for index in (1, 2, 3, 9, 10))


def row_has_item_fields(row: list[Any]) -> bool:
    return any(cell_text(row[index]) for index in (4, 5, 6, 7, 8))


def worksheet_row_values(worksheet: Any, row_number: int) -> list[Any]:
    return [worksheet.cell(row_number, column).value for column in range(1, 12)]


def order_spans_from_merged_column_a(worksheet: Any) -> list[tuple[int, int]]:
    """Return Taobao order row spans, preferring the export's column-A merges."""
    spans: list[tuple[int, int]] = []
    covered_rows: set[int] = set()

    for merged_range in worksheet.merged_cells.ranges:
        if merged_range.min_col == 1 and merged_range.max_col == 1 and merged_range.min_row >= 2:
            spans.append((merged_range.min_row, merged_range.max_row))
            covered_rows.update(range(merged_range.min_row, merged_range.max_row + 1))

    for row_number in range(2, worksheet.max_row + 1):
        if row_number in covered_rows:
            continue
        row = worksheet_row_values(worksheet, row_number)
        if cell_text(row[0]) or row_has_order_level_fields(row) or row_has_item_fields(row):
            spans.append((row_number, row_number))

    spans.sort()
    normalized: list[tuple[int, int]] = []
    last_end = 1
    for start_row, end_row in spans:
        if start_row <= last_end:
            continue
        normalized.append((start_row, end_row))
        last_end = end_row
    return normalized


def items_from_span(worksheet: Any, start_row: int, end_row: int) -> list[OrderItem]:
    items: list[OrderItem] = []
    for row_number in range(start_row, end_row + 1):
        row = worksheet_row_values(worksheet, row_number)
        if row_has_item_fields(row):
            items.append(order_item_from_row(row))
    return items


def read_taobao_orders(path: Path, include_status: str) -> tuple[list[ReimbursementOrder], dict[str, int]]:
    workbook = openpyxl.load_workbook(path, data_only=True)
    worksheet = workbook.active

    orders: list[ReimbursementOrder] = []
    skipped = {
        "blank_order_number": 0,
        "status": 0,
        "empty_groups": 0,
    }

    for start_row, end_row in order_spans_from_merged_column_a(worksheet):
        order_row = worksheet_row_values(worksheet, start_row)
        items = items_from_span(worksheet, start_row, end_row)
        order_no = cell_text(order_row[0])

        if not order_no:
            if row_has_order_level_fields(order_row) or items:
                skipped["blank_order_number"] += 1
            else:
                skipped["empty_groups"] += 1
            continue

        status = cell_text(order_row[2])
        if include_status and status != include_status:
            skipped["status"] += 1
            continue

        order_date, order_datetime = parse_datetime(order_row[1])
        paid = parse_money(order_row[9])
        orders.append(
            ReimbursementOrder(
                source="taobao",
                order_no=order_no,
                date=order_date,
                datetime=order_datetime,
                shop=cell_text(order_row[3]),
                status=status,
                item_label=make_item_label(items),
                amount_rmb=paid or 0.0,
                shipping_rmb=parse_money(order_row[10]),
                item_count=len(items),
                items=items,
                document_type=DEFAULT_DOC_TYPE,
                missing_receipt_reason=DEFAULT_MISSING_REASON,
                evidence_required=list(DEFAULT_EVIDENCE),
            )
        )

    return orders, skipped


def order_item_from_row(row: list[Any]) -> OrderItem:
    return OrderItem(
        name=cell_text(row[4]),
        link=cell_text(row[5]),
        style=cell_text(row[6]),
        quantity=cell_text(row[7]),
        item_amount_rmb=parse_money(row[8]),
    )


def order_to_json(order: ReimbursementOrder) -> dict[str, Any]:
    result = asdict(order)
    result["items"] = [asdict(item) for item in order.items]
    return result


def write_manifest(
    path: Path,
    source_file: Path,
    orders: list[ReimbursementOrder],
    skipped: dict[str, int],
    profile: dict[str, str],
) -> None:
    payload = {
        "schema": "prepare-reimbursements.taobao-normal.v1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_file": str(source_file),
        "profile": profile,
        "summary": {
            "included_orders": len(orders),
            "total_amount_rmb": round(sum(order.amount_rmb for order in orders), 2),
            "skipped": skipped,
        },
        "orders": [order_to_json(order) for order in orders],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_review_workbook(path: Path, orders: list[ReimbursementOrder], skipped: dict[str, int]) -> None:
    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.title = "review"
    headers = [
        "No.",
        "Order No.",
        "Date",
        "Shop",
        "Item Label",
        "Amount RMB",
        "Status",
        "Item Count",
        "Document Type",
        "Missing Receipt Reason",
        "Evidence Required",
        "Notes",
    ]
    worksheet.append(headers)
    for cell in worksheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="4F81BD")
        cell.alignment = Alignment(wrap_text=True, vertical="center")

    for index, order in enumerate(orders, 1):
        worksheet.append(
            [
                index,
                order.order_no,
                order.date,
                order.shop,
                order.item_label,
                order.amount_rmb,
                order.status,
                order.item_count,
                order.document_type,
                order.missing_receipt_reason,
                ", ".join(order.evidence_required),
                "",
            ]
        )

    summary_row = len(orders) + 3
    worksheet.cell(summary_row, 1, "Included orders")
    worksheet.cell(summary_row, 2, len(orders))
    worksheet.cell(summary_row + 1, 1, "Total RMB")
    worksheet.cell(summary_row + 1, 2, round(sum(order.amount_rmb for order in orders), 2))
    worksheet.cell(summary_row + 2, 1, "Skipped blank order number")
    worksheet.cell(summary_row + 2, 2, skipped.get("blank_order_number", 0))
    worksheet.cell(summary_row + 3, 1, "Skipped status")
    worksheet.cell(summary_row + 3, 2, skipped.get("status", 0))

    widths = [8, 24, 14, 22, 36, 14, 12, 12, 52, 22, 46, 24]
    for index, width in enumerate(widths, 1):
        worksheet.column_dimensions[get_column_letter(index)].width = width
    for row in worksheet.iter_rows():
        for cell in row:
            cell.alignment = copy.copy(cell.alignment)
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    worksheet.freeze_panes = "A2"
    workbook.save(path)


def find_order_export(folder: Path) -> Path:
    preferred = folder / "订单数据-报销.xlsx"
    if preferred.exists():
        return preferred
    fallback = folder / "订单数据.xlsx"
    if fallback.exists():
        return fallback
    matches = sorted(folder.glob("订单数据*.xlsx"), key=lambda item: item.stat().st_mtime, reverse=True)
    if matches:
        return matches[0]
    raise FileNotFoundError(f"No Taobao order export found in {folder}")


def find_reimbursement_root(folder: Path) -> Path:
    current = folder.resolve()
    for parent in [current, *current.parents]:
        if parent.name == "Reimbursement":
            return parent
    return folder.parent


def find_template(folder: Path) -> Path:
    root = find_reimbursement_root(folder)
    candidates = [
        path
        for path in root.rglob("報銷清單_Reimbursement list*.xlsx")
        if path.is_file() and folder.resolve() not in [path.parent.resolve()]
    ]
    if not candidates:
        raise FileNotFoundError("No previous normal reimbursement workbook template found")
    return max(candidates, key=lambda item: item.stat().st_mtime)


def copy_row_style(worksheet: Any, source_row: int, target_row: int, max_column: int = 8) -> None:
    worksheet.row_dimensions[target_row].height = worksheet.row_dimensions[source_row].height
    for column in range(1, max_column + 1):
        source = worksheet.cell(source_row, column)
        target = worksheet.cell(target_row, column)
        if source.has_style:
            target._style = copy.copy(source._style)
        target.font = copy.copy(source.font)
        target.fill = copy.copy(source.fill)
        target.border = copy.copy(source.border)
        target.alignment = copy.copy(source.alignment)
        target.number_format = source.number_format
        target.protection = copy.copy(source.protection)


def locate_row(worksheet: Any, text: str, column: int = 1) -> int:
    for row in range(1, worksheet.max_row + 1):
        value = worksheet.cell(row, column).value
        if isinstance(value, str) and value.strip().startswith(text):
            return row
    raise ValueError(f"Could not locate row starting with {text!r}")


def create_builtin_workbook(row_count: int) -> openpyxl.Workbook:
    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.title = "Sheet1"

    widths = {
        "A": 20.85546875,
        "B": 11.140625,
        "C": 31.85546875,
        "D": 13.85546875,
        "E": 14.28515625,
        "F": 25.5703125,
        "G": 58.42578125,
        "H": 32.28515625,
    }
    for column, width in widths.items():
        worksheet.column_dimensions[column].width = width

    worksheet.merge_cells("A2:H2")
    for row in range(3, 7):
        worksheet.merge_cells(start_row=row, start_column=2, end_row=row, end_column=8)

    worksheet["A2"] = "Reimbursement list"
    worksheet["A3"] = "Name:"
    worksheet["A4"] = "Bank name:"
    worksheet["A5"] = "Bank account number:"
    worksheet["A6"] = "Leader's name"

    headers = [
        "No.",
        "Date",
        "Item",
        "Amount (HKD)",
        "Amount (RMB)",
        "Amount (Other Currencies)",
        "Document type",
        "Reason for missing receipt/invoice",
    ]
    for column, header in enumerate(headers, 1):
        worksheet.cell(7, column, header)

    total_row = 8 + max(row_count, 20)
    signature_row = total_row + 6
    worksheet.merge_cells(start_row=total_row, start_column=1, end_row=total_row, end_column=3)
    worksheet.merge_cells(start_row=signature_row, start_column=1, end_row=signature_row, end_column=3)
    worksheet["A" + str(total_row)] = "Total:"
    worksheet["A" + str(signature_row)] = "Leader's Signature:"
    worksheet["G" + str(signature_row)] = "Date:"

    title_fill = PatternFill("solid", fgColor="D9EAF7")
    header_fill = PatternFill("solid", fgColor="BDD7EE")
    thin = Side(style="thin", color="808080")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    for row in worksheet.iter_rows(min_row=2, max_row=signature_row, min_col=1, max_col=8):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="center")
            cell.border = border
    worksheet["A2"].font = Font(bold=True, size=14)
    worksheet["A2"].fill = title_fill
    worksheet.row_dimensions[2].height = 24
    for row in range(3, 7):
        worksheet.cell(row, 1).font = Font(bold=True)
    for cell in worksheet[7]:
        cell.font = Font(bold=True)
        cell.fill = header_fill
    worksheet.freeze_panes = "A8"
    return workbook


def load_template_or_builtin(template: Path | None, output: Path, row_count: int) -> tuple[openpyxl.Workbook, str]:
    if template:
        try:
            shutil.copyfile(template, output)
            return openpyxl.load_workbook(output), str(template)
        except OSError as error:
            print(f"warning: could not read template {template}: {error}")
    return create_builtin_workbook(row_count), "built-in workbook layout"


def write_reimbursement_workbook(
    template: Path | None,
    output: Path,
    orders: list[ReimbursementOrder],
    profile: dict[str, str],
    submission_date: str,
) -> str:
    output.parent.mkdir(parents=True, exist_ok=True)
    workbook, template_used = load_template_or_builtin(template, output, len(orders))
    worksheet = workbook.active

    worksheet["B3"] = profile["name"]
    worksheet["B4"] = profile["bank"]
    worksheet["B5"] = profile["account"]
    worksheet["B6"] = profile["leader"]

    header_row = locate_row(worksheet, "No.")
    item_start = header_row + 1
    total_row = locate_row(worksheet, "Total:")
    available_rows = total_row - item_start
    needed_rows = max(0, len(orders) - available_rows)

    if needed_rows:
        worksheet.insert_rows(total_row, needed_rows)
        for row in range(total_row, total_row + needed_rows):
            copy_row_style(worksheet, total_row - 1, row)
        total_row += needed_rows

    for row in range(item_start, total_row):
        for column in range(1, 9):
            worksheet.cell(row, column).value = None

    for index, order in enumerate(orders, 1):
        row = item_start + index - 1
        worksheet.cell(row, 1, index)
        worksheet.cell(row, 2, datetime.strptime(order.date, "%Y-%m-%d").date() if order.date else "")
        worksheet.cell(row, 3, order.item_label)
        worksheet.cell(row, 4, None)
        worksheet.cell(row, 5, order.amount_rmb)
        worksheet.cell(row, 6, None)
        worksheet.cell(row, 7, order.document_type)
        worksheet.cell(row, 8, order.missing_receipt_reason)

    last_item_row = max(item_start, item_start + len(orders) - 1)
    worksheet.cell(total_row, 1, "Total:")
    worksheet.cell(total_row, 4, f"=SUM(D{item_start}:D{last_item_row})")
    worksheet.cell(total_row, 5, f"=SUM(E{item_start}:E{last_item_row})")
    worksheet.cell(total_row, 6, f"=SUM(F{item_start}:F{last_item_row})")

    signature_date = datetime.strptime(submission_date, "%Y-%m-%d").strftime("%d/%m/%Y")
    for row in range(total_row + 1, min(worksheet.max_row, total_row + 12) + 1):
        if worksheet.cell(row, 7).value == "Date:":
            worksheet.cell(row, 8, signature_date)
            break

    workbook.save(output)
    return template_used


def build_profile(args: argparse.Namespace) -> dict[str, str]:
    return {
        "name": args.name,
        "bank": args.bank,
        "account": args.account,
        "leader": args.leader,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder", type=Path, default=Path.cwd(), help="Reimbursement batch folder")
    parser.add_argument("--orders", type=Path, help="Edited Taobao order export. Defaults to 订单数据-报销.xlsx in folder")
    parser.add_argument("--out-dir", type=Path, help="Output directory. Defaults to <folder>/generated")
    parser.add_argument("--template", type=Path, help="Previous normal reimbursement workbook to use as template")
    parser.add_argument("--submission-date", default=date.today().isoformat(), help="YYYY-MM-DD date for output filename and signature")
    parser.add_argument("--include-status", default="交易成功", help="Only include orders with this status; blank includes all statuses")
    parser.add_argument("--name", default="Applicant Name")
    parser.add_argument("--bank", default="Bank Name")
    parser.add_argument("--account", default="Bank Account Number")
    parser.add_argument("--leader", default="Leader Name")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    folder = args.folder.resolve()
    orders_path = (args.orders or find_order_export(folder)).resolve()
    out_dir = (args.out_dir or folder / "generated").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        template = (args.template or find_template(folder)).resolve()
    except FileNotFoundError:
        template = None
    profile = build_profile(args)

    orders, skipped = read_taobao_orders(orders_path, args.include_status)

    manifest_path = out_dir / "reimbursement-manifest.json"
    review_path = out_dir / "reimbursement-review.xlsx"
    workbook_path = out_dir / f"報銷清單_Reimbursement list {args.name} {args.submission_date}.xlsx"

    write_manifest(manifest_path, orders_path, orders, skipped, profile)
    write_review_workbook(review_path, orders, skipped)
    template_used = write_reimbursement_workbook(template, workbook_path, orders, profile, args.submission_date)

    print(f"source: {orders_path}")
    print(f"template: {template_used}")
    print(f"included orders: {len(orders)}")
    print(f"total amount RMB: {sum(order.amount_rmb for order in orders):.2f}")
    print(f"skipped blank order number: {skipped.get('blank_order_number', 0)}")
    print(f"skipped status: {skipped.get('status', 0)}")
    print(f"manifest: {manifest_path}")
    print(f"review workbook: {review_path}")
    print(f"reimbursement workbook: {workbook_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
