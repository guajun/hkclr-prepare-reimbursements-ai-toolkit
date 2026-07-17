# Optional Local OCR Bridge

## Scope

This bridge lets the reimbursement workflow call a separately maintained RapidOCR evaluation project without adding OCR code or dependencies to this toolkit.

The bridge is intentionally advisory. The existing toolkit remains the owner of manifests, image-quality validation, SQLite state, and compiled outputs.

The screenshot-quarantine workflow is a legacy workaround for malformed Codex/VS Code in-app browser captures, including tiled or blank images. It is not an OCR lifecycle stage and is not an integration target for this bridge.

## Machine-Local Configuration

Set one environment variable to the external uv project root:

`HKCLR_RAPIDOCR_PROJECT=<absolute path to the RapidOCR project>`

The directory must contain `pyproject.toml` and expose the `hkclr-ocr` console command. Keep the absolute path in the machine or user environment; do not add it to tracked configuration.

PowerShell session example:

```powershell
$env:HKCLR_RAPIDOCR_PROJECT = "C:\path\to\hkclr-rapidocr-evaluation"
```

## Health Check

Before the first scan in a new or updated environment:

```powershell
uv run --project $env:HKCLR_RAPIDOCR_PROJECT hkclr-ocr doctor --initialize
```

The bridge is unavailable when the variable is empty, the directory or `pyproject.toml` is missing, the command is missing, or the health check exits nonzero. Unavailability must not block reimbursement preparation.

## Batch Invocation

Run one process per batch. Do not run concurrent scans against the same output directory.

```powershell
$batch = "C:\path\to\reimbursement-batch"
$ocrOutput = Join-Path $batch "generated\ocr\rapidocr"

uv run --project $env:HKCLR_RAPIDOCR_PROJECT hkclr-ocr scan `
  $batch `
  --output $ocrOutput `
  --profile auto
```

The external tool must treat the batch as read-only. Its private derived files stay under `generated\ocr\rapidocr`:

- `ocr-summary.json`: aggregate counts and run status.
- `ocr-manifest.jsonl`: one record per discovered image.
- `objects\*.ocr.json`: cached line-level text, boxes, confidence, and extracted field candidates.

These files can contain payment and reimbursement data. Do not commit, upload, or paste them wholesale into an agent prompt.

## Agent Consumption

Read results from coarse to fine:

1. Read `ocr-summary.json`.
2. If the schema is unsupported, stop consuming OCR output and continue the existing workflow.
3. Read `ocr-manifest.jsonl` only when summary counts require investigation.
4. Inspect individual object JSON or source images only for records that need review.

The OCR bridge may help locate transaction-number candidates, amount candidates, missing keywords, and images needing review. It does not establish accounting truth and cannot override known order data, image-quality warnings, or human judgment.

## Failure Boundary

Treat all bridge failures as soft failures in this phase. Do not install dependencies automatically, mutate the external project, retry indefinitely, or invoke the legacy screenshot-quarantine workflow from OCR results. Fall back to the existing multimodal/manual review path and report the bridge failure separately from reimbursement validation.
