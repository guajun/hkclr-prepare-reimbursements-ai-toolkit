"""Synthetic tests for canonical job selection and the private OCR boundary."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from prepare_reimbursements.ocr_jobs import (
    BUILD_SCHEMA, JOB_MANIFEST_SCHEMA, JOB_SCHEMA, OVERRIDES_SCHEMA, build_jobs, write_jobs,
)


class CanonicalJobsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.batch = Path(self.temp.name).resolve() / "synthetic-batch"
        self.batch.mkdir()

    def image(self, relative: str, content: bytes = b"synthetic source bytes") -> Path:
        path = self.batch / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def manifest(self, orders: list[dict], schema: str = "prepare-reimbursements.taobao-normal.v1") -> Path:
        path = self.batch / "generated" / "reimbursement-manifest.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"schema": schema, "orders": orders}), encoding="utf-8")
        return path

    def overrides(self, evidence: dict) -> Path:
        path = self.batch / "generated" / "ocr" / "evidence-overrides.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"schema": OVERRIDES_SCHEMA, "evidence": evidence}), encoding="utf-8")
        return path

    def order(self, evidence: list[dict], **values: object) -> dict:
        return {"source": "taobao", "order_no": "SYNTHETIC-ORDER", "date": "2040-01-02",
                "amount_rmb": "12.34", "payment_amount": "13.57", "payment_currency": "HKD",
                "alipay_trade_no": "SYNTHETIC-TRADE", "evidence": evidence, **values}

    def test_canonical_sources_only_and_comparison_values_are_not_ocr_inputs(self) -> None:
        source = self.image("物品/taobao/source.png")
        derived = self.image("generated/print-flat/all/copy.png")
        raw = self.image("物品/taobao/_raw_payment_screenshots/copy.png")
        pdf = self.image("物品/vendor/receipt.pdf")
        self.manifest([self.order([
            {"kind": "taobao_order_detail_screenshot", "relative_path": "物品\\taobao\\source.png"},
            {"kind": "taobao_order_detail_screenshot", "actual_path": str(source)},
            *[{"kind": "taobao_order_detail_screenshot", "actual_path": str(path)} for path in (derived, raw, pdf)],
        ])])
        before = source.read_bytes()
        result = build_jobs(self.batch)
        self.assertEqual(result["schema"], BUILD_SCHEMA)
        self.assertEqual(result["job_manifest"]["schema"], JOB_MANIFEST_SCHEMA)
        self.assertEqual(len(result["job_manifest"]["jobs"]), 1)
        job = result["job_manifest"]["jobs"][0]
        self.assertEqual(job["schema"], JOB_SCHEMA)
        self.assertEqual(job["source_path"], str(source.resolve()))
        self.assertEqual(job["requested_profile"], "taobao_order_detail")
        self.assertTrue(all(isinstance(field, str) for field in job["expected_fields"]))
        encoded = json.dumps(result["job_manifest"])
        self.assertNotIn("12.34", encoded)
        self.assertNotIn("SYNTHETIC-TRADE", encoded)
        self.assertEqual(result["comparison_records"][0]["expected_facts"]["amount"], "12.34")
        self.assertEqual(result["comparison_records"][0]["expected_facts"]["order_date"], "2040-01-02")
        self.assertNotIn("paid_date", result["comparison_records"][0]["expected_facts"])
        self.assertIn("order_date", job["expected_fields"])
        self.assertNotIn("paid_date", job["expected_fields"])
        self.assertEqual({item["reason"] for item in result["excluded"]}, {"derived_or_noncanonical_path", "direct_pdf_path"})
        self.assertEqual(source.read_bytes(), before)
        self.assertEqual(result, build_jobs(self.batch))

    def test_payment_uses_recorded_debit_and_missing_debit_is_not_inferred(self) -> None:
        self.image("物品/payment.png")
        evidence = [{"kind": "payment_record_screenshot", "relative_path": "物品/payment.png"}]
        self.manifest([self.order(evidence)])
        facts = build_jobs(self.batch)["comparison_records"][0]["expected_facts"]
        self.assertEqual(facts["amount"], "13.57")
        self.assertEqual(facts["currency"], "HKD")
        self.assertNotIn("date", facts)
        self.assertNotIn("paid_date", facts)
        self.manifest([self.order(evidence, payment_amount=None, payment_currency=None)])
        facts = build_jobs(self.batch)["comparison_records"][0]["expected_facts"]
        self.assertNotIn("amount", facts)
        self.assertNotIn("currency", facts)
        self.manifest([self.order(evidence, payment_date="2040-01-03")])
        result = build_jobs(self.batch)
        self.assertEqual(result["comparison_records"][0]["expected_facts"]["paid_date"], "2040-01-03")
        self.assertIn("paid_date", result["job_manifest"]["jobs"][0]["expected_fields"])
        self.assertNotIn("order_date", result["comparison_records"][0]["expected_facts"])

    def test_early_discovery_requires_explicit_profile_not_filename_guess(self) -> None:
        self.image("差旅/didi_mtr_alipay_approval.png")
        self.image("差旅/_raw/ignored.png")
        self.image("generated/ocr/visualization.png")
        result = build_jobs(self.batch)
        self.assertEqual(result["job_manifest"]["jobs"][0]["requested_profile"], "generic")
        self.assertEqual(result["comparison_records"][0]["toolkit_status"], "unsupported")
        self.assertEqual(len(result["unsupported"]), 1)
        self.assertEqual(result["unsupported"][0]["reason"], "profile_required")
        self.overrides({"差旅/didi_mtr_alipay_approval.png": {
            "requested_profile": "ride_payment", "business_context": {"provider": "didi", "currency": "CNY", "amount": "98.76"},
            "expected_facts": {"amount": "98.76", "transaction_count": 2},
        }})
        result = build_jobs(self.batch)
        job = result["job_manifest"]["jobs"][0]
        self.assertEqual(job["requested_profile"], "ride_payment")
        self.assertEqual(job["business_context"], {"provider": "didi", "currency": "CNY"})
        self.assertNotIn("98.76", json.dumps(job))
        self.assertEqual(result["comparison_records"][0]["expected_facts"]["transaction_count"], 2)

    def test_travel_state_provider_and_approval_type_are_explicit(self) -> None:
        self.image("差旅/a.png")
        self.image("差旅/b.png")
        path = self.batch / "generated" / "travel-reimbursement-manifest.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"schema": "prepare-reimbursements.travel.v1", "expenses": [
            {"date": "2040-01-02", "amount": 999, "currency": "HKD"}], "evidence": [
            {"kind": "travel_approve", "relative_path": "差旅/a.png"},
            {"kind": "travel_payment_screenshot", "relative_path": "差旅/b.png", "details": {
                "business_context": {"provider": "octopus", "currency": "HKD"}}},
        ]}), encoding="utf-8")
        result = build_jobs(self.batch)
        self.assertEqual([job["requested_profile"] for job in result["job_manifest"]["jobs"]], ["travel_approval", "transit_payment"])
        self.assertEqual(result["job_manifest"]["jobs"][0]["expected_fields"], ["approvals"])
        self.assertTrue(all(not record["expected_facts"] for record in result["comparison_records"]))

    def test_sqlite_is_preferred_without_modifying_it_or_source_evidence(self) -> None:
        source = self.image("物品/db.png")
        self.image("物品/manifest.png")
        self.manifest([self.order([{"kind": "taobao_order_detail_screenshot", "relative_path": "物品/manifest.png"}])])
        database = self.batch / "generated" / "reimbursement-state.sqlite3"
        connection = sqlite3.connect(database)
        connection.executescript("""
            PRAGMA user_version=4;
            CREATE TABLE batches(id INTEGER PRIMARY KEY,batch_folder TEXT);
            CREATE TABLE orders(id INTEGER PRIMARY KEY,batch_id INTEGER,source_order_index INTEGER,
                                source TEXT,order_no TEXT,amount_rmb REAL,raw_json TEXT);
            CREATE TABLE evidence_files(id INTEGER PRIMARY KEY,order_id INTEGER,evidence_kind TEXT,
                                        relative_path TEXT,sha256 TEXT,details_json TEXT);
        """)
        connection.execute("INSERT INTO batches VALUES(1,?)", (str(self.batch),))
        connection.execute("INSERT INTO orders VALUES(1,1,1,'xianyu','SYNTHETIC-ORDER',12.34,'{}')")
        connection.execute("INSERT INTO evidence_files VALUES(1,1,'taobao_order_detail_screenshot',?,'obsolete','{}')", ("物品/db.png",))
        connection.commit()
        connection.close()
        before = database.read_bytes()
        result = build_jobs(self.batch)
        self.assertEqual(len(result["job_manifest"]["jobs"]), 1)
        self.assertEqual(result["job_manifest"]["jobs"][0]["source_path"], str(source))
        self.assertEqual(result["job_manifest"]["jobs"][0]["requested_profile"], "xianyu_order_detail")
        self.assertTrue(result["comparison_records"][0]["source_hash_changed"])
        self.assertEqual(database.read_bytes(), before)

    def test_missing_engine_or_unreadable_database_does_not_block_manifest_jobs(self) -> None:
        self.image("物品/source.png")
        self.manifest([self.order([{"kind": "taobao_order_detail_screenshot", "relative_path": "物品/source.png"}])])
        self.image("generated/reimbursement-state.sqlite3", b"not a database")
        result = build_jobs(self.batch)
        self.assertEqual(len(result["job_manifest"]["jobs"]), 1)
        self.assertEqual(result["diagnostics"][0]["reason"], "state_unavailable")

    def test_unsupported_manifest_and_malformed_override_fail_open(self) -> None:
        self.image("差旅/source.png")
        self.manifest([], schema="unknown.v99")
        self.overrides({"差旅/source.png": {"requested_profile": ["ride_payment"]}})
        result = build_jobs(self.batch)
        self.assertEqual(result["job_manifest"]["jobs"][0]["requested_profile"], "generic")
        self.assertEqual(result["diagnostics"][0]["reason"], "manifest_unavailable")
        self.assertEqual(result["unsupported"][0]["reason"], "unsupported_profile")

    def test_override_cannot_reintroduce_print_copies_or_escape_batch(self) -> None:
        self.image("generated/print-flat/copy.png")
        sibling = self.batch.parent / "outside.png"
        sibling.write_bytes(b"unrelated")
        self.overrides({"generated/print-flat/copy.png": {"requested_profile": "ride_payment"},
                        "../outside.png": {"requested_profile": "ride_payment"}})
        result = build_jobs(self.batch)
        self.assertFalse(result["job_manifest"]["jobs"])
        self.assertEqual({row["reason"] for row in result["excluded"]}, {"outside_batch", "derived_or_noncanonical_path"})

    def test_source_hash_is_recomputed_while_id_remains_stable(self) -> None:
        source = self.image("差旅/source.png")
        self.overrides({"差旅/source.png": {"requested_profile": "ride_payment"}})
        first = build_jobs(self.batch)["comparison_records"][0]
        source.write_bytes(b"a changed screenshot")
        second = build_jobs(self.batch)["comparison_records"][0]
        self.assertEqual(first["evidence_id"], second["evidence_id"])
        self.assertNotEqual(first["source_sha256"], second["source_sha256"])
        self.assertEqual(second["source_sha256"], hashlib.sha256(source.read_bytes()).hexdigest())

    def test_expected_fields_override_is_explicit_and_invalid_values_are_diagnostic(self) -> None:
        self.image("差旅/source.png")
        self.overrides({"差旅/source.png": {"requested_profile": "travel_approval", "expected_fields": ["approvals"]}})
        result = build_jobs(self.batch)
        self.assertEqual(result["job_manifest"]["jobs"][0]["expected_fields"], ["approvals"])
        self.overrides({"差旅/source.png": {"requested_profile": "travel_approval", "expected_fields": {"amount": 123}}})
        result = build_jobs(self.batch)
        self.assertEqual(result["job_manifest"]["jobs"][0]["expected_fields"], ["approvals"])
        self.assertEqual(result["diagnostics"][0]["reason"], "invalid_expected_fields_override")

    def test_blank_order_number_is_not_reimbursable_and_outputs_are_private(self) -> None:
        source = self.image("物品/source.png")
        self.manifest([self.order([{"kind": "taobao_order_detail_screenshot", "relative_path": "物品/source.png"}], order_no="")])
        result = build_jobs(self.batch)
        self.assertFalse(result["job_manifest"]["jobs"])
        self.assertEqual(result["excluded"][0]["reason"], "not_reimbursable_blank_order")
        self.overrides({"物品/source.png": {"requested_profile": "vendor_receipt", "expected_facts": {"amount": "12.34"}}})
        self.assertFalse(build_jobs(self.batch)["job_manifest"]["jobs"])
        self.manifest([self.order([{"kind": "receipt_image", "relative_path": "物品/source.png"}])])
        result = build_jobs(self.batch)
        paths = write_jobs(result, self.batch)
        self.assertTrue(all(Path(value).is_relative_to(self.batch / "generated" / "ocr") for value in paths.values()))
        manifest = json.loads(Path(paths["manifest"]).read_text(encoding="utf-8"))
        sidecar = json.loads(Path(paths["comparisons"]).read_text(encoding="utf-8"))
        self.assertNotIn("12.34", json.dumps(manifest))
        self.assertEqual(sidecar["comparison_records"][0]["expected_facts"]["amount"], "12.34")
        self.assertEqual(source.read_bytes(), b"synthetic source bytes")
        self.assertFalse(list((self.batch / "generated" / "ocr").glob("*.tmp")))

    def test_new_unsynced_images_are_ocr_first_without_readding_archives_or_copies(self) -> None:
        self.image("物品/recorded.png", b"canonical screenshot")
        self.image("物品/legitimate-second-record.png", b"canonical screenshot")
        self.manifest([self.order([
            {"kind": "taobao_order_detail_screenshot", "relative_path": "物品/recorded.png"},
            {"kind": "payment_record_screenshot", "relative_path": "物品/legitimate-second-record.png"},
        ])])
        self.image("物品/unsynced-new.png", b"new unreviewed screenshot")
        self.image("物品/copied-source.png", b"canonical screenshot")
        self.image("物品/_previous_payment_screenshots/old.png", b"old screenshot")
        result = build_jobs(self.batch)
        self.assertEqual(len(result["job_manifest"]["jobs"]), 3)
        self.assertEqual(sum(job["requested_profile"] == "generic" for job in result["job_manifest"]["jobs"]), 1)
        self.assertEqual(result["unsupported"][0]["source_path"], "物品/unsynced-new.png")
        self.assertIn("duplicate_of_canonical_source", {row["reason"] for row in result["excluded"]})


if __name__ == "__main__":
    unittest.main()
