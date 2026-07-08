# Reimbursement Conventions

## Folder Shape

Batch folders live under:

`<reimbursement-root>\YYYY年M月D日`

Normal reimbursement batches may contain Taobao exports, evidence screenshots, PDFs, and one output workbook named like:

`報銷清單_Reimbursement list <name> YYYY-MM-DD.xlsx`

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

Payment-record screenshot acceptance:

- The saved Alipay detail screenshot must show `交易成功`, product or counterparty, `流水号`, time, `订单金额`, `= 实付金额`, the final paid amount, and the payment method.
- Browser screenshots may be tiled or duplicated. Never accept these raw images as final evidence. Save raw images under `_raw_payment_screenshots`, then normalize them with `scripts/normalize_alipay_payment_screenshots.py`.
- Approved normalized Alipay final sizes are `820x777`, `911x777`, and `1425x801`. Other dimensions must trigger review unless the preset script has been deliberately updated from a newly inspected good sample.
- Treat very narrow desktop Alipay screenshots as suspect; rerun `scripts/prepare_taobao_evidence.py` and review any `payment_screenshot_warnings`.

## Alipay Screenshot Preset

Use a fixed browser-rendering and screenshot preset for Alipay detail pages.

Browser capture preset:

- Open a fresh dedicated in-app browser tab after the user has logged in to Alipay.
- Open the exact `alipay_detail_url`; do not search the Alipay bill list when an `alipay_trade_no` exists.
- Before saving the first raw screenshot, verify the page DOM contains `交易成功`, `流水号`, `订单金额`, and `= 实付金额`.
- Use the viewport screenshot call (`tab.screenshot({})`) for raw captures. Do not mix `fullPage`, clipped screenshots, viewport resizing, or different tabs within the same batch unless recalibrating from a new inspected sample.
- Save raw files as `<NN>_<order_no>_payment_record.png` inside each order folder's `_raw_payment_screenshots` directory.
- Normalize raw files with:

```powershell
uv run python scripts\normalize_alipay_payment_screenshots.py --folder "<batch-folder>" --start <first> --end <last> --contact-sheet
```

Known-good raw-to-final presets:

- Raw about `2851x1603` -> crop top-left `1425x801`.
- Raw about `1822x1554` -> crop top-left `911x777`.
- Raw about `1485x1554` -> crop top-left `820x777`.

If the first raw screenshot does not match one of these raw sizes, stop before batch capture. Inspect the raw screenshot, create a new deliberate preset from a known-good final image, update the normalization script, then continue.

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
