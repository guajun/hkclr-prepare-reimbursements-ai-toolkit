# HKCLR Prepare Reimbursements AI Toolkit

Codex skill and scripts for preparing reimbursement batches from edited Taobao order exports and manually collected evidence.

The current workflow reads a dated reimbursement folder, applies the convention that a blank order number marks an order as not reimbursable, groups multi-SKU Taobao orders by merged Excel cells, and generates:

- `reimbursement-manifest.json`
- `reimbursement-review.xlsx`
- `報銷清單_Reimbursement list <name> <date>.xlsx`

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

## Privacy

Do not commit real reimbursement exports, screenshots, bank details, or generated reimbursement workbooks to this repository.
