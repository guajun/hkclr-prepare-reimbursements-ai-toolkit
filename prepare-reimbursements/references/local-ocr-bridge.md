# Local OCR CLI contract

## Scope and discovery

The toolkit wrapper calls a separately maintained RapidOCR project through its CLI. It adds no RapidOCR/ONNX Runtime dependency or direct imports from the external checkout. OCR remains advisory; the toolkit owns reimbursement manifests, image-quality validation, state, and compiled outputs. Use [the OCR-first workflow](ocr-first-review.md) for normal batch review; the commands below describe the underlying contract and acceptance checks.

`HKCLR_RAPIDOCR_PROJECT` is the only machine-local discovery mechanism. Set it to the external uv project root in the local environment; never commit its value or search for an alternative checkout. The wrapper uses process configuration first and the Windows User value if the process has no setting. It validates the directory and `pyproject.toml`; invalid configuration returns a reported unavailable state. An explicitly empty process value disables OCR.

Use the already provisioned external environment. `--no-sync` prevents uv from automatically installing/updating dependencies. Missing uv, environment, or `hkclr-ocr` is a soft failure, not an installation request.

## Health check and invocation

```powershell
uv run --no-sync --project $env:HKCLR_RAPIDOCR_PROJECT hkclr-ocr doctor
# Before actual inference in a new or updated environment:
uv run --no-sync --project $env:HKCLR_RAPIDOCR_PROJECT hkclr-ocr doctor --initialize

# Set $batch to the local batch directory and create the private manifest below.
$jobManifest = Join-Path $batch 'generated\ocr\jobs.json'
$ocrOutput = Join-Path $batch 'generated\ocr\rapidocr'
uv run --no-sync --project $env:HKCLR_RAPIDOCR_PROJECT hkclr-ocr run $jobManifest --output $ocrOutput --dry-run
# After a successful initialization check, omit --dry-run for inference:
uv run --no-sync --project $env:HKCLR_RAPIDOCR_PROJECT hkclr-ocr run $jobManifest --output $ocrOutput
```

Plain `doctor` reports package versions without loading OCR and can exit zero with optional runtime packages absent. Only `doctor --initialize` tests engine initialization. Dry-run validates jobs and inspects/hashes images without inference; it still writes summary and run-record files. It does not establish OCR accuracy or evidence validity.

Use `run <job-manifest>` for this bridge. The toolkit job producer selects canonical source paths, excluding raw captures, print-flat copies, quarantine, and OCR outputs. The wrapper serializes each batch output with a lock and creates an isolated run directory; it enforces timeouts and checks result schemas, identity and source hashes. The request manifest is separate from `reimbursement-manifest.json`. For direct manual CLI use, do not share an output directory between concurrent runs.

## Input contract

Write UTF-8 JSON without a BOM. The synthetic example below constructs a complete manifest with a runtime-resolved absolute path. Real manifests use the same shape with selected evidence IDs and paths.

| Field | Contract |
| --- | --- |
| `schema` | `hkclr.rapidocr.job-manifest.v1` |
| `configuration` | Optional object; when supplied, `schema` must be `hkclr.rapidocr.config.v1`. `minimum_score` defaults to `0.5`, range 0–1; CLI `--min-score` overrides it. |
| `jobs` | Array of job objects with unique evidence IDs. |
| Job `schema` | `hkclr.rapidocr.job.v1` |
| `evidence_id` | Stable 1–128 character identifier matching `^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$`; prefer an opaque local ID. |
| `source_path` | Absolute path to an existing readable image, resolved locally. |
| `requested_profile` | Required profile string; prefer an explicit evidence type. |
| `expected_fields` | Required array of unique, nonempty field-name strings; may be empty. |
| `business_context` | Optional JSON object, default `{}`; include only necessary known context. |

Profiles include `taobao_order_detail`, `xianyu_order_detail`, `alipay_payment_detail`, `vendor_receipt`, `travel_approval`, `ride_payment`, and `transit_payment`. The CLI also accepts `auto`, `generic`, and legacy aliases `taobao`/`alipay`; these do not guarantee layout support. Supply profile-appropriate expected fields; do not invent context from uncertain OCR.

`expected_fields` checks field presence, not equality with expected amounts/dates or completeness of transaction rows. An empty list falls back to the adapter's defaults. Keep comparison facts in toolkit state for independent validation; do not use expected values to fill missing recognized values. Amounts are decimal strings, debit amounts can be negative, and Chinese yuan may be reported as `CNY`; comparisons must explicitly normalize currency and debit conventions. `unsupported` is an output status, not an accepted requested profile. For unclassified evidence, use `generic` and retain its unsupported outcome, or record it outside the executable job list.

## Output contract and consumption

| Artifact | Supported schema and contents |
| --- | --- |
| `ocr-summary.json` | `hkclr.rapidocr.summary.v2`: `dry_run`, `errors`, `processed`, `cache_hits`, `image_path_count`, `unique_image_count`, `profiles`, `profile_support`, `profile_checks`, configuration and schema versions. |
| `ocr-manifest.jsonl` | Each row is `hkclr.rapidocr.run-record.v2`, identified by `evidence_id` and `source_path`; successful inspections include `source_sha256`, `cache_key`, and `result_path`. |
| `objects/<cache-key>.ocr.json` | `hkclr.rapidocr.result.v2`: job/configuration, source hash, `ocr`, `profile_support`, typed `extracted_fields`, `transactions`, `candidate_totals`, `adapter_warnings`, `lines`, and `full_text`. |
| `visualizations/` | Optional overlays created with `--visualize`; private derived evidence. |

The nested `schemas` map identifies job v1, config v1, adapter `hkclr.rapidocr.adapter.v1`, and result v2. Configuration records adapter registry version `business-evidence-v1`. Cache identity includes source hash, evidence ID, schema/adapter versions, profile, expected fields, business context, and minimum score. Identical pixels alone do not imply the same job result.

Read the summary first and check its schema and referenced versions. Inspect run records needing review, then individual objects/source images only as necessary. Check each record/object schema before interpreting it. Unknown/missing versions, malformed/unreadable output, or nonzero command exit mean stop consuming that run and fall back; never reuse stale results from an earlier run.

Run-record `status` is `ok`, `cached`, `error`, or `dry_run`. Keep engine status (`ocr_status`) separate from `profile_support_status` and the **top-level** `profile_check`. Prioritize errors, unsupported layouts, `profile_check = review`, and adapter warnings. In objects the check is `profile_support.check`, not `extracted_fields.profile_check`. Typed fields carry `value`, `confidence`, and `source_box`. Transaction rows and per-currency candidate totals remain candidates, not accounting truth. Successful/cached OCR is not evidence acceptance; unsupported layouts never count as passes.

Dry-run rows have `status = dry_run`, `ocr_status = not_run`, and `profile_support_status = not_evaluated`. Their `result_path` is prospective: no OCR object is written. `image_path_count` counts selected jobs including failures; `unique_image_count` counts distinct hashes obtained. Neither measures valid/complete evidence. `images_discovered` is a compatibility count, not reimbursement coverage.

## Privacy and fail-open boundary

Source images are read-only. The external process may write only its designated private derived output directory. It must not edit source evidence, the reimbursement manifest, SQLite, compiled workbooks, or evidence validity. The toolkit may separately persist validated result candidates to OCR-only SQLite tables. This is an integration policy, not an OS sandbox: review the external CLI and choose a separate output directory that cannot overwrite source/state files.

Keep job manifests and results under the local batch's `generated/ocr/` tree, outside this repository. Absolute paths, IDs, business context, recognized text, errors, summaries, and overlays can all reveal reimbursement/payment data. Do not commit, upload, or paste them wholesale into agent prompts. Read only the fields needed for review. Local inference does not promise an unprovisioned runtime never downloads models; provision dependencies/models separately. Do not silently install resources to rescue a failed bridge.

All bridge failures are fail-open **for reimbursement preparation**, not automatic evidence acceptance: report unset/invalid configuration, missing CLI/runtime, failed doctor/run, invalid input, unreadable output, or unsupported schema separately from reimbursement validation. If the user requested OCR explicitly, explain an unavailable state before any visual fallback and continue supported text/PDF work. Do not silently open an entire batch of images, modify the external project as a fallback, retry indefinitely, or let OCR override known order data, image-quality warnings, or human judgment.

Screenshot quarantine is a separate legacy workaround for malformed Codex/VS Code browser captures, including tiled/blank images. It is not an OCR stage. OCR results must never invoke `quarantine_invalid_evidence.py`, move/delete evidence, or create quarantine validation decisions. Its existing independent image-validation workflow remains unchanged.

## Synthetic doctor/dry-run acceptance

With the environment variable explicitly configured and external environment provisioned, run this PowerShell example. It creates a fresh temporary directory with only a synthetic PNG and manifest. No real reimbursement files or OCR initialization are involved.

```powershell
if ([string]::IsNullOrWhiteSpace($env:HKCLR_RAPIDOCR_PROJECT) -or
    -not (Test-Path -LiteralPath $env:HKCLR_RAPIDOCR_PROJECT -PathType Container) -or
    -not (Test-Path -LiteralPath (Join-Path $env:HKCLR_RAPIDOCR_PROJECT 'pyproject.toml') -PathType Leaf)) {
    throw 'Acceptance needs an explicitly configured project; normal workflow skips OCR here.'
}
$acceptanceRoot = Join-Path ([IO.Path]::GetTempPath()) ('hkclr-ocr-synthetic-' + [guid]::NewGuid())
New-Item -ItemType Directory -Path $acceptanceRoot | Out-Null
$source = Join-Path $acceptanceRoot 'synthetic.png'
[IO.File]::WriteAllBytes($source, [Convert]::FromBase64String('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX1sAAAAASUVORK5CYII='))
$jobManifest = Join-Path $acceptanceRoot 'jobs.json'
$manifest = @{
    schema = 'hkclr.rapidocr.job-manifest.v1'
    configuration = @{ schema = 'hkclr.rapidocr.config.v1'; minimum_score = 0.5 }
    jobs = @(@{
        schema = 'hkclr.rapidocr.job.v1'
        evidence_id = 'synthetic-receipt-001'
        source_path = $source
        requested_profile = 'vendor_receipt'
        expected_fields = @('receipt_id', 'amount', 'currency')
        business_context = @{ currency = 'HKD' }
    })
}
[IO.File]::WriteAllText($jobManifest, ($manifest | ConvertTo-Json -Depth 8), [Text.UTF8Encoding]::new($false))
$before = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash
$ocrOutput = Join-Path $acceptanceRoot 'output'
uv run --no-sync --project $env:HKCLR_RAPIDOCR_PROJECT hkclr-ocr doctor
if ($LASTEXITCODE -ne 0) { throw 'doctor failed' }
uv run --no-sync --project $env:HKCLR_RAPIDOCR_PROJECT hkclr-ocr run $jobManifest --output $ocrOutput --dry-run
if ($LASTEXITCODE -ne 0) { throw 'dry-run failed' }
$summary = Get-Content -LiteralPath (Join-Path $ocrOutput 'ocr-summary.json') -Raw | ConvertFrom-Json
$rows = @(Get-Content -LiteralPath (Join-Path $ocrOutput 'ocr-manifest.jsonl') | ForEach-Object { $_ | ConvertFrom-Json })
if ($summary.schema -ne 'hkclr.rapidocr.summary.v2' -or -not $summary.dry_run -or
    $summary.errors -ne 0 -or $summary.processed -ne 0 -or $summary.cache_hits -ne 0 -or
    $summary.image_path_count -ne 1 -or $summary.unique_image_count -ne 1 -or
    $rows.Count -ne 1 -or $rows[0].schema -ne 'hkclr.rapidocr.run-record.v2' -or
    $rows[0].evidence_id -ne 'synthetic-receipt-001' -or $rows[0].status -ne 'dry_run' -or
    $rows[0].ocr_status -ne 'not_run' -or $rows[0].profile_support_status -ne 'not_evaluated' -or
    (Test-Path -LiteralPath $rows[0].result_path) -or
    (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash -ne $before) {
    throw 'Synthetic acceptance assertions failed'
}
'PASS: doctor and manifest dry-run; source unchanged; no inference result.'
```

The throws make this acceptance exercise fail visibly; they do not block real reimbursement preparation. This proves the CLI/schema handoff and unchanged source only, not model initialization, extraction quality, or runtime enforcement of every policy above.

### Recorded acceptance (2026-09-16)

The following first-pass setup record is followed by an independent recheck in the regular external environment.

- Prerequisite #7 was already merged. External project revision: `0280377`, package `0.2.0`.
- The session/user discovery variable was unset. For this explicit synthetic exercise only, it was set in the command process to the supplied external workspace; no persistent configuration was changed.
- The existing external environment lacked the `hkclr-ocr` entry point: the first `doctor` failed with `program not found`. In a normal batch this means skip OCR and continue manual review.
- For acceptance, a separate temporary venv was provisioned with the external CLI package (`uv pip install --no-deps`), reusing existing external site-packages through a temporary `.pth` file. `UV_PROJECT_ENVIRONMENT` selected that temporary environment for uv; it did not discover the project. No toolkit dependencies or external checkout files/environment were changed. This was explicit test setup, not an automatic bridge fallback.
- Executed the PowerShell acceptance block above unchanged: doctor and manifest dry-run both exited 0. Doctor reported Python `3.13.8`, RapidOCR `3.9.1`, ONNX Runtime `1.27.0`, initialization `not_requested`.
- Assertions passed: summary v2, run-record v2, one job/one unique image, zero errors/processed/cache hits, `dry_run` / `not_run` / `not_evaluated`, unchanged source SHA-256, and no result object. No real reimbursement data was accessed. Inference initialization/accuracy was not tested.

### Independent recheck (2026-09-16)

- Two independent reviews verified the bridge contract and external adapters at revision `0280377`; all 15 external synthetic tests passed. The test invocation rebuilt the local external package, making its CLI entry point available in the regular environment.
- Re-ran the synthetic PowerShell block in that regular environment with `--no-sync`; doctor and typed manifest dry-run passed with the same schema, count, source-hash, and no-result-object assertions.
- `doctor --initialize` exited 0 and loaded the existing models. A separate `scan` dry-run on the same synthetic image also exited 0 with one path, one unique image, and zero errors. Typed `run` is the preferred bridge contract following external issue #1; `scan` remains a compatibility check for the original #9 criterion.
- The discovery variable was set only for the acceptance command process. Persistent discovery remains unset, so a normal unconfigured batch still skips OCR.
- A synthetic ride adapter probe reproduced a missing bare-integer amount while `profile_check` remained `pass`. This is a field-extraction limitation, not a bridge failure. It must remain subject to independent amount/completeness validation before any future automatic acceptance. Initialization and synthetic checks do not establish accuracy on real reimbursement evidence.
