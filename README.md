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
