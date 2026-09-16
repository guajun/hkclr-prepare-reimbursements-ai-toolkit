"""Synthetic process-contract regressions; no model, real evidence, or network."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
from prepare_reimbursements import ocr_runner as runner
from prepare_reimbursements.ocr_jobs import COMPARISON_SCHEMA


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


class FakeCLI:
    def __init__(self):
        self.commands = []
        self.change = None
        self.on_doctor = None
        self.fail = None
        self.runtime = {"python": "synthetic", "rapidocr": "synthetic", "onnxruntime": "synthetic"}
        self.raw_cache = False

    def __call__(self, command, *, timeout, project):
        self.commands.append(command)
        if "doctor" in command:
            if self.on_doctor:
                self.on_doctor()
            if self.fail == "timeout":
                raise runner.BridgeFailure("command_timeout")
            if self.fail == "doctor":
                return 1, "private failure text"
            return 0, json.dumps({"initialization": "ok" if "--initialize" in command else "not_requested",
                                   **self.runtime})
        if self.fail == "run":
            return 1, "private failure text"
        manifest = Path(command[command.index("hkclr-ocr") + 2])
        output = Path(command[command.index("--output") + 1])
        request = json.loads(manifest.read_text(encoding="utf-8"))
        configuration = {"schema": runner.EXTERNAL_SCHEMAS["configuration"],
                         "minimum_score": request.get("configuration", {}).get("minimum_score", 0.5),
                         "adapter_registry_version": "synthetic-v1"}
        rows = []
        raw_hits = 0
        dry_run = "--dry-run" in command
        for job in request["jobs"]:
            source = Path(job["source_path"])
            source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
            cache_key = runner._identity({"job": job, "hash": source_hash, "configuration": configuration})
            result_path = output / "objects" / f"{cache_key}.ocr.json"
            fields = {"amount": {"value": "15.00", "confidence": 0.99, "source_box": [1, 2, 3, 4]},
                      "currency": {"value": "CNY", "confidence": 0.99, "source_box": [1, 2, 3, 4]}}
            support = {"requested": job["requested_profile"], "resolved": job["requested_profile"],
                       "status": "supported", "check": "pass", "adapter": "synthetic-v1"}
            payload = {"schema": runner.EXTERNAL_SCHEMAS["result"], "schemas": runner.EXTERNAL_SCHEMAS,
                       "configuration": configuration, "job": job,
                       "source": {"path": str(source), "sha256": source_hash},
                       "ocr": {"status": "ok", "engine": {"name": "fake"}},
                       "profile_support": support, "extracted_fields": fields,
                       "transactions": [], "candidate_totals": [], "adapter_warnings": [],
                       "full_text": "private raw text must not enter the wrapper summary", "lines": [{"text": "private"}]}
            if self.raw_cache and not dry_run:
                engine = {"name": "fake", "model": "fake-default", "rapidocr": self.runtime["rapidocr"],
                          "onnxruntime": self.runtime["onnxruntime"]}
                raw_identity = {"schema": "hkclr.rapidocr.raw-cache.v1", "source_sha256": source_hash, "engine": engine}
                raw_key = hashlib.sha256(json.dumps(raw_identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                raw_path = output / "raw-objects" / f"{raw_key}.ocr.json"
                raw_hits += int(raw_path.exists() and not result_path.exists())
                lines = [{"text": "synthetic OCR text", "score": 0.99, "box": [1, 2, 3, 4]}]
                if not raw_path.exists():
                    write_json(raw_path, {**raw_identity, "raw": {"lines": lines}})
                payload["ocr"].update(engine={"name": "fake", "model": "fake-default"}, raw_cache_key=raw_key)
                payload["lines"] = lines
            row = {"schema": "hkclr.rapidocr.run-record.v2", "evidence_id": job["evidence_id"],
                   "source_path": str(source), "source_sha256": source_hash,
                   "requested_profile": job["requested_profile"], "cache_key": cache_key,
                   "result_path": str(result_path)}
            if dry_run:
                row.update(status="dry_run", ocr_status="not_run", profile_support_status="not_evaluated")
            else:
                row.update(status="cached" if result_path.is_file() else "ok", ocr_status="ok",
                           profile_support_status="supported", profile_check="pass", extracted_fields=fields,
                           transactions=[], candidate_totals=[], adapter_warnings=[])
            if self.change:
                self.change(row, payload)
            if not dry_run:
                write_json(result_path, payload)
            rows.append(row)
        (output / "ocr-manifest.jsonl").write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
        summary = {"schema": "hkclr.rapidocr.summary.v2", "schemas": runner.EXTERNAL_SCHEMAS,
                   "configuration": configuration, "dry_run": dry_run, "image_path_count": len(rows),
                   "unique_image_count": len({row["source_sha256"] for row in rows}),
                   "processed": sum(row["status"] == "ok" for row in rows),
                   "cache_hits": sum(row["status"] == "cached" for row in rows), "errors": 0}
        summary["raw_cache_hits"] = raw_hits
        write_json(output / "ocr-summary.json", summary)
        return 0, "private output must not enter summary"


class ExternalOCRTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ocr-wrapper-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.project = self.root / "external project"
        self.project.mkdir()
        (self.project / "pyproject.toml").write_text("# synthetic", encoding="utf-8")
        self.folder = self.root / "batch with spaces 中文"
        self.folder.mkdir()
        self.source = self.folder / "evidence.png"
        self.source.write_bytes(b"synthetic source bytes")
        self.manifest = self.folder / "generated" / "ocr" / "jobs.json"
        self.job = {"schema": runner.EXTERNAL_SCHEMAS["job"], "evidence_id": "fixture-001",
                    "source_path": str(self.source), "requested_profile": "ride_payment",
                    "expected_fields": ["amount"], "business_context": {"currency": "CNY"}}
        self.request = {"schema": runner.MANIFEST_SCHEMA, "jobs": [self.job]}
        write_json(self.manifest, self.request)
        self.fake = FakeCLI()
        self.environment = patch.dict(os.environ, {runner.ENV_NAME: str(self.project)})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.execution = patch.object(runner, "_execute", self.fake)
        self.execution.start()
        self.addCleanup(self.execution.stop)

    def run_bridge(self, **kwargs):
        return runner.run_external_ocr(self.folder, jobs_manifest=self.manifest, **kwargs)

    def comparison(self, **changes):
        return {"evidence_id": "fixture-001", "source_sha256": hashlib.sha256(self.source.read_bytes()).hexdigest(),
                "source_path": str(self.source), "profile": "ride_payment", "expected_facts": {}, **changes}

    def comparisons(self, records, schema=COMPARISON_SCHEMA):
        write_json(self.manifest.parent / "comparisons.json", {"schema": schema, "comparison_records": records})

    def test_valid_run_preserves_candidates_without_claiming_business_acceptance(self):
        before = self.source.read_bytes()
        result = self.run_bridge()
        self.assertTrue(result["available"])
        self.assertEqual(result["records"][0]["status"], "pass")
        self.assertFalse(result["records"][0]["business_validated"])
        self.assertEqual(result["records"][0]["extracted_fields"]["amount"]["value"], "15.00")
        self.assertEqual(self.source.read_bytes(), before)
        self.assertNotIn("private", json.dumps(result))
        self.assertNotIn("full_text", json.dumps(result))
        self.assertEqual(self.fake.commands[0], ["uv", "run", "--no-sync", "--project", str(self.project), "hkclr-ocr", "doctor", "--initialize"])
        self.assertIn(str(self.manifest.parent / "rapidocr" / "runs" / result["run_id"] / "jobs.json"), self.fake.commands[1])

    def test_dry_run_is_available_but_not_an_extraction_pass(self):
        result = self.run_bridge(dry_run=True)
        self.assertTrue(result["available"])
        self.assertEqual(result["counts"], {"available": 1})
        self.assertEqual(result["records"][0]["reason"], "dry_run_only")
        self.assertNotIn("--initialize", self.fake.commands[0])
        self.assertFalse((Path(result["run_dir"]) / "objects").exists())

    def test_missing_environment_is_read_only_and_user_fallback_works(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(runner, "_read_user_environment", return_value=None):
            result = runner.run_external_ocr(self.folder)
        self.assertEqual(result["reason"], "project_not_configured")
        self.assertEqual(self.fake.commands, [])
        self.assertFalse((self.manifest.parent / "rapidocr").exists())
        with patch.dict(os.environ, {}, clear=True), patch.object(runner, "_read_user_environment", return_value=str(self.project)):
            result = self.run_bridge(dry_run=True)
        self.assertTrue(result["available"])
        self.assertEqual(result["configuration_source"], "user")

    def test_explicit_process_configuration_wins_over_user_value(self):
        with patch.dict(os.environ, {runner.ENV_NAME: ""}), patch.object(runner, "_read_user_environment") as read_user:
            result = self.run_bridge()
        self.assertEqual(result["reason"], "project_not_configured")
        read_user.assert_not_called()

    def test_lock_excludes_concurrent_run_in_same_directory(self):
        observed = []
        self.fake.on_doctor = lambda: observed.append(self.run_bridge())
        result = self.run_bridge()
        self.assertTrue(result["available"])
        self.assertEqual(observed[0]["reason"], "output_locked")
        self.assertFalse((self.manifest.parent / "rapidocr" / ".external-ocr.lock").exists())

    def test_timeout_failure_is_soft_and_does_not_consume_previous_success(self):
        first = self.run_bridge()
        self.fake.fail = "timeout"
        failed = self.run_bridge()
        self.assertEqual(failed["reason"], "command_timeout")
        self.assertFalse(failed["available"])
        self.assertEqual(failed["records"], [])
        self.assertNotEqual(first["run_dir"], failed["run_dir"])
        self.assertNotIn("private", json.dumps(failed))

    def test_failed_cli_output_is_not_accepted_or_leaked(self):
        for failure in ("doctor", "run"):
            with self.subTest(failure=failure):
                self.fake.fail = failure
                result = self.run_bridge()
                self.assertFalse(result["available"])
                self.assertEqual(result["reason"], f"{failure}_failed")
                self.assertEqual(result["records"], [])
                self.assertNotIn("private failure", json.dumps(result))

    def test_unsupported_or_warning_result_requires_review(self):
        def unsupported(row, payload):
            row.update(profile_support_status="unsupported", profile_check="unsupported")
            payload["profile_support"].update(status="unsupported", check="unsupported")
        self.fake.change = unsupported
        result = self.run_bridge()
        self.assertEqual(result["counts"], {"review": 1})
        self.assertFalse(result["records"][0]["business_validated"])

    def test_result_schema_identity_and_hash_tampering_are_rejected(self):
        mutations = {
            "schema": lambda row, payload: payload.update(schema="unknown.v9"),
            "identity": lambda row, payload: payload["job"].update(evidence_id="wrong"),
            "hash": lambda row, payload: row.update(source_sha256="f" * 64),
            "result_hash": lambda row, payload: payload["source"].update(sha256="f" * 64),
            "duplicate": lambda row, payload: row.update(evidence_id="unknown"),
            "result_path": lambda row, payload: row.update(result_path=str(self.source)),
            "source_changed": lambda row, payload: self.source.write_bytes(b"changed after inference"),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                self.source.write_bytes(b"synthetic source bytes")
                self.fake.change = mutate
                result = self.run_bridge()
                self.assertFalse(result["available"])
                self.assertEqual(result["records"], [])

    def test_matching_cache_reused_and_changed_job_source_or_config_is_not(self):
        first = self.run_bridge()
        second = self.run_bridge()
        self.assertEqual(second["seeded_cache_objects"], 1)
        self.assertEqual(second["external_counts"]["cache_hits"], 1)
        self.assertNotEqual(first["run_dir"], second["run_dir"])
        self.request["jobs"][0]["expected_fields"].append("currency")
        write_json(self.manifest, self.request)
        changed_job = self.run_bridge()
        self.assertEqual(changed_job["seeded_cache_objects"], 0)
        self.source.write_bytes(b"new pixels")
        changed_source = self.run_bridge()
        self.assertEqual(changed_source["seeded_cache_objects"], 0)
        self.request["configuration"] = {"schema": runner.EXTERNAL_SCHEMAS["configuration"], "minimum_score": 0.9}
        write_json(self.manifest, self.request)
        changed_config = self.run_bridge()
        self.assertEqual(changed_config["seeded_cache_objects"], 0)

    def test_invalid_input_and_output_locations_do_not_start_cli(self):
        self.assertEqual(self.run_bridge(output_dir=self.folder)["reason"], "output_outside_private_ocr_directory")
        for change in ({"schema": "unknown"}, {"jobs": [self.job, self.job]}):
            write_json(self.manifest, dict(self.request, **change))
            self.assertFalse(self.run_bridge()["available"])
        self.assertEqual(self.fake.commands, [])

    def test_comparison_hash_mismatch_requires_regeneration(self):
        self.comparisons([self.comparison(source_sha256="f" * 64)])
        result = self.run_bridge()
        self.assertEqual(result["reason"], "comparison_source_mismatch")
        self.assertEqual(self.fake.commands, [])

    def test_comparison_sidecar_binds_schema_unique_id_path_and_profile(self):
        self.comparisons([self.comparison()])
        self.assertTrue(self.run_bridge()["available"])
        cases = [([self.comparison()], "unknown.v99"),
                 ([self.comparison(), self.comparison()], COMPARISON_SCHEMA),
                 ([self.comparison(profile="travel_approval")], COMPARISON_SCHEMA),
                 ([self.comparison(source_path=str(self.folder / "other.png"))], COMPARISON_SCHEMA),
                 ([self.comparison(expected_facts=[])], COMPARISON_SCHEMA)]
        for records, schema in cases:
            with self.subTest(schema=schema, records=records):
                self.comparisons(records, schema=schema)
                self.assertFalse(self.run_bridge()["available"])

    def test_runtime_upgrade_never_seeds_previous_inference(self):
        self.assertTrue(self.run_bridge()["available"])
        self.fake.runtime["rapidocr"] = "new-synthetic-version"
        result = self.run_bridge()
        self.assertTrue(result["available"])
        self.assertEqual(result["seeded_cache_objects"], 0)
        self.assertEqual(result["external_counts"]["cache_hits"], 0)

    def test_raw_cache_can_rerun_changed_adapter_without_old_typed_result(self):
        self.fake.raw_cache = True
        first = self.run_bridge()
        self.assertIn("raw_cache_sha256", first["records"][0])
        self.request["jobs"][0]["expected_fields"].append("currency")
        write_json(self.manifest, self.request)
        second = self.run_bridge()
        self.assertEqual(second["seeded_cache_objects"], 0)
        self.assertEqual(second["seeded_raw_cache_objects"], 1)
        self.assertEqual(second["external_counts"]["raw_cache_hits"], 1)
        raw_path = Path(second["records"][0]["raw_cache_path"])
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
        raw["raw"]["lines"][0]["text"] = "tampered"
        write_json(raw_path, raw)
        third = self.run_bridge()
        self.assertEqual(third["seeded_raw_cache_objects"], 0)

    def test_explicit_manifest_cannot_admit_noncanonical_or_duplicate_source_files(self):
        for relative in ("raw/source.png", "_previous_payment_screenshots/source.png", "receipt.pdf"):
            with self.subTest(relative=relative):
                path = self.folder / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"synthetic source")
                write_json(self.manifest, {"schema": runner.MANIFEST_SCHEMA,
                                         "jobs": [dict(self.job, source_path=str(path))]})
                self.assertFalse(self.run_bridge()["available"])
        write_json(self.manifest, {"schema": runner.MANIFEST_SCHEMA,
                                  "jobs": [self.job, dict(self.job, evidence_id="same-source-another-id")]})
        self.assertEqual(self.run_bridge()["reason"], "duplicate_source_path")
        self.assertEqual(self.fake.commands, [])

    def test_generated_symlinks_cannot_hide_input_or_redirect_output(self):
        alias = self.manifest.parent / "alias.png"
        try:
            alias.symlink_to(self.source)
        except OSError:
            self.skipTest("Creating symlinks requires an unavailable platform privilege")
        write_json(self.manifest, {"schema": runner.MANIFEST_SCHEMA,
                                  "jobs": [dict(self.job, source_path=str(alias))]})
        self.assertEqual(self.run_bridge()["reason"], "derived_source_not_allowed")
        fresh = self.root / "symlink-batch"
        fresh.mkdir()
        target = self.root / "unrelated-output"
        target.mkdir()
        (fresh / "generated").symlink_to(target, target_is_directory=True)
        result = runner.run_external_ocr(fresh)
        self.assertEqual(result["reason"], "output_outside_private_ocr_directory")
        self.assertEqual(list(target.iterdir()), [])

    def test_corrupted_prior_cache_object_is_ignored(self):
        first = self.run_bridge()
        Path(first["records"][0]["result_path"]).write_text("{}", encoding="utf-8")
        second = self.run_bridge()
        self.assertTrue(second["available"])
        self.assertEqual(second["seeded_cache_objects"], 0)


class ProcessExecutionTests(unittest.TestCase):
    def test_argument_list_passes_metacharacters_without_a_shell(self):
        with tempfile.TemporaryDirectory(prefix="ocr-process-") as directory:
            payload = 'spaces $() ; & " untouched 中文'
            code, stdout = runner._execute([sys.executable, "-c", "import sys; print(sys.argv[1])", payload],
                                           timeout=10, project=Path(directory))
        self.assertEqual(code, 0)
        self.assertEqual(stdout.strip(), payload)

    def test_real_subprocess_timeout_is_bounded(self):
        with tempfile.TemporaryDirectory(prefix="ocr-process-") as directory:
            with self.assertRaisesRegex(runner.BridgeFailure, "command_timeout"):
                runner._execute([sys.executable, "-c", "import time; time.sleep(20)"],
                                timeout=0.05, project=Path(directory))


if __name__ == "__main__":
    unittest.main()
