# HKCLR Prepare Reimbursements AI Toolkit

Codex skill and scripts for preparing reimbursement batches from edited Taobao order exports, HKCLR travel reimbursement workbooks, and manually collected evidence.

The Taobao importer reads an edited order export, treats a blank top-left order number in a merged order group as an exclusion, and keeps every SKU row inside valid groups. By default, only orders with status `交易成功` are included. Normal and travel workflows share SQLite batch state; compilers rebuild workbooks and evidence summaries from that state plus the source evidence files.

## Scope

- Taobao: import edited exports, prepare evidence folders/checklists, merge browser capture results, normalize Alipay screenshots, and generate reimbursement workbooks.
- Other normal expenses: compile orders and evidence already recorded in SQLite, including official vendor invoice/receipt PDFs. General non-Taobao ingestion and automated capture are not implemented.
- Travel: import an existing HKCLR travel workbook, itinerary, and manually collected evidence, then compile a fresh workbook.
- OCR: review image text through a separately provisioned local RapidOCR project. Candidates are advisory and do not approve evidence or change claims.

Browser capture is an agent-guided workflow described in the [skill](prepare-reimbursements/SKILL.md) and [conventions](prepare-reimbursements/references/conventions.md), not a browser automation service bundled with these scripts. A drag-and-drop frontend is not implemented.

## Layout

- `prepare-reimbursements/` contains the Codex skill.
- `prepare-reimbursements/scripts/` contains deterministic automation scripts.
- `prepare-reimbursements/references/` contains workflow conventions.
- `prepare-reimbursements/prepare_reimbursements/` contains shared SQLite and OCR modules.
- `prepare-reimbursements/tests/` contains OCR job, runner, and review tests.

## Usage

Prerequisites: Python 3.11 or newer and `uv`. From the repository root, enter the skill directory and install its locked dependencies. All subsequent commands below run from this directory, with one shared environment for all batches:

```powershell
Set-Location prepare-reimbursements
uv sync --locked
```

For agent use, read [SKILL.md](prepare-reimbursements/SKILL.md) and the [workflow conventions](prepare-reimbursements/references/conventions.md) before processing a batch.

### First Taobao import

Place the edited export in the private batch folder, preferably named `订单数据-报销.xlsx`. Supply your own applicant and bank details explicitly; the script has existing personal defaults and does not prompt for them.

```powershell
uv run python scripts\build_taobao_normal_reimbursement.py `
  --folder "<path-to-reimbursement-batch>" `
  --name "<applicant-name>" `
  --bank "<bank-name>" `
  --account "<bank-account-number>" `
  --leader "<leader-name>"
```

This bootstraps `generated/reimbursement-manifest.json`, `generated/reimbursement-review.xlsx`, and an initial reimbursement workbook **under `generated`**. Use `--orders <xlsx>` for a different export or `--template <xlsx>` for an explicit previous normal workbook; otherwise a discovered template or built-in layout is used. This initial workbook does not certify evidence completeness.

### Evidence and SQLite state

Prepare the per-order evidence folders, checklist, capture queue, and Taobao print folder:

```powershell
uv run python scripts\prepare_taobao_evidence.py --folder "<path-to-reimbursement-batch>"
```

Collect evidence following the capture presets in the conventions. Run OCR review as soon as source evidence arrives (see below). After saving final screenshots, rerun evidence preparation and sync the manifest and current evidence into SQLite:

```powershell
uv run python scripts\prepare_taobao_evidence.py --folder "<path-to-reimbursement-batch>"
uv run python scripts\sync_reimbursement_state.py --folder "<path-to-reimbursement-batch>"
```

The state database and review-friendly snapshot are `generated/reimbursement-state.sqlite3` and `generated/reimbursement-state.snapshot.json`. SQLite is the transition state layer for orders, items, evidence metadata, validation results, and generated artifacts. Sync imports the compatibility manifest and scans evidence; it does not reparse the edited export.

### Compile normal reimbursement outputs

Once the database exists, rebuild from SQLite and source evidence files without re-reading the edited Taobao export:

```powershell
uv run python scripts\compile_reimbursement_outputs.py --folder "<path-to-reimbursement-batch>"
```

The normal workbook's bottom-right date is derived from the latest reimbursed item date. The dated folder is only the batch creation date. `--submission-date YYYY-MM-DD` is an optional assertion and compilation fails if it differs from the derived date.

The compiler writes the final normal reimbursement workbook in the batch folder. Review workbooks, manifests, summaries, and print folders remain under `generated` (see output locations below). `--workbook-output <xlsx>` overrides the final workbook path; `--out-dir <dir>` overrides supporting output locations.

Normal rows are sorted by source category, then ascending date. The four historical document types are preserved. An official vendor PDF recorded as `invoice_pdf` or `receipt_pdf` can satisfy the order's evidence requirement without additional order/payment screenshots; use `電子發票 Soft copy invoice` and leave the missing-receipt reason blank.

Normal reimbursement state keeps merchant purchase, payment debit, and reimbursement claim amounts separate. This matters when the merchant charges RMB but the payment provider debits HKD, such as a Jingdong order paid through Octopus. Ambiguous currency cases are collected in `generated\currency-confirmation-queue.json` and must be confirmed together at the end of the batch before the final workbook is compiled.

### Travel reimbursement

```powershell
uv run python scripts\sync_travel_reimbursement_state.py `
  --folder "<path-to-reimbursement-batch>"

uv run python scripts\compile_travel_reimbursement_outputs.py `
  --folder "<path-to-reimbursement-batch>" `
  --submission-date YYYY-MM-DD
```

The travel workflow parses `差旅報銷清單_行程資料列表Reimbursement for travel expenses*.xlsx`, the `差旅` evidence folder, and optional `差旅.docx` image bundle into SQLite. The compiler then regenerates the final travel workbook in the batch folder beside the normal reimbursement workbook; `travel-evidence-summary.json` remains under `generated`.

Use `--workbook <xlsx>` on sync to select an explicit input. The compiler uses the source workbook as a layout template when available. Travel `--submission-date` controls the output filename and defaults to the current date; it does not use the normal workflow's latest-item-date assertion. Trip purpose, route, and evidence sufficiency require human confirmation.

### Invalid screenshots

If validation reports bad screenshots, quarantine them out of active evidence folders:

```powershell
uv run python scripts\quarantine_invalid_evidence.py --folder "<path-to-reimbursement-batch>"
uv run python scripts\quarantine_invalid_evidence.py --folder "<path-to-reimbursement-batch>" --apply
```

The first command writes a dry-run report at `generated/evidence-quarantine-report.json`. Inspect its target list before running `--apply`, which moves screenshots flagged by the Taobao evidence validator to `generated/quarantine/evidence/<timestamp>`. Rerun evidence preparation and state sync afterwards. OCR review output is not a quarantine instruction.

## Default output locations

Paths below are relative to the private batch folder. Each command produces only its own workflow's outputs.

| Location | Contents / producer |
| --- | --- |
| `generated/reimbursement-manifest.json`, `generated/reimbursement-review.xlsx` | Initial Taobao import; later regenerated by normal compilation |
| `generated/報銷清單_Reimbursement list <name> <date>.xlsx` | Initial Taobao import workbook |
| `報銷清單_Reimbursement list <name> <date>.xlsx` | Final normal workbook from SQLite compilation |
| `差旅報銷清單_行程資料列表Reimbursement for travel expenses - <name> <date>.xlsx` | Final travel workbook from SQLite compilation |
| `generated/reimbursement-state.sqlite3`, `generated/reimbursement-state.snapshot.json` | Shared state and snapshot written by sync |
| `generated/taobao-evidence-checklist.xlsx`, `generated/taobao-evidence-capture-queue.md`, `generated/taobao-evidence-summary.json` | Evidence preparation or normal compilation; normal compilation covers all recorded sources despite these compatibility filenames |
| `generated/print-flat/taobao/` | Taobao-only print links from evidence preparation |
| `generated/print-flat/all/` | Complete normal print set across recorded sources from SQLite compilation |
| `generated/currency-confirmation-queue.json` | Pending currency decisions from normal compilation; a non-empty queue blocks final workbook generation |
| `generated/travel-reimbursement-manifest.json` | Travel sync |
| `generated/travel-evidence-summary.json` | Travel compilation |
| `generated/reimbursement-state-compile-summary.json`, `generated/travel-reimbursement-state-compile-summary.json` | Respective compiler summaries |
| `generated/ocr/ocr-review.json` | Compact OCR review candidates and reasons |

Compilers update SQLite artifact records but do not refresh the snapshot JSON themselves. See the skill for the sync and snapshot workflow.

## OCR-first evidence review

The toolkit calls a separately maintained local RapidOCR project through its versioned CLI. It builds canonical jobs, caches results, compares extracted candidates with known facts, and reports focused review reasons. It does not install RapidOCR or import its Python package.

Set `HKCLR_RAPIDOCR_PROJECT` to the external project's root directory. Do not commit an absolute local path to this repository.

`HKCLR_RAPIDOCR_PROJECT` is the only discovery mechanism. Windows User configuration is read when absent from the current process. Use an already provisioned external environment, then start reviewing as soon as evidence arrives:

```powershell
uv run python scripts\review_evidence_ocr.py --folder "<batch-folder>"
```

Review can start before a manifest or database exists: job discovery reads SQLite when available, falls back to manifests, and can discover source images directly. It excludes raw captures, generated copies, print copies, and quarantine.

Read `generated/ocr/ocr-review.json` before opening images. The wrapper validates job-manifest v1 and output v2, uses per-run output directories, bounded subprocess calls and locks, and reuses valid cached results. `--dry-run` checks transport only. Read XML/readable PDFs through their native text path rather than rasterizing them for this image OCR workflow. New unclassified screenshots can use generic OCR to obtain text for classification.

Use `--evidence-id <id>` to read one existing result's bounded text excerpt without inference, or `--no-persist` to omit SQLite candidate persistence. Missing external configuration/runtime is reported as unavailable; this command does not provision the OCR environment.

Candidate metadata is saved separately in SQLite OCR tables; original evidence, claim values and validity are untouched. Failures are reported before visual fallback, and explicit OCR requests must not silently turn into full-batch image inspection. Text matches are not automatic reimbursement acceptance. Full text/boxes stay in private sidecars. Review images only for stated unresolved fields or layout checks; never invoke quarantine from OCR output.

See [the review workflow](prepare-reimbursements/references/ocr-first-review.md) for overrides, review routes and local configuration, and [the bridge contract](prepare-reimbursements/references/local-ocr-bridge.md) for schema details and acceptance checks.

## Screenshot compatibility

### Codex In-App Browser Screenshots

The previously reported Windows DPI/viewport tiling bug is resolved in the versions documented by the issue verification. [Upstream issue #31693](https://github.com/openai/codex/issues/31693) closed on 2026-08-13; [toolkit issue #1](https://github.com/guajun/hkclr-prepare-reimbursements-ai-toolkit/issues/1#issuecomment-5490449211) closed on 2026-09-01 after a public-page smoke test with bundled Browser plugin `26.825.51511`. That test verified coherent viewport, full-page, and clipped captures; it was not a private Alipay evidence test.

Historical failure: forcing a viewport such as `1920x1080` produced incorrectly scaled DOM metrics and oversized 2x2 tiled screenshots, notably around `4276x2404`. The defensive rejection of known-bad captures remains in place. Calibrate and visually inspect the first payment screenshot for each new layout/preset before batch capture. Stop if tiling or abnormal scaling returns; do not crop a repeated tile into final evidence.

If the failure recurs, use a real Chrome/Edge/Chromium browser session with a persistent user profile. The user should log in or scan Alipay once in that browser; automation should reuse the visible session without reading cookies, localStorage, sessionStorage, password stores, or browser profile secrets.

## Privacy

Do not commit real reimbursement exports, screenshots, bank details, or generated reimbursement workbooks to this repository.
