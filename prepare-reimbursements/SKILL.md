---
name: prepare-reimbursements
description: Prepare reimbursement batches from local reimbursement folders, especially Taobao order-export spreadsheets and manually collected receipt screenshots. Use when Codex needs to scan reimbursement source files, apply the "blank order number means not reimbursable" convention, generate a review manifest, fill HKCLR normal reimbursement workbooks, plan travel reimbursement workbooks, or validate missing evidence for normal and travel reimbursement folders.
---

# Prepare Reimbursements

## Workflow

1. Identify the reimbursement batch folder, usually a dated folder such as `<reimbursement-root>\YYYY年M月D日`.
2. Read `references/conventions.md` before changing parsing, evidence, or output rules.
3. For Taobao normal reimbursement batches, run `scripts/build_taobao_normal_reimbursement.py` with `--folder <batch-folder>`.
4. Use Taobao's column-A merged row spans as order boundaries. Treat a blank top-left order number in that merged span as the user's explicit exclusion marker. Keep all SKU rows inside a valid merged span.
5. Generate three artifacts: `reimbursement-manifest.json`, `reimbursement-review.xlsx`, and `報銷清單_Reimbursement list <name> <date>.xlsx`.
6. Report skipped counts, especially blank order numbers and non-success order statuses.
7. Do not automate login, 2FA, or manual app-only flows without the user's active participation.

## Human And Agent Boundary

The human user decides which orders are reimbursable by deleting order numbers from the Taobao export. The user also handles login, 2FA, app-only evidence capture, and ambiguous business-purpose judgment.

The agent parses the edited export, fills deterministic workbook fields, generates manifests, validates required evidence, and prepares screenshot capture checklists or browser automation steps where feasible.

## Scripts

This skill is a uv project. Keep the shared virtual environment in this skill folder and do not create per-batch environments under reimbursement folders.

Set up or refresh the environment from the skill root:

```powershell
uv sync
```

Use:

```powershell
uv run python scripts\build_taobao_normal_reimbursement.py --folder "<batch-folder>"
```

If an interactive shell needs the environment activated:

```powershell
.\.venv\Scripts\Activate.ps1
```

Useful options:

- `--out-dir <dir>` writes artifacts outside the batch folder.
- `--template <xlsx>` uses an explicit previous normal reimbursement workbook.
- `--submission-date YYYY-MM-DD` controls the signature date and output filename.
- `--include-status 交易成功` keeps the default status filter.

Set applicant, bank, account, and leader values with `--name`, `--bank`, `--account`, and `--leader`.

For dependency updates, prefer `uv add <package>` for runtime dependencies and `uv add --dev <package>` for validation, testing, or frontend build helpers. Commit or keep `pyproject.toml` and `uv.lock` together.

After building the manifest and reimbursement workbook, prepare evidence folders and checklists:

```powershell
uv run python scripts\prepare_taobao_evidence.py --folder "<batch-folder>"
```

## Current Limits

Travel reimbursement, Meituan/manual evidence ingestion, and drag-and-drop local frontend are planned workflow layers. Keep them in the convention document until implemented.
