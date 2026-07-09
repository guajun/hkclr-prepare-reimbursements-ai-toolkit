---
name: prepare-reimbursements
description: Prepare reimbursement batches from local reimbursement folders, especially Taobao order-export spreadsheets, Taobao order-detail screenshots, Alipay trade numbers extracted from Taobao details, and manually collected receipt screenshots. Use when Codex needs to scan reimbursement source files, apply the "blank order number means not reimbursable" convention, generate a review manifest, fill HKCLR normal reimbursement workbooks, extract Alipay detail URLs from Taobao evidence, plan travel reimbursement workbooks, validate missing evidence for normal and travel reimbursement folders, or coordinate reimbursement-related Alipay cashier-link/HTTP 402 payment flows with dedicated Alipay payment skills.
---

# Prepare Reimbursements

## Workflow

1. Identify the reimbursement batch folder, usually a dated folder such as `<reimbursement-root>\YYYY年M月D日`.
2. Read `references/conventions.md` before changing parsing, evidence, or output rules.
3. For Taobao normal reimbursement batches, run `scripts/build_taobao_normal_reimbursement.py` with `--folder <batch-folder>`.
4. Use Taobao's column-A merged row spans as order boundaries. Treat a blank top-left order number in that merged span as the user's explicit exclusion marker. Keep all SKU rows inside a valid merged span.
5. Generate three artifacts: `reimbursement-manifest.json`, `reimbursement-review.xlsx`, and `報銷清單_Reimbursement list <name> <date>.xlsx`.
6. Report skipped counts, especially blank order numbers and non-success order statuses.
7. For Taobao evidence capture, open each manifest order's `taobao_order_detail_url`, capture the order-detail screenshot with the Taobao order-detail bbox preset below, and extract the field labelled `支付宝交易号`.
8. Merge browser capture results back into the manifest with `scripts/merge_taobao_capture_results.py`, then open each `alipay_detail_url` directly for payment-record screenshots.
9. Before batch Alipay screenshots, calibrate the screenshot preset in `references/conventions.md`: use one logged-in Alipay detail tab, keep it alive for the batch, save one raw viewport screenshot, normalize it with `scripts/normalize_alipay_payment_screenshots.py`, and inspect the result. Reuse the same tab, browser shape, and screenshot call for the batch.
10. Save Alipay raw screenshots under each order's `_raw_payment_screenshots` folder. Produce final payment screenshots only by running `scripts/normalize_alipay_payment_screenshots.py`; do not hand-crop or accept raw tiled screenshots as final evidence.
11. Reopen or inspect the normalized Alipay payment screenshot before accepting it. It must show `交易成功`, product or counterparty, `流水号`, time, `订单金额`, `= 实付金额`, final paid amount, and payment method. If the raw screenshot does not match an approved preset, stop and recalibrate instead of guessing a crop.
12. Run `scripts/prepare_taobao_evidence.py` after final screenshots exist. It refreshes `generated/print-flat/taobao`, a flat all-screenshots print folder with sequential symlinks or hardlinks back to the per-order evidence files, so the user can select all and print while preserving one source of truth.
13. If the Codex in-app browser screenshot output is an abnormal 2x2 tiled image, especially around `4276x2404` after a forced `1920x1080` viewport override, stop the batch. Treat this as a browser screenshot backend failure and use a real browser capture engine instead of masking it with a crop.
14. Search or filter the Alipay bill list only as a fallback when Taobao does not expose a usable `支付宝交易号`.
15. For reimbursement-related live payment links or HTTP 402 payment responses, use the Alipay payment skills as a separate payment workflow, then return here to capture evidence and update the reimbursement packet.
16. Do not automate login, 2FA, wallet binding, payment, or manual app-only flows without the user's explicit intent and active participation.

## Human And Agent Boundary

The human user decides which orders are reimbursable by deleting order numbers from the Taobao export. The user also handles login, 2FA, app-only evidence capture, and ambiguous business-purpose judgment.

The agent parses the edited export, fills deterministic workbook fields, generates manifests, validates required evidence, and prepares screenshot capture checklists or browser automation steps where feasible.

For Taobao normal reimbursement, the agent should treat the Taobao order detail page as the source of the Alipay transaction id. Capture the Taobao screenshot first, extract `支付宝交易号`, then construct `https://consumeprod.alipay.com/record/detail/simpleDetail.htm?bizType=TRADE&bizInNo=<支付宝交易号>` and capture the Alipay detail page. Do not use amount/date matching in Alipay as the primary method.

## Taobao Order-Detail Browser Capture

Use a page-coordinate CDP clip for Taobao order details in VS Code browser. Do not rely on Playwright element screenshots, `page.screenshot({ clip })`, or temporary DOM repositioning unless the result has been visually revalidated; those paths have produced stale-layout crops, scrollbars, and clipped panels in the VS Code browser.

Before sampling any bounding box, set the viewport and then reload the detail page. Taobao does not reliably recompute this layout after a resize alone, so stale DOM rects can disagree with the rendered screenshot. After reload, wait for `.logo--zP4dbtIP`, `#mainContentContainer`, and `#rightMainContentContainer`, reset scroll to `(0, 0)`, and verify the visible text contains `交易成功`, the order number, `实付款`, `支付方式`, `支付宝支付`, and `支付宝交易号`.

For the print-focused screenshot, capture from the page left edge to the right edge of `#rightMainContentContainer`, and from the top of `.logo--zP4dbtIP` to the bottom of `#rightMainContentContainer`. This preserves the title, status, paid amount, order number, payment method, Alipay trade number, and the visible item rows while avoiding the large right-bottom whitespace produced by the full `#mainContentContainer` height.

```javascript
await page.setViewportSize({ width: 1600, height: 1200 });
await page.reload({ waitUntil: 'domcontentloaded' });
await page.waitForSelector('.logo--zP4dbtIP');
await page.waitForSelector('#rightMainContentContainer');
await page.evaluate(() => {
	const root = document.scrollingElement || document.documentElement;
	root.scrollTo({ left: 0, top: 0, behavior: 'instant' });
	root.scrollLeft = 0;
	root.scrollTop = 0;
	document.documentElement.scrollLeft = 0;
	document.documentElement.scrollTop = 0;
	document.body.scrollLeft = 0;
	document.body.scrollTop = 0;
});

const clip = await page.evaluate(() => {
	const logo = document.querySelector('.logo--zP4dbtIP');
	const right = document.querySelector('#rightMainContentContainer');
	const logoRect = logo.getBoundingClientRect();
	const rightRect = right.getBoundingClientRect();
	const top = Math.floor(logoRect.top + scrollY);
	return {
		x: 0,
		y: top,
		width: Math.ceil(rightRect.right + scrollX),
		height: Math.ceil(rightRect.bottom + scrollY) - top,
		scale: 1,
	};
});

const client = await page.context().newCDPSession(page);
const result = await client.send('Page.captureScreenshot', {
	format: 'png',
	fromSurface: true,
	captureBeyondViewport: true,
	clip,
});
```

If a complete left-column order screenshot is required instead of a print-focused one, use `#mainContentContainer` as the bottom boundary and accept that the shorter right column leaves white space below it. Do not mix the two modes within a batch without naming or recording the mode.

## Alipay Payment Skills Boundary

Treat `alipay/payment-skills` as an optional collaborator for live payment flows, not as a historical bill or receipt exporter.

Use the dedicated Alipay payment skills only when the reimbursement task includes a live Alipay cashier link, an HTTP 402 Payment Required response, or a user request to open/authorize Alipay agent payment. After that workflow finishes, return to this skill to record the order in the manifest, request or file the Alipay payment-record screenshot, and prepare the reimbursement workbook.

Do not install or run `alipay-bot` merely to fetch Taobao orders, historical Alipay bills, receipts, or transaction screenshots. The current reimbursement evidence source remains the user's edited Taobao export plus screenshots/PDFs that the user captures or supplies.

When an Alipay payment skill is used, follow that skill's own wallet authorization, URL preservation, MEDIA handling, and user-consent rules exactly. Do not merge those CLI instructions into this skill's scripts.

## Scripts

This skill is a uv project. Keep the shared virtual environment in this skill folder and do not create per-batch environments under reimbursement folders.

Set up or refresh the environment from the skill root:

```powershell
uv sync
```

Use:

```powershell
uv run python scripts\build_taobao_normal_reimbursement.py --folder "<batch-folder>"
```

If an interactive shell needs the environment activated:

```powershell
.\.venv\Scripts\Activate.ps1
```

Useful options:

- `--out-dir <dir>` writes artifacts outside the batch folder.
- `--template <xlsx>` uses an explicit previous normal reimbursement workbook.
- `--submission-date YYYY-MM-DD` controls the signature date and output filename.
- `--include-status 交易成功` keeps the default status filter.

Set applicant, bank, account, and leader values with `--name`, `--bank`, `--account`, and `--leader`.

For dependency updates, prefer `uv add <package>` for runtime dependencies and `uv add --dev <package>` for validation, testing, or frontend build helpers. Commit or keep `pyproject.toml` and `uv.lock` together.

After building the manifest and reimbursement workbook, prepare evidence folders and checklists:

```powershell
uv run python scripts\prepare_taobao_evidence.py --folder "<batch-folder>"
```

This also refreshes `<batch-folder>\generated\print-flat\taobao`, a flat print folder containing sequential links to each Taobao order-detail screenshot and Alipay payment screenshot. Use it for select-all printing; keep the per-order evidence files as the source of truth.

After browser automation captures Taobao order-detail pages and extracts Alipay trade numbers, merge the capture results into the manifest:

```powershell
uv run python scripts\merge_taobao_capture_results.py --folder "<batch-folder>"
uv run python scripts\prepare_taobao_evidence.py --folder "<batch-folder>"
```

After browser automation saves Alipay raw screenshots, normalize them through the fixed preset before running evidence validation:

```powershell
uv run python scripts\normalize_alipay_payment_screenshots.py --folder "<batch-folder>" --start <first> --end <last> --contact-sheet
uv run python scripts\prepare_taobao_evidence.py --folder "<batch-folder>"
```

## Current Limits

Travel reimbursement, Meituan/manual evidence ingestion, and drag-and-drop local frontend are planned workflow layers. Keep them in the convention document until implemented.
