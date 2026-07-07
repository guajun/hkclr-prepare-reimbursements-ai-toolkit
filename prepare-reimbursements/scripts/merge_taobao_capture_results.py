#!/usr/bin/env python3
"""Merge Taobao browser-capture results back into the reimbursement manifest."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ALIPAY_DETAIL_URL = "https://consumeprod.alipay.com/record/detail/simpleDetail.htm?bizType=TRADE&bizInNo={trade_no}"
TRADE_NO_RE = re.compile(r"\b20\d{26}\b")


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def first_text(record: dict[str, Any], names: list[str]) -> str:
    for name in names:
        value = record.get(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def normalized_order_no(record: dict[str, Any]) -> str:
    return first_text(record, ["order_no", "orderNo", "order", "biz_order_id", "bizOrderId"])


def normalized_trade_no(record: dict[str, Any]) -> str:
    direct = first_text(
        record,
        [
            "alipay_trade_no",
            "alipayTradeNo",
            "payment_trade_no",
            "paymentTradeNo",
            "trade_no",
            "tradeNo",
            "biz_in_no",
            "bizInNo",
        ],
    )
    if direct:
        return direct
    for name in ("text", "page_text", "pageText", "raw", "body"):
        value = record.get(name)
        if value:
            match = TRADE_NO_RE.search(str(value))
            if match:
                return match.group(0)
    return ""


def load_results(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    results = payload.get("results", [])
    if not isinstance(results, list):
        raise ValueError(f"{path} does not contain a results list")
    return [item for item in results if isinstance(item, dict)]


def merge_results(manifest: dict[str, Any], results: list[dict[str, Any]]) -> dict[str, Any]:
    by_order = {normalized_order_no(record): record for record in results if normalized_order_no(record)}
    updated = 0
    missing_trade_no: list[str] = []

    for order in manifest.get("orders", []):
        order_no = str(order.get("order_no") or "")
        record = by_order.get(order_no)
        if not record:
            missing_trade_no.append(order_no)
            continue

        taobao_url = first_text(record, ["taobao_order_detail_url", "taobaoDetailUrl", "url", "detail_url"])
        if taobao_url:
            order["taobao_order_detail_url"] = taobao_url

        screenshot = first_text(record, ["taobao_order_screenshot", "taobaoScreenshot", "saved", "path"])
        if screenshot:
            order["taobao_order_screenshot"] = screenshot

        trade_no = normalized_trade_no(record)
        if trade_no:
            order["alipay_trade_no"] = trade_no
            order["alipay_detail_url"] = ALIPAY_DETAIL_URL.format(trade_no=trade_no)
            updated += 1
        elif not order.get("alipay_trade_no"):
            missing_trade_no.append(order_no)

    manifest.setdefault("capture", {})
    manifest["capture"]["taobao_results_merged_at"] = datetime.now().isoformat(timespec="seconds")
    manifest["capture"]["alipay_trade_numbers_found"] = updated
    manifest["capture"]["alipay_trade_numbers_missing"] = missing_trade_no
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder", type=Path, required=True, help="Reimbursement batch folder")
    parser.add_argument("--manifest", type=Path, help="Manifest path. Defaults to <folder>/generated/reimbursement-manifest.json")
    parser.add_argument(
        "--results",
        type=Path,
        help="Taobao browser capture results JSON. Defaults to <folder>/generated/taobao-order-screenshot-results.json",
    )
    parser.add_argument("--out", type=Path, help="Output manifest path. Defaults to updating --manifest in place")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    folder = args.folder.resolve()
    generated = folder / "generated"
    manifest_path = (args.manifest or generated / "reimbursement-manifest.json").resolve()
    results_path = (args.results or generated / "taobao-order-screenshot-results.json").resolve()
    output_path = (args.out or manifest_path).resolve()

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    results = load_results(results_path)
    merged = merge_results(manifest, results)
    output_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = {
        "manifest": str(output_path),
        "results": str(results_path),
        "orders": len(merged.get("orders", [])),
        "alipay_trade_numbers_found": merged.get("capture", {}).get("alipay_trade_numbers_found", 0),
        "alipay_trade_numbers_missing": merged.get("capture", {}).get("alipay_trade_numbers_missing", []),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
