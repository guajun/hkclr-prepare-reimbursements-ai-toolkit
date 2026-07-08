#!/usr/bin/env python3
"""Normalize Alipay payment screenshots from known-good browser capture presets."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw


APPROVED_FINAL_SIZES = {(820, 777), (911, 777), (1425, 801)}


@dataclass(frozen=True)
class ScreenshotProfile:
    name: str
    raw_width_min: int
    raw_width_max: int
    raw_height_min: int
    raw_height_max: int
    crop_width: int
    crop_height: int
    note: str
    final_width: int | None = None
    final_height: int | None = None

    def matches(self, width: int, height: int) -> bool:
        return (
            self.raw_width_min <= width <= self.raw_width_max
            and self.raw_height_min <= height <= self.raw_height_max
        )

    @property
    def final_size(self) -> tuple[int, int]:
        return (self.final_width or self.crop_width, self.final_height or self.crop_height)


KNOWN_PROFILES = [
    ScreenshotProfile(
        name="alipay-iab-wide-2851x1603-to-1425x801",
        raw_width_min=2700,
        raw_width_max=2950,
        raw_height_min=1550,
        raw_height_max=1680,
        crop_width=1425,
        crop_height=801,
        note="Fresh wide in-app browser tab, viewport screenshot, duplicated as a 2x2 tile.",
    ),
    ScreenshotProfile(
        name="alipay-iab-standard-1822x1554-to-911x777",
        raw_width_min=1760,
        raw_width_max=1900,
        raw_height_min=1500,
        raw_height_max=1605,
        crop_width=911,
        crop_height=777,
        note="Standard in-app browser tab, viewport screenshot, duplicated as a 2x2 tile.",
    ),
    ScreenshotProfile(
        name="alipay-iab-narrow-1485x1554-to-820x777",
        raw_width_min=1440,
        raw_width_max=1560,
        raw_height_min=1500,
        raw_height_max=1605,
        crop_width=820,
        crop_height=777,
        note="Narrow in-app browser tab fallback. Use only when the final image still shows paid amount and method.",
    ),
]


KNOWN_BAD_IAB_VIEWPORT_RANGES = [
    {
        "raw_width": (4200, 4350),
        "raw_height": (2350, 2450),
        "warning": "Codex in-app browser viewport override produced a 2x2 tiled capture around 4276x2404. Do not normalize this as final evidence; use a real browser capture engine or recalibrate after the screenshot backend is fixed.",
    },
]


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def choose_profile(width: int, height: int) -> ScreenshotProfile | None:
    for profile in KNOWN_PROFILES:
        if profile.matches(width, height):
            return profile
    return None


def known_bad_iab_warning(width: int, height: int) -> str:
    for item in KNOWN_BAD_IAB_VIEWPORT_RANGES:
        width_min, width_max = item["raw_width"]
        height_min, height_max = item["raw_height"]
        if width_min <= width <= width_max and height_min <= height <= height_max:
            return str(item["warning"])
    return ""


def lanczos_resampling() -> int:
    return getattr(Image, "Resampling", Image).LANCZOS


def load_manifest(batch_folder: Path, manifest_path: Path | None) -> list[dict[str, Any]]:
    path = manifest_path or batch_folder / "generated" / "reimbursement-manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    return list(manifest["orders"])


def iter_targets(
    batch_folder: Path,
    manifest_path: Path | None,
    evidence_root: Path | None,
    start: int | None,
    end: int | None,
) -> list[dict[str, Any]]:
    orders = load_manifest(batch_folder, manifest_path)
    root = evidence_root or batch_folder / "物品" / "taobao"
    targets: list[dict[str, Any]] = []
    for index, order in enumerate(orders, 1):
        if start is not None and index < start:
            continue
        if end is not None and index > end:
            continue
        order_no = str(order["order_no"])
        stem = f"{index:02d}_{order_no}_payment_record.png"
        folder = root / f"{index:02d}_{order_no}"
        raw_path = folder / "_raw_payment_screenshots" / stem
        out_path = folder / stem
        targets.append(
            {
                "index": index,
                "order_no": order_no,
                "raw_path": raw_path,
                "out_path": out_path,
            }
        )
    return targets


def normalize_one(
    raw_path: Path,
    out_path: Path,
    backup_existing: bool,
    dry_run: bool,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "raw_path": str(raw_path),
        "path": str(out_path),
    }
    if not raw_path.exists():
        record["status"] = "missing_raw"
        return record

    with Image.open(raw_path) as image:
        width, height = image.size
        record["raw_size"] = [width, height]
        if (width, height) in APPROVED_FINAL_SIZES:
            record["profile"] = "already-normalized"
            record["final_size"] = [width, height]
            if not dry_run:
                out_path.parent.mkdir(parents=True, exist_ok=True)
                if backup_existing and out_path.exists() and out_path.resolve() != raw_path.resolve():
                    backup_path = out_path.parent / "_previous_payment_screenshots" / out_path.name
                    backup_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(out_path, backup_path)
                    record["backup_path"] = str(backup_path)
                if out_path.resolve() != raw_path.resolve():
                    shutil.copy2(raw_path, out_path)
            record["status"] = "copied"
            return record

        bad_warning = known_bad_iab_warning(width, height)
        if bad_warning:
            record["status"] = "known_bad_iab_viewport_capture"
            record["warning"] = bad_warning
            return record

        profile = choose_profile(width, height)
        if profile is None:
            record["status"] = "unrecognized_raw_size"
            record["warning"] = "Raw screenshot does not match an approved Alipay capture preset; do not overwrite final evidence."
            return record

        record["profile"] = profile.name
        record["profile_note"] = profile.note
        final_width, final_height = profile.final_size
        record["crop_size"] = [profile.crop_width, profile.crop_height]
        record["final_size"] = [final_width, final_height]
        if not dry_run:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            if backup_existing and out_path.exists() and out_path.resolve() != raw_path.resolve():
                backup_path = out_path.parent / "_previous_payment_screenshots" / out_path.name
                backup_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(out_path, backup_path)
                record["backup_path"] = str(backup_path)
            cropped = image.crop((0, 0, profile.crop_width, profile.crop_height))
            if cropped.size != (final_width, final_height):
                cropped = cropped.resize((final_width, final_height), lanczos_resampling())
            cropped.save(out_path)
        record["status"] = "normalized"
        return record


def make_contact_sheet(records: list[dict[str, Any]], path: Path) -> None:
    images: list[tuple[dict[str, Any], Image.Image]] = []
    try:
        for record in records:
            if record.get("status") not in {"normalized", "copied"}:
                continue
            screenshot_path = Path(str(record["path"]))
            if not screenshot_path.exists():
                continue
            with Image.open(screenshot_path) as image:
                thumbnail = image.convert("RGB")
                thumbnail.thumbnail((440, 260))
                images.append((record, thumbnail.copy()))
    except OSError:
        return

    if not images:
        return

    columns = 2
    tile_width = 480
    tile_height = 330
    rows = (len(images) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * tile_width, rows * tile_height), "white")
    draw = ImageDraw.Draw(sheet)
    for offset, (record, image) in enumerate(images):
        col = offset % columns
        row = offset // columns
        x = col * tile_width
        y = row * tile_height
        label = f"{record.get('index', ''):>02} {record.get('order_no', '')} {record.get('final_size', '')}"
        draw.text((x + 12, y + 8), label, fill="black")
        sheet.paste(image, (x + 12, y + 34))
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(path)


def write_preset_file(path: Path) -> None:
    payload = {
        "schema": "prepare-reimbursements.alipay-screenshot-preset.v1",
        "browser_capture_preset": {
            "browser": "Codex in-app browser",
            "tab": "single dedicated logged-in Alipay detail tab kept alive for the batch",
            "capture_call": "tab.screenshot({})",
            "avoid": [
                "fullPage screenshots",
                "mid-batch viewport resizing",
                "opening new Alipay tabs after login",
                "closing or finalizing the live Alipay tab before the batch is complete",
                "accepting raw tiled images as final evidence",
            ],
            "preflight": [
                "Open one alipay_detail_url directly.",
                "Confirm DOM contains 交易成功, 流水号, 订单金额, = 实付金额.",
                "Save raw screenshot to _raw_payment_screenshots.",
                "Normalize the raw screenshot and inspect the first final image before batch capture.",
            ],
        },
        "approved_final_sizes": sorted([list(size) for size in APPROVED_FINAL_SIZES]),
        "profiles": [
            {
                "name": profile.name,
                "raw_width": [profile.raw_width_min, profile.raw_width_max],
                "raw_height": [profile.raw_height_min, profile.raw_height_max],
                "crop": [0, 0, profile.crop_width, profile.crop_height],
                "final_size": list(profile.final_size),
                "note": profile.note,
            }
            for profile in KNOWN_PROFILES
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder", type=Path, required=True, help="Reimbursement batch folder")
    parser.add_argument("--manifest", type=Path, help="Manifest path. Defaults to <folder>/generated/reimbursement-manifest.json")
    parser.add_argument("--evidence-root", type=Path, help="Evidence root. Defaults to <folder>/物品/taobao")
    parser.add_argument("--start", type=int, help="First manifest index to process")
    parser.add_argument("--end", type=int, help="Last manifest index to process")
    parser.add_argument("--dry-run", action="store_true", help="Report selected presets without writing final files")
    parser.add_argument("--no-backup-existing", action="store_true", help="Do not back up existing final screenshots before overwrite")
    parser.add_argument("--contact-sheet", action="store_true", help="Write a contact sheet for visual review")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    batch_folder = args.folder.resolve()
    generated = batch_folder / "generated"
    targets = iter_targets(
        batch_folder=batch_folder,
        manifest_path=args.manifest.resolve() if args.manifest else None,
        evidence_root=args.evidence_root.resolve() if args.evidence_root else None,
        start=args.start,
        end=args.end,
    )
    records: list[dict[str, Any]] = []
    for target in targets:
        record = {
            "index": target["index"],
            "order_no": target["order_no"],
            **normalize_one(
                target["raw_path"],
                target["out_path"],
                backup_existing=not args.no_backup_existing,
                dry_run=args.dry_run,
            ),
        }
        records.append(record)

    preset_path = generated / "alipay-screenshot-preset.json"
    report_path = generated / "alipay-payment-screenshot-normalize-report.json"
    write_preset_file(preset_path)
    if args.contact_sheet and not args.dry_run:
        make_contact_sheet(records, generated / "alipay-payment-screenshot-contact-sheet.png")

    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "preset": str(preset_path),
        "processed": len(records),
        "normalized": sum(1 for record in records if record.get("status") == "normalized"),
        "copied": sum(1 for record in records if record.get("status") == "copied"),
        "missing_raw": sum(1 for record in records if record.get("status") == "missing_raw"),
        "unrecognized_raw_size": [
            {
                "index": record["index"],
                "order_no": record["order_no"],
                "raw_size": record.get("raw_size"),
                "raw_path": record.get("raw_path"),
            }
            for record in records
            if record.get("status") == "unrecognized_raw_size"
        ],
        "known_bad_iab_viewport_capture": [
            {
                "index": record["index"],
                "order_no": record["order_no"],
                "raw_size": record.get("raw_size"),
                "raw_path": record.get("raw_path"),
                "warning": record.get("warning"),
            }
            for record in records
            if record.get("status") == "known_bad_iab_viewport_capture"
        ],
        "records": records,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if summary["unrecognized_raw_size"] or summary["known_bad_iab_viewport_capture"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
