"""Synthetic evidence comparison, routing, and additive persistence acceptance."""
from __future__ import annotations

from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from prepare_reimbursements import ocr_review as review
from prepare_reimbursements import state_db


def field(value, confidence=0.99, **extras):
    return {"value": value, "confidence": confidence, "source_box": [1, 2, 3, 4], **extras}


def run_fixture():
    record = {"evidence_id": "synthetic-001", "source_path": "synthetic.png", "source_sha256": "a" * 64,
              "profile": "alipay_payment_detail", "status": "pass", "external_status": "ok",
              "ocr_status": "ok", "profile_support_status": "supported", "profile_check": "pass",
              "extracted_fields": {"amount": field("30.26", currency="CNY"), "currency": field("CNY"),
                                   "transaction_id": field("synthetic-trade-001")},
              "transactions": [], "candidate_totals": [{"currency": "CNY", "amount": "30.26"}],
              "adapter_warnings": [], "engine": {"name": "fixture", "model": "synthetic"},
              "adapter": {"version": "fixture-v1"}, "result_schema": "hkclr.rapidocr.result.v2"}
    comparison = {"evidence_id": record["evidence_id"], "source_path": record["source_path"],
                  "source_sha256": record["source_sha256"],
                  "expected_facts": {"amount": "30.26", "currency": "RMB", "alipay_trade_no": "synthetic-trade-001"}}
    return {"run_id": "fixture-run", "available": True, "status": "available", "dry_run": False,
            "records": [record], "comparison_records": [comparison], "configuration": {"minimum_score": 0.5}}


class OCRReviewTests(unittest.TestCase):
    def test_valid_text_match_does_not_accept_evidence_or_include_geometry(self):
        result = review.build_review(run_fixture())
        self.assertEqual(result["counts"], {"text_verified": 1})
        self.assertFalse(result["entries"][0]["evidence_accepted"])
        self.assertNotIn("source_box", json.dumps(result))
        self.assertEqual(result["metrics"]["false_acceptance"], "not_measured")

    def test_missing_integer_row_is_detected_even_if_external_check_passes(self):
        run = run_fixture()
        record = run["records"][0]
        record.update(profile="ride_payment", extracted_fields={},
                      transactions=[{"date": field("2026-07-13"), "amount": field("15.26", currency="CNY")}],
                      candidate_totals=[{"currency": "CNY", "amount": "15.26"}])
        run["comparison_records"][0]["expected_facts"] = {
            "amount": "30.26", "currency": "CNY", "date": "2026-07-13", "transaction_count": 2}
        entry = review.build_review(run)["entries"][0]
        self.assertEqual(entry["route"], "visual_review")
        self.assertIn("amount_missing_or_mismatch", entry["reasons"])
        self.assertIn("transaction_count_mismatch", entry["reasons"])

    def test_order_date_and_next_day_payment_are_separate_facts(self):
        run = run_fixture()
        record = run["records"][0]
        record["profile"] = "taobao_order_detail"
        record["extracted_fields"].update(order_date=field("2026-07-13"), paid_date=field("2026-07-14"))
        facts = run["comparison_records"][0]["expected_facts"]
        facts["order_date"] = "2026-07-13"
        facts["paid_date"] = "2026-07-14"
        self.assertEqual(review.build_review(run)["counts"], {"text_verified": 1})
        del record["extracted_fields"]["order_date"]
        facts["order_date"] = "2026-07-14"
        reasons = review.build_review(run)["entries"][0]["reasons"]
        self.assertIn("order_date_missing_or_mismatch", reasons)
        self.assertNotIn("paid_date_missing_or_mismatch", reasons)

    def test_transaction_totals_must_match_rows_and_detail_expectations(self):
        run = run_fixture()
        record = run["records"][0]
        record.update(profile="ride_payment", extracted_fields={},
                      transactions=[{"date": field("2026-07-13"), "amount": field("15.00", currency="CNY")},
                                    {"date": field("2026-07-13"), "amount": field("15.26", currency="CNY")}])
        facts = {"amount": "30.26", "currency": "CNY", "date": "2026-07-13", "transaction_count": 2,
                 "per_transactions": [{"date": "2026-07-13", "amount": "15.26", "currency": "RMB"},
                                      {"date": "2026-07-13", "amount": "15", "currency": "CNY"}]}
        run["comparison_records"][0]["expected_facts"] = facts
        self.assertEqual(review.build_review(run)["counts"], {"text_verified": 1})
        facts["per_transactions"][0]["amount"] = "14.26"
        entry = review.build_review(run)["entries"][0]
        self.assertIn("per_transactions_missing_or_mismatch", entry["reasons"])
        record["candidate_totals"][0]["amount"] = "99"
        self.assertIn("candidate_totals_inconsistent_with_transactions", review.build_review(run)["entries"][0]["reasons"])

    def test_negative_transit_debits_normalize_but_mixed_credit_debit_is_reviewed(self):
        run = run_fixture()
        record = run["records"][0]
        record.update(profile="transit_payment", extracted_fields={},
                      transactions=[{"date": field("2026-07-13"), "amount": field("-28.80", currency="HKD")},
                                    {"date": field("2026-07-13"), "amount": field("-28.80", currency="HKD")}],
                      candidate_totals=[{"currency": "HKD", "amount": "-57.60"}])
        run["comparison_records"][0]["expected_facts"] = {"amount": "57.60", "currency": "HKD", "date": "2026-07-13"}
        self.assertEqual(review.build_review(run)["counts"], {"text_verified": 1})
        record["transactions"][1]["amount"]["value"] = "28.80"
        self.assertIn("mixed_debit_credit_transactions", review.build_review(run)["entries"][0]["reasons"])

    def test_unknown_expected_facts_and_bad_confidence_never_verify(self):
        for score in (float("nan"), float("inf"), -1, 1.2, None, "invalid"):
            with self.subTest(score=score):
                run = run_fixture()
                run["records"][0]["extracted_fields"]["amount"]["confidence"] = score
                self.assertEqual(review.build_review(run)["counts"], {"visual_review": 1})
        run = run_fixture()
        del run["records"][0]["extracted_fields"]["amount"]["confidence"]
        self.assertEqual(review.build_review(run)["counts"], {"visual_review": 1})
        run = run_fixture()
        run["comparison_records"][0]["expected_facts"]["merchant_unsupported"] = "name"
        self.assertIn("unsupported_expected_fact:merchant_unsupported", review.build_review(run)["entries"][0]["reasons"])

    def test_unavailable_unsupported_dryrun_and_comparison_identity(self):
        for key, value in (("status", "available"), ("ocr_status", "not_run"), ("external_status", "mystery")):
            run = run_fixture()
            run["records"][0][key] = value
            self.assertNotEqual(review.build_review(run)["entries"][0]["route"], "text_verified")
        run = run_fixture()
        run["available"] = False
        self.assertIn("run_unavailable", review.build_review(run)["entries"][0]["reasons"])
        run = run_fixture()
        run["dry_run"] = True
        self.assertEqual(review.build_review(run)["counts"], {"not_evaluated": 1})
        for key, value in (("source_sha256", "b" * 64), ("source_path", "other.png")):
            run = run_fixture()
            run["comparison_records"][0][key] = value
            self.assertIn("comparison_identity_mismatch", review.build_review(run)["entries"][0]["reasons"])
        run = run_fixture()
        run["comparison_records"].append(deepcopy(run["comparison_records"][0]))
        self.assertIn("comparison_identity_mismatch", review.build_review(run)["entries"][0]["reasons"])

    def test_approval_intervals_compare_complete_multiset(self):
        run = run_fixture()
        record = run["records"][0]
        record.update(profile="travel_approval", transactions=[], candidate_totals=[],
                      extracted_fields={"approvals": field([
                          {"start_date": field("2026-07-13"), "end_date": field("2026-07-13"), "approval_status": field("approved")},
                          {"start_date": field("2026-07-14"), "end_date": field("2026-07-14"), "approval_status": field("approved")}])})
        intervals = [{"start_date": "2026-07-14", "end_date": "2026-07-14", "approval_status": "approved"},
                     {"start_date": "2026-07-13", "end_date": "2026-07-13", "approval_status": "approved"}]
        run["comparison_records"][0]["expected_facts"] = {"approval_intervals": intervals}
        self.assertEqual(review.build_review(run)["counts"], {"text_verified": 1})
        intervals.pop()
        self.assertEqual(review.build_review(run)["counts"], {"visual_review": 1})

    def test_persistence_is_idempotent_additive_and_does_not_read_raw_objects(self):
        with tempfile.TemporaryDirectory(prefix="ocr-review-") as directory:
            batch = Path(directory)
            db = batch / "generated" / "reimbursement-state.sqlite3"
            with closing(state_db.connect(db)) as connection, connection:
                connection.execute("INSERT INTO batches (batch_folder,batch_label,reimbursement_type,source_manifest_path,profile_json,summary_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                    (str(batch), "fixture", "normal", "synthetic.json", "{}", "{}", "fixture", "fixture"))
                before = {table: list(connection.execute(f"SELECT * FROM {table}")) for table in ("batches", "orders", "evidence_files")}
            run = run_fixture()
            # Persistence must use validated compact metadata, never reopen this object.
            run["records"][0]["result_path"] = str(batch / "malformed.json")
            (batch / "malformed.json").write_text("NOT JSON", encoding="utf-8")
            for _ in range(2):
                review.review_outputs(batch, run)
            with closing(sqlite3.connect(db)) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM ocr_runs").fetchone()[0], 1)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM ocr_results").fetchone()[0], 1)
                self.assertEqual(connection.execute("SELECT provider FROM ocr_results").fetchone()[0], "fixture")
                saved_review = Path(connection.execute("SELECT summary_path FROM ocr_runs").fetchone()[0])
                self.assertNotEqual(saved_review, batch / "generated" / "ocr" / "ocr-review.json")
                self.assertTrue(saved_review.is_file())
                for table, rows in before.items():
                    self.assertEqual([tuple(row) for row in rows], list(connection.execute(f"SELECT * FROM {table}")))
            original_hash = state_db.sha256_file(db)
            run["dry_run"] = True
            review.review_outputs(batch, run)
            self.assertEqual(state_db.sha256_file(db), original_hash)

    def test_different_run_keeps_historical_review_and_id_reuse_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="ocr-history-") as directory:
            batch = Path(directory)
            db = batch / "generated" / "reimbursement-state.sqlite3"
            with closing(state_db.connect(db)):
                pass
            run = run_fixture()
            first = review.review_outputs(batch, run)
            first_content = Path(first["review_path"]).read_bytes()
            run["run_id"] = "second-fixture-run"
            review.review_outputs(batch, run)
            self.assertEqual(Path(first["review_path"]).read_bytes(), first_content)
            run["configuration"]["minimum_score"] = 0.9
            result = review.review_outputs(batch, run)
            self.assertEqual(result["persistence_error"], "ValueError")

    def test_absent_or_unrelated_database_is_not_initialized(self):
        with tempfile.TemporaryDirectory(prefix="ocr-review-") as directory:
            batch = Path(directory)
            db = batch / "generated" / "reimbursement-state.sqlite3"
            review.review_outputs(batch, run_fixture())
            self.assertFalse(db.exists())
            with closing(sqlite3.connect(db)) as connection, connection:
                connection.execute("CREATE TABLE unrelated (value TEXT)")
            before = db.read_bytes()
            review.review_outputs(batch, run_fixture())
            self.assertEqual(db.read_bytes(), before)

    def test_version_three_metadata_migration_preserves_business_state(self):
        with tempfile.TemporaryDirectory(prefix="ocr-migration-") as directory:
            batch = Path(directory)
            db = batch / "generated" / "reimbursement-state.sqlite3"
            with closing(state_db.connect(db)) as connection, connection:
                connection.execute("CREATE TABLE business_sentinel (amount REAL, status TEXT)")
                connection.execute("INSERT INTO business_sentinel VALUES (22.5,'confirmed')")
                connection.execute("DROP TABLE ocr_results")
                connection.execute("DROP TABLE ocr_runs")
                connection.execute("PRAGMA user_version=3")
            run = run_fixture()
            for _ in range(2):
                review.persist_review(db, batch_folder=batch, run=run,
                                      review=review.build_review(run), summary_path=batch / "summary.json")
            with closing(sqlite3.connect(db)) as connection:
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 4)
                self.assertEqual(connection.execute("SELECT * FROM business_sentinel").fetchall(), [(22.5, "confirmed")])
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM ocr_results").fetchone()[0], 1)

    def test_excerpt_rejects_changed_source_or_result(self):
        with tempfile.TemporaryDirectory(prefix="ocr-excerpt-") as directory:
            batch = Path(directory)
            source = batch / "synthetic.png"
            source.write_bytes(b"synthetic")
            result_path = batch / "generated" / "ocr" / "objects" / "synthetic.json"
            result_path.parent.mkdir(parents=True)
            obj = {"schema": "hkclr.rapidocr.result.v2", "job": {"evidence_id": "synthetic-001"},
                   "source": {"sha256": state_db.sha256_file(source)}, "full_text": "text" * 1000}
            result_path.write_text(json.dumps(obj), encoding="utf-8")
            run = run_fixture()
            record = run["records"][0]
            record.update(source_path=str(source), source_sha256=state_db.sha256_file(source),
                          result_path=str(result_path), result_sha256=state_db.sha256_file(result_path))
            run["comparison_records"][0].update(source_path=str(source), source_sha256=record["source_sha256"])
            review.review_outputs(batch, run, persist=False)
            self.assertEqual(len(review.read_evidence_excerpt(batch, "synthetic-001")["text_excerpt"]), 1800)
            result_path.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "changed"):
                review.read_evidence_excerpt(batch, "synthetic-001")
            refreshed = review.review_outputs(batch, run, persist=False)
            self.assertEqual(refreshed["counts"], {"visual_review": 1})


if __name__ == "__main__":
    unittest.main()
