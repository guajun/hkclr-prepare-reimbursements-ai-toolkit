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

## Travel Reimbursement

Travel reimbursement uses a separate workbook with sheets `差旅報銷清單` and `行程資料列表`.

The agent may fill dates, destinations, trip purpose, and transport amounts from a structured manifest. The human user remains responsible for confirming the trip purpose, route, and whether each payment record is enough. Octopus-style travel evidence may only need the payment/travel record screenshot.

## Planned Frontend

Add a local drag-and-drop frontend later. It should let the user choose a batch folder or upload an edited Taobao export, preview parsed orders, edit `item_label`, run the build script, and open the output folder.
