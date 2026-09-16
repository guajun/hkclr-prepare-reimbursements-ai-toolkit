"""Build private, canonical OCR jobs without importing or invoking an OCR engine.

Only field names cross the external job boundary. Comparison facts stay in a
separate toolkit sidecar, so OCR cannot report expected values as observations.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

JOB_MANIFEST_SCHEMA = "hkclr.rapidocr.job-manifest.v1"
JOB_SCHEMA = "hkclr.rapidocr.job.v1"
CONFIG_SCHEMA = "hkclr.rapidocr.config.v1"
BUILD_SCHEMA = "prepare-reimbursements.ocr-job-build.v1"
COMPARISON_SCHEMA = "prepare-reimbursements.ocr-comparisons.v1"
OVERRIDES_SCHEMA = "prepare-reimbursements.ocr-overrides.v1"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
PROFILES = {
    "taobao_order_detail", "xianyu_order_detail", "alipay_payment_detail",
    "vendor_receipt", "travel_approval", "ride_payment", "transit_payment", "generic",
}
PROFILE_FIELDS = {
    "taobao_order_detail": ["amount", "payment_method", "transaction_id"],
    "xianyu_order_detail": ["order_id", "amount"],
    "alipay_payment_detail": ["amount", "transaction_id"],
    "vendor_receipt": ["receipt_id", "paid_date", "amount", "currency", "payment_method"],
    "travel_approval": ["approvals"],
    "ride_payment": ["transactions", "candidate_totals"],
    "transit_payment": ["transactions", "candidate_totals"],
    "generic": [],
}
KIND_PROFILES = {
    "xianyu_order_detail_screenshot": "xianyu_order_detail",
    "alipay_payment_screenshot": "alipay_payment_detail",
    "alipay_payment_detail": "alipay_payment_detail",
    "travel_approve": "travel_approval", "travel_approval": "travel_approval",
    "travel_approval_screenshot": "travel_approval",
    "travel_didi_payment": "ride_payment", "ride_payment": "ride_payment",
    "travel_ride_payment_screenshot": "ride_payment",
    "travel_mtr_payment": "transit_payment", "transit_payment": "transit_payment",
    "travel_transit_payment_screenshot": "transit_payment",
    "invoice_image": "vendor_receipt", "receipt_image": "vendor_receipt",
    "invoice_screenshot": "vendor_receipt", "receipt_screenshot": "vendor_receipt",
    "vendor_receipt": "vendor_receipt",
}
FACT_FIELDS = {
    "amount", "currency", "date", "order_date", "paid_date", "order_no", "alipay_trade_no", "receipt_id",
    "transaction_count", "transactions", "per_transactions", "candidate_totals",
    "start_date", "end_date", "destination", "approval_status", "approval_intervals",
}
CONTEXT_FIELDS = {"provider", "currency", "locale", "timezone"}


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("JSON root must be an object")
    return value


def _object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _source_path(batch: Path, value: Any) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value.replace("\\", "/"))
    return (path if path.is_absolute() else batch / path).resolve()


def _input_is_derived(batch: Path, value: Any) -> bool:
    """Reject generated aliases before resolving symlinks to source images."""
    if not isinstance(value, str):
        return False
    path = Path(value.replace("\\", "/"))
    try:
        relative = path.relative_to(batch) if path.is_absolute() else path
    except ValueError:
        return False
    return any(_excluded_component(part) for part in relative.parts[:-1])


def _excluded_component(component: str) -> bool:
    name = component.lower()
    return (
        name in {"generated", "quarantine", "print-flat", "print_flat", "raw", "ocr",
                 ".git", ".venv", ".codex-tmp", "__pycache__", "ocr-visualizations"}
        or name.startswith(("_raw", "raw_", "raw-", "_previous", "previous_", "previous-", "quarantine-", "quarantine_"))
    )


def _path_reason(batch: Path, path: Path) -> str | None:
    try:
        relative = path.relative_to(batch)
    except ValueError:
        return "outside_batch"
    if any(_excluded_component(part) for part in relative.parts[:-1]):
        return "derived_or_noncanonical_path"
    if path.suffix.lower() == ".pdf":
        return "direct_pdf_path"
    if path.suffix.lower() not in IMAGE_SUFFIXES:
        return "not_an_image"
    if not path.is_file():
        return "missing_source"
    return None


def _relative(batch: Path, path: Path) -> str:
    try:
        return path.relative_to(batch).as_posix()
    except ValueError:
        return str(path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _path_key(path: Path) -> str:
    return os.path.normcase(str(path))


def _candidate(evidence: dict[str, Any], *, order: dict[str, Any] | None = None,
               state_ref: dict[str, Any] | None = None) -> dict[str, Any]:
    details = _object(evidence.get("details") or evidence.get("details_json"))
    return {
        "path": evidence.get("relative_path") or evidence.get("actual_path"),
        "kind": evidence.get("evidence_kind") or evidence.get("kind") or "unclassified",
        "capture_method": evidence.get("capture_method", ""),
        "details": details,
        "order": order or {},
        "state_ref": state_ref or {},
        "stored_sha256": evidence.get("sha256"),
        "blocked_reason": evidence.get("blocked_reason"),
    }


def _database_candidates(batch: Path, db_path: Path) -> tuple[list[dict[str, Any]], set[str]]:
    """Read existing state without schema migration or evidence validity writes."""
    rows: list[dict[str, Any]] = []
    scopes: set[str] = set()
    connection = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version not in {1, 2, 3, 4}:
            raise ValueError(f"Unsupported state schema version: {version}")
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        batch_row = connection.execute("SELECT id FROM batches WHERE batch_folder = ?", (str(batch),)).fetchone()
        if batch_row is None:
            raise ValueError("No matching batch in state database")
        batch_id = int(batch_row["id"])
        if {"orders", "evidence_files"} <= tables:
            orders = connection.execute("SELECT * FROM orders WHERE batch_id=? ORDER BY source_order_index,id", (batch_id,))
            for raw_order in orders:
                order = {**_object(raw_order["raw_json"]), **dict(raw_order)}
                for evidence in connection.execute("SELECT * FROM evidence_files WHERE order_id=? ORDER BY evidence_kind,id", (order["id"],)):
                    values = dict(evidence)
                    if not str(order.get("order_no") or "").strip():
                        values["blocked_reason"] = "not_reimbursable_blank_order"
                    rows.append(_candidate(values, order=order, state_ref={"table": "evidence_files", "id": evidence["id"], "order_id": order["id"]}))
                    scopes.add("normal")
        if "travel_evidence_files" in tables:
            for evidence in connection.execute("SELECT * FROM travel_evidence_files WHERE batch_id=? ORDER BY evidence_index,id", (batch_id,)):
                rows.append(_candidate(dict(evidence), state_ref={"table": "travel_evidence_files", "id": evidence["id"]}))
                scopes.add("travel")
        return rows, scopes
    finally:
        connection.close()


def _manifest_candidates(payload: dict[str, Any], scope: str) -> list[dict[str, Any]]:
    schema = payload.get("schema", "")
    accepted = {"prepare-reimbursements.taobao-normal.v1", "prepare-reimbursements.travel.v1",
                "prepare-reimbursements.state.snapshot.v3", "prepare-reimbursements.state.snapshot.v4"}
    if schema not in accepted:
        raise ValueError(f"Unsupported reimbursement manifest schema: {schema!r}")
    rows: list[dict[str, Any]] = []
    if scope == "travel":
        evidence = payload.get("travel", {}).get("evidence", []) if "travel" in payload else payload.get("evidence", [])
        for index, item in enumerate(evidence):
            rows.append(_candidate(item, state_ref={"manifest": "travel", "index": index}))
        return rows
    for position, order in enumerate(payload.get("orders", []), 1):
        if not str(order.get("order_no") or "").strip():
            for evidence in order.get("evidence", []):
                rows.append(_candidate({**evidence, "blocked_reason": "not_reimbursable_blank_order"}, order=order,
                                       state_ref={"manifest": "normal", "index": position}))
            continue
        if order.get("evidence"):
            for evidence in order["evidence"]:
                rows.append(_candidate(evidence, order=order, state_ref={"manifest": "normal", "index": position}))
            continue
        source = str(order.get("source") or "taobao")
        index = int(order.get("source_order_index") or order.get("index") or position)
        order_no = str(order["order_no"])
        folder = f"物品/{source}/{index:02d}_{order_no}"
        # These are the established manifest conventions, not filename classification.
        names = {
            "taobao_order_detail_screenshot": f"{index:02d}_{order_no}_taobao_order_detail.png",
            "payment_record_screenshot": f"{index:02d}_{order_no}_payment_record.png",
            "invoice_pdf": order.get("direct_document_filename"),
            "receipt_pdf": order.get("direct_document_filename"),
        }
        required = order.get("evidence_required") or ["taobao_order_detail_screenshot", "payment_record_screenshot"]
        for kind in required:
            filename = names.get(kind)
            rows.append(_candidate({"kind": kind, "relative_path": f"{folder}/{filename}" if filename else None}, order=order,
                                   state_ref={"manifest": "normal", "index": position}))
    return rows


def _discover_unclassified(batch: Path, scope: str) -> list[dict[str, Any]]:
    root = batch / ("差旅" if scope == "travel" else "物品")
    rows: list[dict[str, Any]] = []
    if root.is_dir():
        for directory, folders, filenames in os.walk(root, followlinks=False):
            folders[:] = sorted(name for name in folders if not _excluded_component(name))
            for name in sorted(filenames):
                path = Path(directory) / name
                if path.suffix.lower() in IMAGE_SUFFIXES:
                    rows.append(_candidate({"relative_path": _relative(batch, path), "kind": "unclassified"},
                                           state_ref={"discovery": scope}))
    return rows


def _profile(candidate: dict[str, Any]) -> str | None:
    kind = candidate["kind"]
    details = candidate["details"]
    explicit = details.get("requested_profile") or details.get("ocr_profile")
    if explicit:
        return explicit if isinstance(explicit, str) and explicit in PROFILES else None
    if kind == "taobao_order_detail_screenshot":
        return {"taobao": "taobao_order_detail", "xianyu": "xianyu_order_detail"}.get(str(candidate["order"].get("source", "")).lower())
    if kind == "payment_record_screenshot":
        if (str(candidate["order"].get("source", "")).lower() in {"taobao", "xianyu"}
                or "alipay" in candidate["capture_method"]):
            return "alipay_payment_detail"
        return None
    if kind in {"travel_payment_screenshot", "travel_evidence_image"}:
        provider = str(details.get("provider") or _object(details.get("business_context")).get("provider") or "").lower()
        return {"didi": "ride_payment", "mtr": "transit_payment", "octopus": "transit_payment"}.get(provider)
    return KIND_PROFILES.get(kind)


def _facts(candidate: dict[str, Any], profile: str) -> dict[str, Any]:
    order = candidate["order"]
    facts: dict[str, Any] = {}
    if order:
        if profile in {"taobao_order_detail", "xianyu_order_detail"}:
            facts["amount"] = order.get("purchase_amount", order.get("amount_rmb"))
            facts["currency"] = order.get("purchase_currency") or ("RMB" if order.get("amount_rmb") is not None else None)
            facts["order_date"] = order.get("order_date") or order.get("date")
            facts["order_no"] = order.get("order_no")
        elif profile == "alipay_payment_detail":
            facts["amount"] = order.get("payment_amount")
            facts["currency"] = order.get("payment_currency")
            facts["paid_date"] = order.get("payment_date") or order.get("paid_date")
        elif profile == "vendor_receipt":
            facts["amount"] = order.get("purchase_amount", order.get("claim_amount"))
            facts["currency"] = order.get("purchase_currency") or order.get("claim_currency")
            facts["date"] = order.get("receipt_date") or order.get("order_date") or order.get("date")
            facts["receipt_id"] = order.get("receipt_id")
        if profile in {"taobao_order_detail", "alipay_payment_detail"}:
            facts["alipay_trade_no"] = order.get("alipay_trade_no")
    facts.update({key: value for key, value in _object(candidate["details"].get("expected_facts")).items() if key in FACT_FIELDS})
    return {key: value for key, value in facts.items() if value is not None and value != ""}


def build_jobs(batch_folder: Path, *, db_path: Path | None = None,
               manifest_path: Path | None = None, overrides_path: Path | None = None) -> dict[str, Any]:
    """Return deterministic jobs, comparison sidecar and unsupported diagnostics.

    The source database, manifests and evidence files are read-only. Unsupported
    Untyped images get generic jobs for OCR-first text access and remain explicit
    unsupported candidates for business review; they carry no comparison facts.
    """
    batch = batch_folder.resolve()
    generated = batch / "generated"
    diagnostics: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    scopes: set[str] = set()
    database = db_path or generated / "reimbursement-state.sqlite3"
    if database.is_file():
        try:
            candidates, scopes = _database_candidates(batch, database)
        except (sqlite3.Error, ValueError, KeyError, TypeError) as error:
            diagnostics.append({"reason": "state_unavailable", "error_type": type(error).__name__})
    for scope, path in (("normal", manifest_path or generated / "reimbursement-manifest.json"),
                        ("travel", generated / "travel-reimbursement-manifest.json")):
        if scope in scopes:
            continue
        if path.is_file():
            try:
                found = _manifest_candidates(_json(path), scope)
                candidates.extend(found)
                if found:
                    scopes.add(scope)
            except (OSError, ValueError, KeyError, TypeError) as error:
                diagnostics.append({"reason": "manifest_unavailable", "scope": scope, "error_type": type(error).__name__})
    # New screenshots must reach OCR before their name/profile has been reviewed,
    # even when an older state DB already exists. Canonical records still win.
    for scope in ("normal", "travel"):
        candidates.extend(_discover_unclassified(batch, scope))
    candidates.extend(_candidate({"relative_path": path.name, "kind": "unclassified"}, state_ref={"discovery": "batch_root"})
                      for path in sorted(batch.glob("*")) if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES)

    overrides: dict[str, dict[str, Any]] = {}
    override_file = overrides_path or generated / "ocr" / "evidence-overrides.json"
    if override_file.is_file():
        try:
            payload = _json(override_file)
            if payload.get("schema") != OVERRIDES_SCHEMA or not isinstance(payload.get("evidence"), dict):
                raise ValueError("Unsupported OCR override schema")
            for raw_path, value in payload["evidence"].items():
                path = _source_path(batch, raw_path)
                if path is None or not isinstance(value, dict):
                    raise ValueError("Every override must name a source path and object")
                if not _input_is_derived(batch, raw_path):
                    overrides[_path_key(path)] = value
                candidates.append(_candidate({"relative_path": raw_path, "kind": value.get("evidence_kind", "explicit_override")},
                                             state_ref={"override": _relative(batch, path)}))
        except (OSError, ValueError, TypeError) as error:
            overrides = {}
            diagnostics.append({"reason": "overrides_unavailable", "error_type": type(error).__name__})

    jobs: list[dict[str, Any]] = []
    comparisons: list[dict[str, Any]] = []
    unsupported: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    seen: set[str] = set()
    canonical_hashes: dict[str, str] = {}
    source_hashes: dict[str, str] = {}
    for candidate in candidates:
        if "discovery" in candidate["state_ref"] or candidate["blocked_reason"] or _input_is_derived(batch, candidate["path"]):
            continue
        path = _source_path(batch, candidate["path"])
        if path is not None and not _path_reason(batch, path):
            try:
                source_hashes[_path_key(path)] = _sha256(path)
                canonical_hashes.setdefault(source_hashes[_path_key(path)], _relative(batch, path))
            except OSError:
                pass
    # Preserve first state record for each canonical path; override-only candidates follow state.
    for candidate in candidates:
        if _input_is_derived(batch, candidate["path"]):
            excluded.append({"source_path": str(candidate["path"]).replace("\\", "/"),
                             "reason": "derived_or_noncanonical_path", "source_kind": candidate["kind"]})
            continue
        path = _source_path(batch, candidate["path"])
        if path is None:
            unsupported.append({"reason": "missing_source_path", "source_kind": candidate["kind"], "state_ref": candidate["state_ref"]})
            continue
        key = _path_key(path)
        if key in seen:
            continue
        seen.add(key)
        relative = _relative(batch, path)
        if candidate["blocked_reason"]:
            excluded.append({"source_path": relative, "reason": candidate["blocked_reason"], "source_kind": candidate["kind"]})
            continue
        reason = _path_reason(batch, path)
        if reason:
            excluded.append({"source_path": relative, "reason": reason, "source_kind": candidate["kind"]})
            continue
        try:
            source_hash = source_hashes.get(key) or _sha256(path)
        except OSError as error:
            unsupported.append({"source_path": relative, "reason": "source_unreadable", "error_type": type(error).__name__})
            continue
        if "discovery" in candidate["state_ref"] and key not in overrides and source_hash in canonical_hashes:
            excluded.append({"source_path": relative, "reason": "duplicate_of_canonical_source",
                             "canonical_source_path": canonical_hashes[source_hash], "source_kind": candidate["kind"]})
            continue
        override = overrides.get(key, {})
        profile = override.get("requested_profile") if "requested_profile" in override else _profile(candidate)
        if not isinstance(profile, str) or profile not in PROFILES:
            unsupported.append({"source_path": relative, "reason": "unsupported_profile" if profile else "profile_required",
                                "source_kind": candidate["kind"], "state_ref": candidate["state_ref"]})
            profile = "generic"
        elif profile == "generic":
            unsupported.append({"source_path": relative, "reason": "generic_profile",
                                "source_kind": candidate["kind"], "state_ref": candidate["state_ref"]})
        evidence_id = "ev-" + hashlib.sha256(relative.encode("utf-8")).hexdigest()[:24]
        context = {**_object(candidate["details"].get("business_context")), **_object(override.get("business_context"))}
        if candidate["details"].get("provider") and "provider" not in context:
            context["provider"] = candidate["details"]["provider"]
        context = {name: value for name, value in context.items() if name in CONTEXT_FIELDS and isinstance(value, str)}
        facts = {} if profile == "generic" else {**_facts(candidate, profile), **{name: value for name, value in _object(override.get("expected_facts")).items() if name in FACT_FIELDS}}
        expected_fields = list(PROFILE_FIELDS[profile])
        explicit_fields = override.get("expected_fields")
        fields_overridden = False
        if explicit_fields is not None and profile != "generic":
            if (isinstance(explicit_fields, list) and all(isinstance(value, str) and value.strip() for value in explicit_fields)
                    and len(explicit_fields) == len(set(explicit_fields))):
                expected_fields = list(explicit_fields)
                fields_overridden = True
            else:
                diagnostics.append({"source_path": relative, "reason": "invalid_expected_fields_override"})
        # Request additional observations when the toolkit has comparison facts.
        additional_fields = () if fields_overridden else (("order_no", "order_id"), ("date", "paid_date"),
                                                         ("order_date", "order_date"), ("paid_date", "paid_date"), ("currency", "currency"))
        for fact, field in additional_fields:
            if fact in facts and profile not in {"ride_payment", "transit_payment", "travel_approval"} and field not in expected_fields:
                expected_fields.append(field)
        jobs.append({"schema": JOB_SCHEMA, "evidence_id": evidence_id, "source_path": str(path),
                     "requested_profile": profile, "expected_fields": expected_fields, "business_context": context})
        comparisons.append({"evidence_id": evidence_id, "source_path": str(path), "source_sha256": source_hash,
                            "source_kind": candidate["kind"], "profile": profile, "expected_facts": facts,
                            "state_ref": candidate["state_ref"],
                            "toolkit_status": "unsupported" if profile == "generic" else "pending",
                            "source_hash_changed": bool(candidate["stored_sha256"] and candidate["stored_sha256"] != source_hash)})
    jobs.sort(key=lambda row: _relative(batch, Path(row["source_path"])))
    comparisons.sort(key=lambda row: _relative(batch, Path(row["source_path"])))
    unsupported.sort(key=lambda row: (row.get("source_path", ""), row["reason"]))
    excluded.sort(key=lambda row: (row.get("source_path", ""), row["reason"]))
    return {"schema": BUILD_SCHEMA, "job_manifest": {"schema": JOB_MANIFEST_SCHEMA,
            "configuration": {"schema": CONFIG_SCHEMA}, "jobs": jobs}, "comparison_records": comparisons,
            "unsupported": unsupported, "excluded": excluded, "diagnostics": diagnostics}


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=".ocr-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write_jobs(payload: dict[str, Any], batch_folder: Path) -> dict[str, str]:
    """Atomically publish private outputs under the batch's generated/ocr folder."""
    folder = batch_folder.resolve() / "generated" / "ocr"
    paths = {"manifest": folder / "jobs.json", "comparisons": folder / "comparisons.json",
             "summary": folder / "job-build-summary.json"}
    _atomic_json(paths["manifest"], payload["job_manifest"])
    _atomic_json(paths["comparisons"], {"schema": COMPARISON_SCHEMA, "comparison_records": payload["comparison_records"]})
    _atomic_json(paths["summary"], {key: value for key, value in payload.items() if key not in {"job_manifest", "comparison_records"}})
    return {key: str(path) for key, path in paths.items()}
