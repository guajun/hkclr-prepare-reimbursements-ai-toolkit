# HKCLR Prepare Reimbursements AI Toolkit

Codex skill and scripts for preparing reimbursement batches from edited Taobao order exports, HKCLR travel reimbursement workbooks, and manually collected evidence.

The current workflow reads a dated reimbursement folder, applies the convention that a blank order number marks an order as not reimbursable, groups multi-SKU Taobao orders by merged Excel cells, and generates:

- `reimbursement-manifest.json`
- `reimbursement-review.xlsx`
- `報銷清單_Reimbursement list <name> <date>.xlsx`
- `travel-reimbursement-manifest.json`
- `差旅報銷清單_行程資料列表Reimbursement for travel expenses - <name> <date>.xlsx`
- `reimbursement-state.sqlite3`
- `reimbursement-state.snapshot.json`

## Layout

- `prepare-reimbursements/` contains the Codex skill.
- `prepare-reimbursements/scripts/` contains deterministic automation scripts.
- `prepare-reimbursements/references/` contains workflow conventions.

## Usage

From the skill directory:

```powershell
uv sync
uv run python scripts\build_taobao_normal_reimbursement.py `
  --folder "<path-to-reimbursement-batch>" `
  --name "<applicant-name>" `
  --bank "<bank-name>" `
  --account "<bank-account-number>" `
  --leader "<leader-name>"
```

The preferred edited Taobao export name is `订单数据-报销.xlsx`.

After collecting and validating screenshots, sync the batch into SQLite state:

```powershell
uv run python scripts\sync_reimbursement_state.py --folder "<path-to-reimbursement-batch>"
```

The SQLite database is the transition source of truth for orders, items, evidence files, validation results, and generated artifacts. The JSON snapshot is deliberately review-friendly; use it to inspect state changes without opening SQLite.

To rebuild generated outputs from SQLite without re-reading the edited Taobao export:

```powershell
uv run python scripts\compile_reimbursement_outputs.py --folder "<path-to-reimbursement-batch>"
```

The normal workbook's bottom-right date is derived from the latest reimbursed item date. The dated folder is only the batch creation date. `--submission-date YYYY-MM-DD` is an optional assertion and compilation fails if it differs from the derived date.

The final normal reimbursement workbook is written in the batch folder. Review workbooks, manifests, summaries, and print-flat caches remain under `generated`.

Normal reimbursement state keeps merchant purchase, payment debit, and reimbursement claim amounts separate. This matters when the merchant charges RMB but the payment provider debits HKD, such as a Jingdong order paid through Octopus. Ambiguous currency cases are collected in `generated\currency-confirmation-queue.json` and must be confirmed together at the end of the batch before the final workbook is compiled.

For travel reimbursement batches:

```powershell
uv run python scripts\sync_travel_reimbursement_state.py `
  --folder "<path-to-reimbursement-batch>"

uv run python scripts\compile_travel_reimbursement_outputs.py `
  --folder "<path-to-reimbursement-batch>" `
  --submission-date YYYY-MM-DD
```

The travel workflow parses `差旅報銷清單_行程資料列表Reimbursement for travel expenses*.xlsx`, the `差旅` evidence folder, and optional `差旅.docx` image bundle into SQLite. The compiler then regenerates the final travel workbook in the batch folder beside the normal reimbursement workbook; `travel-evidence-summary.json` remains under `generated`.

If validation reports bad screenshots, quarantine them out of active evidence folders:

```powershell
uv run python scripts\quarantine_invalid_evidence.py --folder "<path-to-reimbursement-batch>"
uv run python scripts\quarantine_invalid_evidence.py --folder "<path-to-reimbursement-batch>" --apply
```

The first command is a dry run. The second moves only screenshots that already have validation warnings.

## OCR-first evidence review

The toolkit calls a separately maintained local RapidOCR project through its versioned CLI. It builds canonical jobs, caches results, compares extracted candidates with known facts, and reports focused review reasons. It does not install RapidOCR or import its Python package.

Set `HKCLR_RAPIDOCR_PROJECT` to the external project's root directory. Do not commit an absolute local path to this repository.

`HKCLR_RAPIDOCR_PROJECT` is the only discovery mechanism. Windows User configuration is read when absent from the current process. Use an already provisioned external environment, then start reviewing as soon as evidence arrives:

```powershell
uv run python prepare-reimbursements/scripts/review_evidence_ocr.py --folder "<batch-folder>"
```

Read `generated/ocr/ocr-review.json` before opening images. The wrapper validates job-manifest v1 and output v2, uses per-run output directories, bounded subprocess calls and locks, and reuses valid cached results. `--dry-run` checks transport only. XML/readable PDFs use text extraction directly. New unclassified screenshots can use generic OCR to obtain text for classification.

Candidate metadata is saved separately in SQLite OCR tables; original evidence, claim values and validity are untouched. Failures are reported before visual fallback, and explicit OCR requests must not silently turn into full-batch image inspection. Text matches are not automatic reimbursement acceptance. Full text/boxes stay in private sidecars. Review images only for stated unresolved fields or layout checks; never invoke quarantine from OCR output.

See [the review workflow](prepare-reimbursements/references/ocr-first-review.md) for overrides, review routes and local configuration, and [the bridge contract](prepare-reimbursements/references/local-ocr-bridge.md) for schema details and acceptance checks.

## Known Issues

### Codex In-App Browser Screenshots

Do not use Codex Desktop's in-app browser screenshot API for final Alipay payment-record evidence on Windows until the upstream screenshot/viewport bug is resolved.

Observed failure: forcing an in-app browser viewport such as `1920x1080` can produce incorrectly scaled DOM metrics and oversized 2x2 tiled screenshots. This repository now treats those captures as invalid rather than cropping them into final evidence.

Tracking:

- Toolkit issue: https://github.com/guajun/hkclr-prepare-reimbursements-ai-toolkit/issues/1
- Upstream Codex issue: https://github.com/openai/codex/issues/31693

Recommended workaround: use a real Chrome/Edge/Chromium browser session with a persistent user profile. The user should log in or scan Alipay once in that browser; automation should reuse the visible session without reading cookies, localStorage, sessionStorage, password stores, or browser profile secrets.

## Privacy

Do not commit real reimbursement exports, screenshots, bank details, or generated reimbursement workbooks to this repository.
