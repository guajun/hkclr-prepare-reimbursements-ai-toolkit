# HKCLR Prepare Reimbursements AI Toolkit

Codex skill and scripts for preparing reimbursement batches from edited Taobao order exports and manually collected evidence.

The current workflow reads a dated reimbursement folder, applies the convention that a blank order number marks an order as not reimbursable, groups multi-SKU Taobao orders by merged Excel cells, and generates:

- `reimbursement-manifest.json`
- `reimbursement-review.xlsx`
- `報銷清單_Reimbursement list <name> <date>.xlsx`
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

Use `--submission-date YYYY-MM-DD` when the workbook date should differ from today's date.

If validation reports bad screenshots, quarantine them out of active evidence folders:

```powershell
uv run python scripts\quarantine_invalid_evidence.py --folder "<path-to-reimbursement-batch>"
uv run python scripts\quarantine_invalid_evidence.py --folder "<path-to-reimbursement-batch>" --apply
```

The first command is a dry run. The second moves only screenshots that already have validation warnings.

## Optional Local OCR Bridge

The toolkit can optionally collaborate with a separately maintained local RapidOCR project. This is an experimental, machine-local bridge: this repository does not install RapidOCR, import its Python package, or write OCR results into SQLite.

Set `HKCLR_RAPIDOCR_PROJECT` to the external project's root directory. Do not commit an absolute local path to this repository.

For the current PowerShell session:

```powershell
$env:HKCLR_RAPIDOCR_PROJECT = "C:\path\to\hkclr-rapidocr-evaluation"
```

To persist it for the current Windows user and future shells:

```powershell
[Environment]::SetEnvironmentVariable(
  "HKCLR_RAPIDOCR_PROJECT",
  "C:\path\to\hkclr-rapidocr-evaluation",
  "User"
)
```

Run the external CLI through its own uv project and keep private OCR output inside the reimbursement batch:

```powershell
$batch = "C:\path\to\reimbursement-batch"
$ocrOutput = Join-Path $batch "generated\ocr\rapidocr"

uv run --project $env:HKCLR_RAPIDOCR_PROJECT hkclr-ocr doctor --initialize
uv run --project $env:HKCLR_RAPIDOCR_PROJECT hkclr-ocr scan `
  $batch `
  --output $ocrOutput `
  --profile auto
```

If the variable is unset, the project is unavailable, or OCR fails, continue with the existing image-review workflow. OCR results are advisory in this phase and must not change SQLite state or evidence validity. See `prepare-reimbursements/references/local-ocr-bridge.md` for the command and output contract.

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
