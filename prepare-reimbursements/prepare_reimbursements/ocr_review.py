"""Compact OCR candidates and text-first review, independent of claim decisions."""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import uuid
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from . import state_db

REVIEW_SCHEMA = "prepare-reimbursements.ocr-review.v1"
FACT_ALIASES = {
    "order_no": ("order_id", "order_no"),
    "alipay_trade_no": ("transaction_id", "alipay_trade_no", "trade_no"),
    "receipt_id": ("receipt_id",), "date": ("paid_date", "date", "order_date"),
    "order_date": ("order_date",), "paid_date": ("paid_date",),
    "start_date": ("start_date",), "end_date": ("end_date",),
    "destination": ("destination",), "approval_status": ("approval_status",),
}
SUPPORTED_FACTS = set(FACT_ALIASES) | {"amount", "currency", "transaction_count", "transactions",
    "per_transactions", "candidate_totals", "approval_intervals"}


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def compact(value: Any) -> Any:
    """Remove OCR geometry/full text from review and SQLite candidates."""
    if isinstance(value, dict):
        return {key: compact(item) for key, item in value.items()
                if key not in {"source_box", "source_boxes", "box", "lines", "full_text"}}
    if isinstance(value, list):
        return [compact(item) for item in value]
    return value


def unwrap(value: Any) -> Any:
    return value.get("value") if isinstance(value, dict) else value


def currency(value: Any) -> str:
    text = str(unwrap(value) or "").upper()
    return "CNY" if text == "RMB" else text


def decimal(value: Any) -> Decimal | None:
    try:
        result = Decimal(str(unwrap(value)))
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def confidences(value: Any) -> list[float]:
    if isinstance(value, dict):
        found = []
        if "value" in value or "confidence" in value:
            try:
                score = float(value["confidence"])
                found.append(score if math.isfinite(score) and 0 <= score <= 1 else 0.0)
            except (KeyError, ValueError, TypeError):
                found.append(0.0)
        for key, item in value.items():
            if key not in {"source_box", "source_boxes"}:
                found.extend(confidences(item))
        return found
    if isinstance(value, list):
        return [score for item in value for score in confidences(item)]
    return []


def _row_currency(row: dict[str, Any]) -> str:
    amount = row.get("amount")
    return currency(row.get("currency")) or (currency(amount.get("currency")) if isinstance(amount, dict) else "")


def _row_matches(expected: dict[str, Any], observed: dict[str, Any], *, debit: bool = False) -> bool:
    for key, expected_value in expected.items():
        if key == "amount":
            left, right = decimal(expected_value), decimal(observed.get(key))
            if debit and right is not None:
                right = abs(right)
            if left is None or right is None or abs(left - right) > Decimal("0.005"):
                return False
        elif key == "currency":
            if currency(expected_value) != _row_currency(observed):
                return False
        elif key in {"date", "start_date", "end_date", "destination", "approval_status"}:
            actual = unwrap(observed.get(key))
            if str(actual) != str(unwrap(expected_value)):
                return False
        else:
            return False
    return bool(expected)


def _rows_match(expected: Any, observed: Any, *, debit: bool = False) -> bool:
    """Compare a complete multiset so repeated dates/amounts retain their counts."""
    observed = unwrap(observed)
    if not isinstance(expected, list) or not isinstance(observed, list) or len(expected) != len(observed):
        return False
    if any(not isinstance(row, dict) for row in expected + observed):
        return False
    # Partial expected rows may match several observed rows; seek a full matching
    # rather than greedily consuming the only row suitable for a later expectation.
    matches: dict[int, int] = {}
    def assign(expected_index: int, visited: set[int]) -> bool:
        for index, actual in enumerate(observed):
            if index in visited or not _row_matches(expected[expected_index], actual, debit=debit):
                continue
            visited.add(index)
            if index not in matches or assign(matches[index], visited):
                matches[index] = expected_index
                return True
        return False
    return all(assign(index, set()) for index in range(len(expected)))


def _transaction_consistency(record: dict[str, Any]) -> list[str]:
    transactions = record.get("transactions") or []
    if not transactions:
        return []
    totals = record.get("candidate_totals") or []
    observed: dict[str, Decimal] = {}
    signs = set()
    for transaction in transactions:
        amount = decimal(transaction.get("amount"))
        code = _row_currency(transaction)
        if amount is None or not code:
            return ["transaction_amount_currency_incomplete"]
        signs.add(amount.compare(Decimal(0)))
        observed[code] = observed.get(code, Decimal(0)) + amount
    if record.get("profile") == "transit_payment" and Decimal(-1) in signs and Decimal(1) in signs:
        return ["mixed_debit_credit_transactions"]
    actual: dict[str, Decimal] = {}
    for total in totals:
        code, amount = _row_currency(total), decimal(total.get("amount"))
        if not code or amount is None or code in actual:
            return ["ambiguous_candidate_totals"]
        actual[code] = amount
    if set(actual) != set(observed) or any(abs(actual[code] - amount) > Decimal("0.005") for code, amount in observed.items()):
        return ["candidate_totals_inconsistent_with_transactions"]
    return []


def compare_facts(record: dict[str, Any], facts: dict[str, Any]) -> tuple[list[str], list[str]]:
    fields = record.get("extracted_fields") or {}
    transactions = record.get("transactions") or []
    totals = record.get("candidate_totals") or []
    matched: list[str] = []
    conflicts = [f"unsupported_expected_fact:{key}" for key in facts if key not in SUPPORTED_FACTS]
    conflicts.extend(_transaction_consistency(record))
    expected_currency = currency(facts.get("currency"))
    if "amount" in facts and facts["amount"] is not None:
        expected = decimal(facts["amount"])
        observed = decimal(fields.get("amount"))
        if transactions:
            candidates = [item for item in totals if not expected_currency or currency(item.get("currency")) == expected_currency]
            observed = decimal(candidates[0].get("amount")) if len(candidates) == 1 else None
        if record.get("profile") == "transit_payment" and observed is not None:
            # Expense claims use positive debit cost. Adapter warnings retain sign ambiguity.
            observed = abs(observed)
        if observed is not None and expected is not None and abs(observed - expected) <= Decimal("0.005"):
            matched.append("amount")
        else:
            conflicts.append("amount_missing_or_mismatch")
    if expected_currency:
        observed_currencies = {currency(item.get("currency")) for item in totals}
        observed_currencies.add(currency(fields.get("currency")))
        amount_field = fields.get("amount")
        if isinstance(amount_field, dict):
            observed_currencies.add(currency(amount_field.get("currency")))
        observed_currencies.discard("")
        if observed_currencies == {expected_currency}:
            matched.append("currency")
        else:
            conflicts.append("currency_missing_or_mismatch")
    for key, aliases in FACT_ALIASES.items():
        expected = facts.get(key)
        if expected is None or expected == "":
            continue
        observed = {str(unwrap(fields[name])) for name in aliases if name in fields and unwrap(fields[name]) is not None}
        if key in {"date", "order_date", "paid_date"}:
            if key == "date":
                observed.update(str(unwrap(item["date"])) for item in transactions if item.get("date"))
            expected = str(expected)[:10]
            observed = {value[:10] for value in observed}
        if observed == {str(expected)}:
            matched.append(key)
        else:
            conflicts.append(f"{key}_missing_or_mismatch")
    if facts.get("transaction_count") is not None:
        expected_count = decimal(facts["transaction_count"])
        if expected_count is not None and expected_count >= 0 and expected_count == len(transactions):
            matched.append("transaction_count")
        else:
            conflicts.append("transaction_count_mismatch")
    for key, observed in (("transactions", transactions), ("per_transactions", transactions),
                          ("candidate_totals", totals), ("approval_intervals", fields.get("approvals", []))):
        if key not in facts:
            continue
        if _rows_match(facts[key], observed, debit=record.get("profile") == "transit_payment"):
            matched.append(key)
        else:
            conflicts.append(f"{key}_missing_or_mismatch")
    return matched, conflicts


def build_review(run: dict[str, Any], *, visual_budget: int = 3, min_confidence: float = 0.85) -> dict[str, Any]:
    if type(visual_budget) is not int or visual_budget < 0 or not math.isfinite(min_confidence) or not 0 <= min_confidence <= 1:
        raise ValueError("Review budget and confidence threshold must be valid")
    comparisons = {item["evidence_id"]: item for item in run.get("comparison_records", [])}
    comparison_counts = Counter(item["evidence_id"] for item in run.get("comparison_records", []))
    entries = []
    for record in run.get("records", []):
        evidence_id = record["evidence_id"]
        comparison = comparisons.get(evidence_id, {})
        expected = comparison.get("expected_facts") or {}
        matched, conflicts = compare_facts(record, expected)
        warnings = list(record.get("adapter_warnings") or [])
        fields = record.get("extracted_fields") or {}
        transactions = record.get("transactions") or []
        scores = confidences(fields) + confidences(transactions) + confidences(record.get("candidate_totals") or [])
        reasons = list(conflicts)
        reasons.extend(record.get("provenance_warnings") or [])
        if not run.get("available") or run.get("status") != "available":
            reasons.append("run_unavailable")
        if comparison and (comparison_counts[evidence_id] != 1 or comparison.get("source_sha256") != record.get("source_sha256")
                           or comparison.get("source_path") != record.get("source_path")):
            reasons.append("comparison_identity_mismatch")
        if comparison.get("toolkit_status") == "unsupported":
            reasons.append("unclassified_evidence")
        if record.get("status") == "error" or record.get("ocr_status") == "error":
            reasons.append("ocr_error")
        if record.get("profile_check") == "unsupported" or record.get("profile_support_status") == "unsupported":
            reasons.append("unsupported_layout")
        if record.get("profile_check") == "review":
            reasons.append("missing_required_fields")
        if not run.get("dry_run") and record.get("external_status") != "dry_run" and (
            record.get("status") not in {"pass", "review"} or record.get("ocr_status") != "ok"
            or record.get("external_status") not in {"ok", "cached"}
            or record.get("profile_support_status") not in {"supported", "unsupported"}
            or record.get("profile_check") not in {"pass", "review", "unsupported"}):
            reasons.append("extraction_not_validated")
        if scores and min(scores) < min_confidence:
            reasons.append("low_field_confidence")
        if not scores and matched:
            reasons.append("missing_field_confidence")
        if warnings:
            reasons.append("adapter_warning")
        if run.get("dry_run") or record.get("external_status") == "dry_run":
            route = "not_evaluated"
        elif reasons:
            route = "visual_review"
        elif record.get("status") == "pass" and record.get("profile_check") == "pass" and comparison and (
            ({"amount", "currency"}.issubset(matched) and any(key in matched for key in ("date", "order_date", "paid_date", "order_no", "alipay_trade_no", "receipt_id", "transactions", "per_transactions")))
            or "approval_intervals" in matched):
            route = "text_verified"
        else:
            route = "text_review"
        entries.append({
            "evidence_id": evidence_id, "source_path": record.get("source_path"),
            "source_sha256": record.get("source_sha256"), "profile": record.get("profile"),
            "route": route, "reasons": reasons, "matched_fields": matched,
            "expected_facts": expected, "fields": compact(fields),
            "transactions": compact(transactions), "candidate_totals": compact(record.get("candidate_totals") or []),
            "warnings": warnings, "result_path": record.get("result_path"),
            "result_sha256": record.get("result_sha256"),
            "minimum_confidence": min(scores) if scores else None,
            "evidence_accepted": False,
        })
    routes = Counter(item["route"] for item in entries)
    return {
        "schema": REVIEW_SCHEMA, "run_id": run.get("run_id"),
        "status": run.get("status", "unavailable"), "reason": run.get("reason"),
        "counts": dict(routes), "evidence_count": len(entries),
        "visual_budget": visual_budget,
        "visual_budget_exceeded": routes["visual_review"] > visual_budget,
        "policy": "Read candidates first; images only for stated unresolved reasons. No automatic evidence acceptance.",
        "metrics": {
            "text_verified": routes["text_verified"],
            "visual_candidates": routes["visual_review"],
            "actual_visual_calls": 0,
            "false_acceptance": "not_measured",
            "token_reduction": "not_measured",
        },
        "unsupported": run.get("unsupported", []), "excluded": run.get("excluded", []),
        "entries": entries,
    }


def persist_review(db_path: Path, *, batch_folder: Path, run: dict[str, Any], review: dict[str, Any], summary_path: Path) -> None:
    """Add OCR candidate metadata only; never touch claims or evidence validity."""
    if not db_path.is_file() or not run.get("available") or run.get("dry_run") or not run.get("records"):
        return
    config_hash = hashlib.sha256(json.dumps(run.get("configuration") or {}, sort_keys=True).encode()).hexdigest()
    records = {item["evidence_id"]: item for item in run["records"]}
    run_id = str(run["run_id"])
    # Never turn an arbitrary/empty file into a reimbursement database.
    with closing(sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)) as existing:
        tables = {row[0] for row in existing.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"batches", "orders", "evidence_files"}.issubset(tables):
            return
    with closing(sqlite3.connect(db_path)) as connection, connection:
        connection.execute("PRAGMA foreign_keys=ON")
        state_db.migrate(connection)
        existing = connection.execute("SELECT batch_folder, configuration_hash, schemas_json FROM ocr_runs WHERE run_id=?", (run_id,)).fetchone()
        identity = (str(batch_folder.resolve()), config_hash, json.dumps(run.get("schemas") or {}, sort_keys=True))
        if existing is not None and tuple(existing) != identity:
            raise ValueError("An OCR run ID cannot be reused with a different identity")
        connection.execute("""INSERT INTO ocr_runs VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(run_id) DO UPDATE SET status=excluded.status,
            summary_path=excluded.summary_path, metrics_json=excluded.metrics_json""", (
            run_id, str(batch_folder.resolve()), datetime.now(timezone.utc).isoformat(),
            str(run.get("status", "unknown")), str(summary_path), config_hash,
            json.dumps(run.get("schemas") or {}, sort_keys=True), json.dumps(review["metrics"]),
        ))
        # Re-running review for this run replaces its candidate set atomically.
        connection.execute("DELETE FROM ocr_results WHERE run_id=?", (run_id,))
        for entry in review["entries"]:
            record = records[entry["evidence_id"]]
            result_path = record.get("result_path")
            adapter = record.get("adapter") or {}
            engine = record.get("engine") or {}
            connection.execute("INSERT OR REPLACE INTO ocr_results VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                run_id, entry["evidence_id"], str(entry.get("source_sha256") or ""),
                str(entry.get("profile") or "unknown"), engine.get("name", "unknown"), engine.get("model", "unknown"),
                str(adapter.get("version", "unknown")), config_hash, record.get("result_schema", "not_evaluated"),
                record.get("cache_key"), result_path, str(record.get("ocr_status", "unknown")), entry["route"],
                json.dumps({"fields": entry["fields"], "transactions": entry["transactions"], "candidate_totals": entry["candidate_totals"]}, ensure_ascii=False),
                json.dumps(entry["reasons"] + entry["warnings"], ensure_ascii=False), record.get("elapsed_seconds"),
            ))


def review_outputs(batch_folder: Path, run: dict[str, Any], *, persist: bool = True, visual_budget: int = 3) -> dict[str, Any]:
    root = (batch_folder / "generated" / "ocr").resolve()
    if not root.is_relative_to(batch_folder.resolve()):
        return {"schema": REVIEW_SCHEMA, "status": "unavailable", "counts": {}, "evidence_count": 0,
                "visual_budget_exceeded": False, "reason": "output_outside_private_ocr_directory"}
    # A stored run can be reviewed later, after a source or result changed. Keep
    # its candidates visible but never label their comparison as text-verified.
    checked_records = []
    for record in run.get("records", []):
        record = dict(record)
        warnings = list(record.get("provenance_warnings") or [])
        for path_key, hash_key, root, reason in (
            ("source_path", "source_sha256", batch_folder.resolve(), "source_missing_or_changed"),
            ("result_path", "result_sha256", (batch_folder / "generated" / "ocr").resolve(), "result_missing_or_changed"),
        ):
            if run.get("dry_run") or record.get("external_status") == "dry_run":
                continue
            try:
                path = Path(record.get(path_key) or "").resolve()
                valid = path.is_relative_to(root) and path.is_file() and state_db.sha256_file(path) == record.get(hash_key)
            except OSError:
                valid = False
            if not valid:
                warnings.append(reason)
        record["provenance_warnings"] = warnings
        checked_records.append(record)
    run = dict(run, records=checked_records)
    review = build_review(run, visual_budget=visual_budget)
    output = batch_folder / "generated" / "ocr" / "ocr-review.json"
    # Latest is convenient for users, while SQLite retains a stable per-run
    # sidecar that the next invocation cannot replace.
    run_key = hashlib.sha256(str(run.get("run_id") or "unavailable").encode("utf-8")).hexdigest()
    review_key = hashlib.sha256(json.dumps(review, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    run_output = batch_folder / "generated" / "ocr" / "reviews" / run_key / f"{review_key}.json"
    if not all(path.resolve().is_relative_to(root) for path in (output, run_output)):
        return {"schema": REVIEW_SCHEMA, "status": "unavailable", "counts": {}, "evidence_count": 0,
                "visual_budget_exceeded": False, "reason": "output_outside_private_ocr_directory"}
    atomic_json(run_output, review)
    atomic_json(output, review)
    persistence_error = None
    if persist:
        try:
            persist_review(batch_folder / "generated" / "reimbursement-state.sqlite3", batch_folder=batch_folder,
                           run=run, review=review, summary_path=run_output)
        except (OSError, sqlite3.Error, RuntimeError, ValueError) as error:
            persistence_error = type(error).__name__
    result = {"schema": REVIEW_SCHEMA, "review_path": str(run_output), "latest_review_path": str(output), "status": review["status"],
            "counts": review["counts"], "evidence_count": review["evidence_count"],
            "visual_budget_exceeded": review["visual_budget_exceeded"], "reason": review["reason"]}
    if persistence_error:
        result["persistence_error"] = persistence_error
    return result


def read_evidence_excerpt(batch_folder: Path, evidence_id: str) -> dict[str, Any]:
    """Read one bounded text excerpt only if its saved result/source identities hold."""
    root = (batch_folder / "generated" / "ocr").resolve()
    if not root.is_relative_to(batch_folder.resolve()) or not (root / "ocr-review.json").resolve().is_relative_to(root):
        raise ValueError("Review path is outside the private OCR output")
    review = json.loads((root / "ocr-review.json").read_text(encoding="utf-8"))
    if review.get("schema") != REVIEW_SCHEMA:
        raise ValueError("Unsupported review schema")
    entry = next((item for item in review["entries"] if item["evidence_id"] == evidence_id), None)
    if entry is None or not entry.get("result_path"):
        raise ValueError("Evidence has no saved OCR result")
    path = Path(entry["result_path"]).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError("Result path is unavailable or outside the private OCR output")
    source = Path(entry["source_path"]).resolve()
    if not source.is_relative_to(batch_folder.resolve()) or not source.is_file():
        raise ValueError("Source evidence is unavailable")
    if state_db.sha256_file(path) != entry.get("result_sha256") or state_db.sha256_file(source) != entry.get("source_sha256"):
        raise ValueError("Source or OCR result changed; regenerate OCR review")
    obj = json.loads(path.read_text(encoding="utf-8"))
    if obj.get("schema") != "hkclr.rapidocr.result.v2" or obj.get("job", {}).get("evidence_id") != evidence_id or obj.get("source", {}).get("sha256") != entry["source_sha256"]:
        raise ValueError("OCR result identity mismatch")
    return {"evidence_id": evidence_id, "fields": entry["fields"], "transactions": entry["transactions"],
            "reasons": entry["reasons"], "text_excerpt": str(obj.get("full_text", ""))[:1800]}
