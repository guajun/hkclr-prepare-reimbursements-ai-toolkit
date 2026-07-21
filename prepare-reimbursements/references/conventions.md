# Reimbursement Conventions

## Folder Shape

Batch folders live under:

`<reimbursement-root>\YYYY年M月D日`

Normal reimbursement batches may contain Taobao exports, evidence screenshots, PDFs, and one output workbook named like:

`報銷清單_Reimbursement list <name> YYYY-MM-DD.xlsx`

Generated print folders live under:

`<batch-folder>\generated\print-flat\<source>`

`generated\print-flat\all` is the complete DB-compiled print set across Taobao, hqchip, Meituan, Jingdong, and other supported order sources. `generated\print-flat\taobao` remains the Taobao-only capture workflow output.

For Taobao, `generated\print-flat\taobao` should contain sequential symlinks or hardlinks to each order-detail screenshot and payment-record screenshot. This folder exists only for bulk select-all printing; the per-order evidence files remain the source of truth.

Travel reimbursement batches may contain:

- `差旅報銷清單_行程資料列表Reimbursement for travel expenses - <name>.xlsx`
- a `差旅` evidence folder or `差旅.docx` image bundle
- optionally a `物品` folder for normal reimbursement evidence in the same batch

## Taobao Export Rule

The user edits the Taobao order export before automation.

- Use the Taobao export's merged cells to identify order boundaries. The order number column (`A`) is the primary group boundary; Taobao also tends to merge order-level fields such as order time, status, shop, paid amount, and shipping over the same row span.
- If the top-left order-number cell for an order group has no order number, the whole merged group is not reimbursable.
- Continuation rows inside a valid multi-item merged group normally have blank order-number cells when read by automation and must remain attached to that group.
- Do not infer multi-SKU groups only by scanning blank rows or forward-filling values. Use merged row spans first, then fall back to single-row groups only for exports without merges.
- Default status filter: only `交易成功` is reimbursable. Orders with an order number but another status are skipped and reported.

Preferred edited export filename:

`订单数据-报销.xlsx`

Original export backup filename:

`订单数据-原始.xlsx`

## Normal Reimbursement Workbook

Fill the first worksheet:

- `B3`: applicant name, supplied by CLI option or environment-specific wrapper
- `B4`: bank, supplied by CLI option or environment-specific wrapper
- `B5`: bank account number, supplied by CLI option or environment-specific wrapper
- `B6`: leader, supplied by CLI option or environment-specific wrapper
- rows starting at row 8:
  - `A`: sequential number
  - `B`: order date
  - `C`: short item label
  - `D`: amount in HKD, usually blank for Taobao
  - `E`: amount in RMB
  - `F`: other currencies, usually blank
  - `G`: document type
  - `H`: reason for missing receipt/invoice

Default Taobao document fields:

- Document type: `淘寶截圖加付款紀錄 Taobao capture screen & payment record`
- Missing receipt reason: `商家未提供`

All workbook date cells must contain real Excel date values, not preformatted date strings. Use the display format `dd/mm/yyyy` for normal reimbursement item dates and the signature date. Keep the normal reimbursement date column at least 14 Excel character units wide so valid dates do not render as `########`. This keeps sorting, filtering, and date arithmetic reliable across Excel locales.

The normal workbook does not embed screenshots. Evidence lives as separate screenshots/PDFs beside the workbook or in typed folders.

The `Document type` column must use the historical template's values exactly:

- `實體 Hard copy receipt/Invoice`
- `電子發票 Soft copy invoice`
- `淘寶截圖加付款紀錄 Taobao capture screen & payment record`
- `沒有 Missing`

Do not create merchant-specific values. Taobao, Meituan, Jingdong, and any other workflow based on merchant screenshots plus a payment record all use the Taobao capture option. Vendor-issued electronic invoices and receipts use the soft-copy invoice option.

Normal reimbursement rows are ordered by source category before date. Keep the established category order: Taobao, hqchip, Meituan, Jingdong, GitHub, Aliyun, then unknown sources. Within each category, sort dates ascending and use the original source index only as a stable tie-breaker. Output row numbers are regenerated sequentially; evidence folder indices remain unchanged.

Set `Reason for missing receipt/invoice` to `商家未提供` by default only for `淘寶截圖加付款紀錄 Taobao capture screen & payment record`. For hard-copy receipts, soft-copy invoices, and missing-document rows, preserve an explicit reason if one exists but do not inherit the Taobao default.

The dated batch folder records when the batch was created; it does not determine the workbook's signature date. Set the bottom-right date in the normal reimbursement workbook to the latest reimbursed item date. The compiler derives this from order state. If `--submission-date` is supplied for compatibility, it must equal the derived latest date or compilation fails.

### Direct Vendor Documents

An official vendor invoice or receipt PDF can satisfy the full evidence requirement for one order. Record it as `invoice_pdf` or `receipt_pdf`; do not also require an order-detail screenshot and payment-record screenshot. Use the total and currency printed on the document as the claim amount and currency, set document type to `電子發票 Soft copy invoice`, and leave the missing receipt/invoice reason blank.

The DB compiler omits valid direct-document orders from the Taobao capture queue, marks them complete in the evidence summary, and copies the PDF into `generated\print-flat\all`. Supplemental machine-readable invoice files such as XML may also be stored, but the printable PDF remains the primary evidence.

## Currency Resolution

Store purchase, payment, and claim values independently:

- `amount_rmb`: merchant purchase amount in RMB. Preserve it even when the actual payment uses another currency.
- `payment_amount` and `payment_currency`: amount actually debited by the wallet, card, or payment provider.
- `claim_amount` and `claim_currency`: amount and currency written into the reimbursement workbook.
- `currency_review_status`: `resolved`, `confirmed`, or `needs_confirmation`.
- `currency_note`: evidence and reasoning used to resolve the currencies.

Apply these rules in order:

1. Prefer explicit currency labels on the merchant and payment records.
2. Treat the payment provider and known app/session context as supporting evidence. An Octopus payment is normally in HKD.
3. Do not infer a conflict from unequal numbers before checking whether they are different currencies or an FX conversion.
4. When a merchant charges RMB and the actual debit is an Octopus HKD payment, preserve the RMB purchase amount and use the actual HKD debit as the claim amount.
5. Do not identify Octopus from generic colors or layout alone. A merchant row, timestamp, red negative amount, and category icon may support the inference only when Octopus branding or known app context is also present.
6. If the evidence is still ambiguous, set `currency_review_status` to `needs_confirmation`, add a concise `currency_note`, and continue reviewing the rest of the batch.
7. At the end of the batch, present all pending rows together. Do not ask the user separately for each order.

The normal compiler writes `generated\currency-confirmation-queue.json` when pending rows exist and blocks final workbook generation until they are changed to `resolved` or `confirmed`. It also queues a nominally `resolved` row when only one payment field is present or when its claim amount/currency does not match the recorded debit. Claim amounts are written to column `D` for HKD, column `E` for RMB/CNY, and column `F` for other currencies.

## Manifest Object

Each parsed order should become one manifest object:

- `source`: `taobao`
- `order_no`
- `taobao_order_detail_url`
- `alipay_trade_no`
- `alipay_detail_url`
- `date`
- `shop`
- `status`
- `item_label`
- `amount_rmb`
- `item_count`
- `items`
- `document_type`
- `missing_receipt_reason`
- `evidence_required`

Evidence required for Taobao normal reimbursement:

- `taobao_order_detail_screenshot`
- `payment_record_screenshot`

## SQLite State Database

`generated\reimbursement-state.sqlite3` is the transition source state for a batch. Normal Taobao state is rebuilt or refreshed from the current manifest and evidence files by:

```powershell
uv run python scripts\sync_reimbursement_state.py --folder "<batch-folder>"
```

Schema v3 represents:

- `batches`: batch folder, reimbursement type, source manifest/export, profile, and parsed summary.
- `orders`: normalized reimbursable orders with source identifiers plus separate purchase, payment, and claim currency fields and batch confirmation state.
- `order_items`: SKU/item rows attached to each order.
- `evidence_files`: expected and actual screenshot files, file metadata, hashes, capture method, validation status, and warnings.
- `travel_expense_rows`: travel workbook expense rows by source worksheet row, category, currency, and amount.
- `travel_itinerary_rows`: travel itinerary rows with trip date, origin, destination, and purpose.
- `travel_evidence_files`: manually supplied travel screenshots and docx image bundles with file metadata and validation status.
- `validation_results`: per-order validation state from evidence preparation scripts.
- `generated_artifacts`: manifests, workbooks, checklists, contact sheets, print folders, and other compiled outputs.

`generated\reimbursement-state.snapshot.json` is the canonical review format for humans and agents. It should be stable enough to diff conceptually: orders are sorted by reimbursement index, item rows by item index, and evidence by kind. The snapshot may contain hashes and local relative paths; do not commit real batch snapshots to the public repo.

The intended compiler model is:

- source state: SQLite plus source screenshots/PDFs
- compiled outputs: XLSX reimbursement workbooks, evidence checklists, capture queues, print-flat folders, contact sheets, and summary JSON
- compatibility input during transition: `generated\reimbursement-manifest.json`

After SQLite state exists, rebuild normal Taobao outputs without re-reading the edited Taobao export:

```powershell
uv run python scripts\compile_reimbursement_outputs.py --folder "<batch-folder>"
```

The compiler reads orders, items, evidence paths, validation status, and artifact state from SQLite. It may still read source screenshot files to create print-flat links and calculate artifact hashes. It should not parse `订单数据*.xlsx`.

The final normal reimbursement workbook is written directly under the batch folder. Manifests, review workbooks, checklists, summaries, and print-flat caches remain under `generated`.

For travel reimbursement, sync the source travel workbook and evidence files into the same database:

```powershell
uv run python scripts\sync_travel_reimbursement_state.py --folder "<batch-folder>"
```

This script reads the workbook through a temporary copy first, because OneDrive reparse/placeholder files may fail when opened directly by `openpyxl`. It parses:

- sheet `差旅報銷清單`, rows 10 through the row before `Total:`
- sheet `行程資料列表`, rows 2 onward
- `差旅` image files, sorted by filename
- `差旅.docx` as an optional image bundle record

After SQLite travel state exists, rebuild the generated travel workbook without treating the source workbook as state:

```powershell
uv run python scripts\compile_travel_reimbursement_outputs.py --folder "<batch-folder>" --submission-date YYYY-MM-DD
```

The compiler uses the original travel workbook as a formatting template when available, fills profile cells, expense rows, formulas, and itinerary rows from SQLite, and writes the final travel workbook directly under the batch folder beside the normal reimbursement workbook. Review summaries such as `generated\travel-evidence-summary.json` remain under `generated`.

## Evidence Quarantine

Final screenshots with validation warnings must not count as complete evidence. Use:

```powershell
uv run python scripts\quarantine_invalid_evidence.py --folder "<batch-folder>"
uv run python scripts\quarantine_invalid_evidence.py --folder "<batch-folder>" --apply
```

The first command is a dry run and writes `generated\evidence-quarantine-report.json`. The `--apply` command moves warned screenshots to `generated\quarantine\evidence\<timestamp>` while leaving missing evidence untouched. After quarantine, rerun evidence preparation and state sync so the summary and SQLite validation state show the screenshot as missing instead of accepted.

Taobao order-detail screenshot acceptance:

- In VS Code browser, use a CDP `Page.captureScreenshot` page-coordinate clip after setting the viewport and reloading the page. Resizing without reload leaves Taobao layout stale and can make DOM rects disagree with the rendered page.
- The print-focused capture clips from page `x = 0`, from the top of `.logo--zP4dbtIP`, to the right and bottom edges of `#rightMainContentContainer`. It must show order status, shop or item details, paid amount, order number, payment method, and `支付宝交易号` without browser scrollbars or recommendation blocks.
- For a complete left-column screenshot, `#mainContentContainer` may be used as the bottom boundary; the resulting right-bottom white space is expected because the left order column is taller than the right payment/order-info column.
- Do not accept captures with repeated vertical chunks, clipped order/payment information, stale resize layout, browser scrollbars, recommendation blocks, or large right/bottom whitespace in print-focused mode.

Payment-record screenshot acceptance:

- The saved Alipay detail screenshot must show `交易成功`, product or counterparty, `流水号`, time, `订单金额`, `= 实付金额`, the final paid amount, and the payment method.
- Browser screenshots may be tiled or duplicated. Never accept these raw images as final evidence. Save raw images under `_raw_payment_screenshots`, then normalize them with `scripts/normalize_alipay_payment_screenshots.py`.
- Approved normalized Alipay final sizes are `820x777`, `911x777`, `1425x801`, `1521x633`, `1521x688`, and `1536x639`. Other dimensions must trigger review unless the preset script has been deliberately updated from a newly inspected good sample.
- Treat very narrow desktop Alipay screenshots as suspect; rerun `scripts/prepare_taobao_evidence.py` and review any `payment_screenshot_warnings`.

## Alipay Screenshot Preset

Use a fixed browser-rendering and screenshot preset for Alipay detail pages.

Browser capture preset:

- Use one dedicated in-app browser tab after the user has logged in to Alipay. Keep that tab alive for the whole batch; do not close, finalize, or replace it between orders unless Alipay itself expires the session.
- Open the exact `alipay_detail_url`; do not search the Alipay bill list when an `alipay_trade_no` exists.
- Before saving the first raw screenshot, verify the page DOM contains `交易成功`, `流水号`, `订单金额`, and `= 实付金额`.
- Use the viewport screenshot call (`tab.screenshot({})`) for raw captures. Do not mix `fullPage`, clipped screenshots, viewport resizing, or different tabs within the same batch unless recalibrating from a new inspected sample.
- Raw screenshots from the in-app browser may be JPEG bytes even when saved with a `.png` filename; normalize by opening the image bytes with Pillow rather than assuming the extension.
- Do not treat the in-app browser viewport override as equivalent to opening a real 1920x1080 desktop browser window. On the current Windows/Codex setup, forcing `1920x1080` produced an abnormal `4276x2404` 2x2 tiled screenshot. This is a screenshot-backend failure, not a valid preset. Stop the batch if this shape appears.
- Save raw files as `<NN>_<order_no>_payment_record.png` inside each order folder's `_raw_payment_screenshots` directory.
- Normalize raw files with:

```powershell
uv run python scripts\normalize_alipay_payment_screenshots.py --folder "<batch-folder>" --start <first> --end <last> --contact-sheet
```

Known-good raw-to-final presets:

- Raw about `2400x1350` from VS Code browser with a `1920x1080` viewport at 1.25 device scale -> crop top-left `1425x801`.
- Raw about `1521x633`, `1521x688`, or `1536x639` from a visible Chrome window through the Codex Chrome extension -> keep/copy as a single-frame Chrome screenshot.
- Raw about `2851x1603` -> crop top-left `1425x801`.
- Raw about `1822x1554` -> crop top-left `911x777`.
- Raw about `1485x1554` -> crop top-left `820x777`.

If the first raw screenshot does not match one of these raw sizes, stop before batch capture. Inspect the raw screenshot, create a new deliberate preset from a known-good final image, update the normalization script, then continue.

For new batches, prefer a real browser capture engine when the user needs normal full-window screenshots. A real browser engine means a visible Chrome/Edge/Chromium session with a persistent user profile, a genuine `1920x1080` viewport/window, and browser-native screenshot output that is not tiled. The human user scans or completes Alipay login once in that browser; the agent reuses the same live session for the batch without reading cookies or session stores.

## Taobao To Alipay Evidence Route

Use the Taobao order detail page as the source of the Alipay transaction id:

1. Open `taobao_order_detail_url` or `https://buyertrade.taobao.com/trade/detail/trade_item_detail.htm?biz_order_id=<order_no>`.
2. Save the Taobao order-detail screenshot.
3. Extract the value labelled `支付宝交易号`; store it as `alipay_trade_no`.
4. Build `alipay_detail_url` as `https://consumeprod.alipay.com/record/detail/simpleDetail.htm?bizType=TRADE&bizInNo=<alipay_trade_no>`.
5. Open `alipay_detail_url` directly and save the raw Alipay payment-record screenshot.
6. Normalize the raw screenshot through the Alipay screenshot preset and inspect the contact sheet before accepting final evidence.

Only use Alipay bill-list amount/date filtering as a fallback when the Taobao detail page does not expose `支付宝交易号`. Do not rely on amount-only matching when a transaction id is available.

## Alipay Payment Skills Boundary

`alipay/payment-skills` may support live payment flows that appear during reimbursement preparation, such as Alipay cashier links or HTTP 402 Payment Required responses. These skills do not replace Taobao exports, Alipay app screenshots, or other evidence files for historical reimbursement packets.

If a live payment flow is completed while preparing a reimbursement packet, keep the normal evidence requirement: capture or request the Alipay payment-record screenshot that shows date/time, merchant or order reference, and amount. Do not read hidden CLI state or local wallet files to infer reimbursement evidence.

Do not use Alipay payment skills to automate login, 2FA, app-only history browsing, wallet binding, or payment unless the user explicitly requested that payment workflow and remains actively involved.

## Travel Reimbursement

Travel reimbursement uses a separate workbook with sheets `差旅報銷清單` and `行程資料列表`.

The first sheet has profile fields at `A3`, `I3`, `I5`, and `A6`. Data rows start at row 10 and end before the row whose column A value starts with `Total:`. The current HKCLR template uses:

- `A`: date
- `B`: destination
- `C:E`: Flight/Vessel/Train/Car amounts in HKD/RMB/Other
- `F:H`: Hotel amounts in HKD/RMB/Other
- `I:K`: Conference Fee amounts in HKD/RMB/Other
- `L:N`: Meal amounts in HKD/RMB/Other
- `O:Q`: Misc. amounts in HKD/RMB/Other

The itinerary sheet starts at row 2 and uses `A:E` for index, date, origin, destination, and purpose.

Travel claim dates in column `A` and itinerary dates in column `B` must contain real Excel date values with the display format `dd/mm/yyyy`. Keep both date columns at least 14 Excel character units wide. Do not write `strftime()` output such as `13/05/2026` into those cells as text.

The human user remains responsible for confirming the trip purpose, route, and whether each payment record is enough. The agent may parse, normalize into SQLite, and compile the workbook, but should not infer a route or business purpose that is not already present in the workbook or supplied by the user. Octopus-style travel evidence may only need the payment/travel record screenshot.

## Planned Frontend

Add a local drag-and-drop frontend later. It should let the user choose a batch folder or upload an edited Taobao export, preview parsed orders, edit `item_label`, run the build script, and open the output folder.
