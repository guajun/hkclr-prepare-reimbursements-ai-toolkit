# Reimbursement Conventions

## Folder Shape

Batch folders live under:

`<reimbursement-root>\YYYY年M月D日`

Normal reimbursement batches may contain Taobao exports, evidence screenshots, PDFs, and one output workbook named like:

`報銷清單_Reimbursement list <name> YYYY-MM-DD.xlsx`

Generated print folders live under:

`<batch-folder>\generated\print-flat\<source>`

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

The normal workbook does not embed screenshots. Evidence lives as separate screenshots/PDFs beside the workbook or in typed folders.

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

`generated\reimbursement-state.sqlite3` is the transition source state for a batch. It is rebuilt or refreshed from the current manifest and evidence files by:

```powershell
uv run python scripts\sync_reimbursement_state.py --folder "<batch-folder>"
```

Schema v1 represents:

- `batches`: batch folder, reimbursement type, source manifest/export, profile, and parsed summary.
- `orders`: normalized reimbursable orders with Taobao and Alipay identifiers.
- `order_items`: SKU/item rows attached to each order.
- `evidence_files`: expected and actual screenshot files, file metadata, hashes, capture method, validation status, and warnings.
- `validation_results`: per-order validation state from evidence preparation scripts.
- `generated_artifacts`: manifests, workbooks, checklists, contact sheets, print folders, and other compiled outputs.

`generated\reimbursement-state.snapshot.json` is the canonical review format for humans and agents. It should be stable enough to diff conceptually: orders are sorted by reimbursement index, item rows by item index, and evidence by kind. The snapshot may contain hashes and local relative paths; do not commit real batch snapshots to the public repo.

The intended compiler model is:

- source state: SQLite plus source screenshots/PDFs
- compiled outputs: XLSX reimbursement workbooks, evidence checklists, capture queues, print-flat folders, contact sheets, and summary JSON
- compatibility input during transition: `generated\reimbursement-manifest.json`

After SQLite state exists, rebuild normal Taobao outputs without re-reading the edited Taobao export:

```powershell
uv run python scripts\compile_reimbursement_outputs.py --folder "<batch-folder>" --submission-date YYYY-MM-DD
```

The compiler reads orders, items, evidence paths, validation status, and artifact state from SQLite. It may still read source screenshot files to create print-flat links and calculate artifact hashes. It should not parse `订单数据*.xlsx`.

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

The agent may fill dates, destinations, trip purpose, and transport amounts from a structured manifest. The human user remains responsible for confirming the trip purpose, route, and whether each payment record is enough. Octopus-style travel evidence may only need the payment/travel record screenshot.

## Planned Frontend

Add a local drag-and-drop frontend later. It should let the user choose a batch folder or upload an edited Taobao export, preview parsed orders, edit `item_label`, run the build script, and open the output folder.
