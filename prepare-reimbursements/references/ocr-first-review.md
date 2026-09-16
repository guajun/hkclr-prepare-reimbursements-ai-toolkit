# OCR-first review

Use this workflow as soon as evidence arrives, including before the batch is complete. It implements canonical job generation, the external CLI wrapper, and advisory candidate persistence. Human financial decisions remain in the normal reimbursement state.

## One command

```powershell
uv run python scripts\review_evidence_ocr.py --folder "<batch-folder>"
```

The command prints only status, counts, and the private review path. Read `generated/ocr/ocr-review.json` for compact candidates and exact review reasons. To inspect one record's OCR text without reopening the image:

```powershell
uv run python scripts\review_evidence_ocr.py --folder "<batch-folder>" --evidence-id <id>
```

This prints a bounded text excerpt. Do not dump every object's full text. Use `--dry-run` for transport/schema checks only and `--no-persist` to omit SQLite candidate metadata. A dry run does not prove recognition quality.

## Explicit evidence typing

The producer reads SQLite in read-only mode, falls back to manifests, and can discover source images before either exists. It excludes raw captures, generated outputs, print copies, and quarantine. Official PDF receipts stay on the existing PDF text path. Unclassified images produce generic OCR jobs and unsupported diagnostics so the agent can read text before choosing a supported profile.

For ambiguous screenshots, write private `generated/ocr/evidence-overrides.json`:

```json
{
  "schema": "prepare-reimbursements.ocr-overrides.v1",
  "evidence": {
    "travel/example.png": {
      "requested_profile": "transit_payment",
      "business_context": {"provider": "octopus", "currency": "HKD"},
      "expected_facts": {"amount": "20.00", "currency": "HKD", "transaction_count": 2}
    },
    "travel/approval.png": {
      "requested_profile": "travel_approval",
      "expected_fields": ["approvals"]
    }
  }
}
```

The example is synthetic. Provider/currency context must come from known app/source provenance or user confirmation. Expected facts belong in the comparison sidecar, not as invented recognized text. Do not assign a whole-day total to a single screenshot when several screenshots jointly support the day. Profile names are those in the external bridge contract.

## Review results

- `text_verified`: available OCR fields agree with independent amount/currency and date/identifier facts. Review text; this does not accept evidence or prove capture quality.
- `text_review`: candidates lack sufficient independent comparison facts. Read the bounded excerpt, establish source-supported facts, and rerun if appropriate.
- `visual_review`: a concrete mismatch, missing field, low-confidence field, unsupported layout, or adapter warning needs resolution. First inspect the targeted OCR text, then only necessary images.
- `not_evaluated`: dry run only.

Native-vision usage is a workflow decision, not enforced by the CLI. Start with three necessary image inspections, report remaining reasons before expanding, and never repeat an unchanged hash unnecessarily. Aggregate route counts estimate potential visual work; they are not actual token savings, measured OCR accuracy, or evidence acceptance. Do not enable automatic financial acceptance without labelled false-acceptance measurements.

## Candidate storage and cache

The wrapper keeps each run in its own directory, validates schemas/IDs/source hashes, and seeds cache only from validated prior objects. Per-output locks and bounded subprocess calls prevent conflicting runs. OCR provider model caches remain local. Repeated files with the same pixels can share the provider's raw recognition cache while distinct evidence IDs retain distinct business comparisons.

Additive SQLite schema v4 introduces `ocr_runs` and `ocr_results` only. The original evidence files, validation rows, orders, and claim amounts are untouched. Candidate fields, warnings, versions/configuration hash, provenance, timing and result sidecar path are stored separately. Snapshot output contains only the latest run summary; text/boxes remain in private sidecars. A failed optional persistence operation must be reported separately without invalidating existing reimbursements.

## Machine setup

`HKCLR_RAPIDOCR_PROJECT` is the only external-project setting. On Windows the wrapper also reads the persisted User value when the application process predates it, so a restart is not required for this command. Do not commit the machine's value. Missing project/runtime yields `unavailable` with a reason; no automatic dependency installation occurs. Persisting configuration and provisioning the OCR environment are setup actions, not silent fallback steps.
