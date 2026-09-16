"""Fail-open, process-only bridge to the explicitly configured external OCR CLI.

An extraction ``pass`` is not a reimbursement decision. This module never writes
business state or changes evidence, and never imports the external project.
"""

from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
from typing import Any
import uuid

ENV_NAME = "HKCLR_RAPIDOCR_PROJECT"
SUMMARY_SCHEMA = "prepare-reimbursements.external-ocr-run.v1"
EXTERNAL_SCHEMAS = {
    "job": "hkclr.rapidocr.job.v1",
    "configuration": "hkclr.rapidocr.config.v1",
    "adapter": "hkclr.rapidocr.adapter.v1",
    "result": "hkclr.rapidocr.result.v2",
}
MANIFEST_SCHEMA = "hkclr.rapidocr.job-manifest.v1"
SUMMARY_NAME = "external-ocr-run-summary.json"
PROFILES = {
    "taobao_order_detail", "xianyu_order_detail", "alipay_payment_detail",
    "vendor_receipt", "travel_approval", "ride_payment", "transit_payment",
    "auto", "generic", "taobao", "alipay",
}


class BridgeFailure(Exception):
    """A soft failure with a compact, non-evidence-bearing reason code."""


def _read_user_environment(name: str) -> str | None:
    if os.name != "nt":
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, kind = winreg.QueryValueEx(key, name)
        if isinstance(value, str):
            return os.path.expandvars(value) if kind == winreg.REG_EXPAND_SZ else value
    except OSError:
        pass
    return None


def discover_project() -> tuple[Path | None, str, str | None]:
    """Read process configuration, then Windows User configuration if absent.

    An explicitly empty process variable disables the bridge. A malformed process
    value does not silently fall back to a different project.
    """
    raw = os.environ.get(ENV_NAME)
    origin = "process"
    if raw is None:
        raw = _read_user_environment(ENV_NAME)
        origin = "user" if raw is not None else "unset"
    if not raw or not raw.strip():
        return None, origin, "project_not_configured"
    project = Path(raw.strip()).expanduser()
    if not project.is_absolute() or not project.is_dir() or not (project / "pyproject.toml").is_file():
        return None, origin, "project_not_available"
    return project.resolve(), origin, None


def _hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _identity(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise BridgeFailure("output_not_object")
    return payload


@contextmanager
def _output_lock(output: Path):
    """Exclusive creation deliberately leaves crash recovery to the caller."""
    output.mkdir(parents=True, exist_ok=True)
    lock = output / ".external-ocr.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as error:
        raise BridgeFailure("output_locked") from error
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump({"pid": os.getpid(), "created_at": _now()}, stream)
        yield
    finally:
        lock.unlink(missing_ok=True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _execute(command: list[str], *, timeout: float, project: Path) -> tuple[int, str]:
    """Bound the complete subprocess tree, including uv's Python child."""
    environment = os.environ.copy()
    environment.update({ENV_NAME: str(project), "UV_OFFLINE": "1", "UV_PYTHON_DOWNLOADS": "never",
                        "PYTHONIOENCODING": "utf-8"})
    options: dict[str, Any] = {"start_new_session": True} if os.name != "nt" else {
        "creationflags": subprocess.CREATE_NO_WINDOW,
    }
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               text=True, encoding="utf-8", errors="replace", env=environment,
                               cwd=str(project), **options)
    try:
        stdout, _ = process.communicate(timeout=timeout)
        return process.returncode, stdout
    except subprocess.TimeoutExpired as error:
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=subprocess.CREATE_NO_WINDOW, timeout=10, check=False)
            else:
                os.killpg(process.pid, signal.SIGKILL)
        finally:
            process.kill()
            process.communicate(timeout=10)
        raise BridgeFailure("command_timeout") from error


def _validate_jobs(payload: dict[str, Any], folder: Path) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    from .ocr_jobs import _input_is_derived, _path_reason

    if payload.get("schema") != MANIFEST_SCHEMA:
        raise BridgeFailure("unsupported_input_schema")
    configuration = payload.get("configuration", {"schema": EXTERNAL_SCHEMAS["configuration"]})
    if not isinstance(configuration, dict) or configuration.get("schema") != EXTERNAL_SCHEMAS["configuration"]:
        raise BridgeFailure("unsupported_configuration_schema")
    score = configuration.get("minimum_score", 0.5)
    if isinstance(score, bool) or not isinstance(score, (float, int)) or not math.isfinite(score) or not 0 <= score <= 1:
        raise BridgeFailure("invalid_minimum_score")
    jobs = payload.get("jobs")
    if not isinstance(jobs, list):
        raise BridgeFailure("invalid_jobs")
    by_id: dict[str, dict[str, Any]] = {}
    hashes: dict[str, str] = {}
    seen_paths: set[Path] = set()
    for job in jobs:
        if not isinstance(job, dict) or job.get("schema") != EXTERNAL_SCHEMAS["job"]:
            raise BridgeFailure("unsupported_job_schema")
        evidence_id = job.get("evidence_id")
        if not isinstance(evidence_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", evidence_id) or evidence_id in by_id:
            raise BridgeFailure("invalid_evidence_identity")
        path_text = job.get("source_path")
        if not isinstance(path_text, str) or not Path(path_text).is_absolute():
            raise BridgeFailure("invalid_source_path")
        path = Path(path_text).resolve()
        if _input_is_derived(folder, path_text):
            raise BridgeFailure("derived_source_not_allowed")
        reason = _path_reason(folder, path)
        if reason:
            raise BridgeFailure("derived_source_not_allowed" if reason == "derived_or_noncanonical_path" else "source_not_available")
        if path in seen_paths:
            raise BridgeFailure("duplicate_source_path")
        seen_paths.add(path)
        if job.get("requested_profile") not in PROFILES:
            raise BridgeFailure("unsupported_requested_profile")
        fields = job.get("expected_fields")
        if not isinstance(fields, list) or any(not isinstance(item, str) or not item.strip() for item in fields) or len(fields) != len(set(fields)):
            raise BridgeFailure("invalid_expected_fields")
        if not isinstance(job.get("business_context", {}), dict):
            raise BridgeFailure("invalid_business_context")
        normalized = {"schema": EXTERNAL_SCHEMAS["job"], "evidence_id": evidence_id,
            "source_path": str(path), "requested_profile": job["requested_profile"],
            "expected_fields": fields, "business_context": job.get("business_context", {})}
        by_id[evidence_id] = normalized
        hashes[evidence_id] = _hash(path)
    return by_id, hashes


def _validate_comparisons(payload: Any, jobs: dict[str, dict[str, Any]], hashes: dict[str, str]) -> list[dict[str, Any]]:
    from .ocr_jobs import COMPARISON_SCHEMA

    if not isinstance(payload, dict) or payload.get("schema") != COMPARISON_SCHEMA:
        raise BridgeFailure("unsupported_comparison_schema")
    records = payload.get("comparison_records")
    if not isinstance(records, list):
        raise BridgeFailure("invalid_comparison_records")
    seen: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            raise BridgeFailure("invalid_comparison_record")
        evidence_id = record.get("evidence_id")
        if not isinstance(evidence_id, str) or evidence_id not in jobs or evidence_id in seen:
            raise BridgeFailure("comparison_identity_mismatch")
        seen.add(evidence_id)
        if record.get("source_sha256") != hashes[evidence_id]:
            raise BridgeFailure("comparison_source_mismatch")
        source_path = record.get("source_path")
        if (not isinstance(source_path, str) or not Path(source_path).is_absolute()
                or Path(source_path).resolve() != Path(jobs[evidence_id]["source_path"])
                or record.get("profile") != jobs[evidence_id]["requested_profile"]):
            raise BridgeFailure("comparison_job_mismatch")
        if not isinstance(record.get("expected_facts"), dict):
            raise BridgeFailure("invalid_comparison_facts")
    return records


def _validate_versions(payload: dict[str, Any], schema: str) -> None:
    if payload.get("schema") != schema or payload.get("schemas") != EXTERNAL_SCHEMAS:
        raise BridgeFailure("unsupported_output_schema")
    configuration = payload.get("configuration")
    if not isinstance(configuration, dict) or configuration.get("schema") != EXTERNAL_SCHEMAS["configuration"]:
        raise BridgeFailure("unsupported_output_configuration")


def _object(path: Path, run_dir: Path, job: dict[str, Any], source_hash: str,
            configuration: dict[str, Any]) -> dict[str, Any]:
    path = path.resolve()
    if not path.is_relative_to((run_dir / "objects").resolve()):
        raise BridgeFailure("result_path_outside_run")
    payload = _read_json(path)
    _validate_versions(payload, EXTERNAL_SCHEMAS["result"])
    if payload.get("job") != job or payload.get("configuration") != configuration:
        raise BridgeFailure("result_job_configuration_mismatch")
    source = payload.get("source", {})
    if not isinstance(source, dict) or source.get("sha256") != source_hash or Path(source.get("path", "")).resolve() != Path(job["source_path"]):
        raise BridgeFailure("result_source_mismatch")
    support = payload.get("profile_support")
    if not isinstance(support, dict) or support.get("requested") != job["requested_profile"]:
        raise BridgeFailure("result_profile_mismatch")
    if not isinstance(payload.get("ocr"), dict) or not isinstance(payload.get("extracted_fields"), dict):
        raise BridgeFailure("malformed_result")
    if payload["ocr"].get("status") not in {"ok", "error"} or support.get("status") not in {"supported", "unsupported"} or support.get("check") not in {"pass", "review", "unsupported"}:
        raise BridgeFailure("unsupported_result_status")
    for key in ("transactions", "candidate_totals", "adapter_warnings"):
        if not isinstance(payload.get(key), list):
            raise BridgeFailure("malformed_result")
    return payload


def _raw_object(run_dir: Path, result: dict[str, Any], source_hash: str,
                runtime: dict[str, Any]) -> Path | None:
    """Bind reusable pixels-to-text output to the verified adapter object."""
    key = result["ocr"].get("raw_cache_key")
    if not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{64}", key):
        return None
    path = (run_dir / "raw-objects" / f"{key}.ocr.json").resolve()
    if not path.is_relative_to(run_dir.resolve()) or not path.is_file():
        return None
    raw = _read_json(path)
    identity = raw.get("engine")
    engine = result["ocr"].get("engine")
    if not isinstance(identity, dict) or not isinstance(engine, dict):
        return None
    expected_identity = {"name": engine.get("name"), "model": engine.get("model"),
                         "rapidocr": runtime.get("rapidocr"), "onnxruntime": runtime.get("onnxruntime")}
    key_payload = {"schema": "hkclr.rapidocr.raw-cache.v1", "source_sha256": source_hash, "engine": identity}
    expected_key = hashlib.sha256(json.dumps(key_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    if (raw.get("schema") != key_payload["schema"] or raw.get("source_sha256") != source_hash
            or identity != expected_identity or expected_key != key):
        return None
    lines = raw.get("raw", {}).get("lines") if isinstance(raw.get("raw"), dict) else None
    if not isinstance(lines, list) or any(not isinstance(line, dict) or not isinstance(line.get("text"), str)
            or isinstance(line.get("score"), bool) or not isinstance(line.get("score"), (int, float))
            or not math.isfinite(line["score"]) or not 0 <= line["score"] <= 1 for line in lines):
        return None
    threshold = result["configuration"].get("minimum_score", 0.5)
    if [line for line in lines if line["score"] >= threshold] != result.get("lines"):
        return None
    return path


def _consume(run_dir: Path, jobs: dict[str, dict[str, Any]], hashes: dict[str, str],
             *, dry_run: bool, minimum_score: float, runtime: dict[str, Any] | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    summary = _read_json(run_dir / "ocr-summary.json")
    _validate_versions(summary, "hkclr.rapidocr.summary.v2")
    if summary.get("dry_run") is not dry_run or summary.get("image_path_count") != len(jobs):
        raise BridgeFailure("output_job_count_mismatch")
    if any(type(summary.get(key)) is not int or summary[key] < 0 for key in ("image_path_count", "unique_image_count", "processed", "cache_hits", "errors")):
        raise BridgeFailure("malformed_output_counts")
    if summary["errors"] or summary["unique_image_count"] != len(set(hashes.values())):
        raise BridgeFailure("inconsistent_output_counts")
    if summary["configuration"].get("minimum_score") != minimum_score:
        raise BridgeFailure("output_configuration_mismatch")
    rows = [json.loads(line) for line in (run_dir / "ocr-manifest.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    seen: set[str] = set()
    records: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or row.get("schema") != "hkclr.rapidocr.run-record.v2":
            raise BridgeFailure("unsupported_record_schema")
        evidence_id = row.get("evidence_id")
        if not isinstance(evidence_id, str) or evidence_id not in jobs or evidence_id in seen:
            raise BridgeFailure("output_evidence_identity_mismatch")
        seen.add(evidence_id)
        job, source_hash = jobs[evidence_id], hashes[evidence_id]
        if Path(row.get("source_path", "")).resolve() != Path(job["source_path"]) or row.get("requested_profile") != job["requested_profile"]:
            raise BridgeFailure("record_source_profile_mismatch")
        if row.get("source_sha256") != source_hash or _hash(Path(job["source_path"])) != source_hash:
            raise BridgeFailure("source_hash_changed")
        record: dict[str, Any] = {"evidence_id": evidence_id, "source_path": job["source_path"],
            "source_sha256": source_hash, "requested_profile": job["requested_profile"],
            "job_identity": _identity(job), "available": True, "business_validated": False,
            "external_status": row.get("status"), "ocr_status": row.get("ocr_status"),
            "profile_support_status": row.get("profile_support_status"),
            "profile_check": row.get("profile_check"), "status": "review"}
        if dry_run:
            if row.get("status") != "dry_run" or row.get("ocr_status") != "not_run" or row.get("profile_support_status") != "not_evaluated":
                raise BridgeFailure("unexpected_dry_run_result")
            record.update(status="available", reason="dry_run_only")
        elif row.get("status") in {"ok", "cached"}:
            result_path = Path(row.get("result_path", ""))
            result = _object(result_path, run_dir, job, source_hash, summary["configuration"])
            support = result["profile_support"]
            pairs = {"ocr_status": result["ocr"]["status"], "profile_support_status": support["status"], "profile_check": support["check"]}
            if any(row.get(key) != value for key, value in pairs.items()):
                raise BridgeFailure("record_result_status_mismatch")
            for key in ("extracted_fields", "transactions", "candidate_totals", "adapter_warnings"):
                if row.get(key) != result[key]:
                    raise BridgeFailure("record_result_fields_mismatch")
                record[key] = result[key]
            key = row.get("cache_key")
            if not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{64}", key) or result_path.name != f"{key}.ocr.json":
                raise BridgeFailure("invalid_cache_identity")
            record.update(result_path=str(result_path.resolve()), result_sha256=_hash(result_path), cache_key=key,
                          profile=support.get("resolved"), engine=result["ocr"].get("engine"),
                          adapter=support.get("adapter"), elapsed_seconds=result["ocr"].get("elapsed_seconds"),
                          result_schema=result["schema"])
            try:
                raw_path = _raw_object(run_dir, result, source_hash, runtime or {})
                if raw_path:
                    record.update(raw_cache_path=str(raw_path), raw_cache_sha256=_hash(raw_path),
                                  raw_cache_key=result["ocr"]["raw_cache_key"])
            except (OSError, ValueError, TypeError, KeyError, BridgeFailure):
                pass  # Missing raw cache cannot invalidate a verified extraction.
            if record["ocr_status"] != "ok":
                record.update(status="error", reason="engine_error")
            elif support["status"] == "supported" and support["check"] == "pass" and not result["adapter_warnings"]:
                record.update(status="pass", reason="extraction_only_requires_business_comparison")
            else:
                record.update(status="review", reason="unsupported_or_incomplete_extraction")
        elif row.get("status") == "error":
            record.update(status="error", reason="evidence_ocr_error")
        else:
            raise BridgeFailure("unknown_record_status")
        records.append(record)
    if seen != set(jobs):
        raise BridgeFailure("output_missing_evidence")
    return summary, records


def _seed_cache(output: Path, run_dir: Path, jobs: dict[str, dict[str, Any]], hashes: dict[str, str],
                minimum_score: float, runtime: dict[str, Any]) -> dict[str, int]:
    """Copy only previously validated objects; the external CLI checks its key."""
    previous_path = output / SUMMARY_NAME
    copied = {"objects": 0, "raw_objects": 0}
    if not previous_path.is_file():
        return copied
    try:
        previous = _read_json(previous_path)
        previous_dir = Path(previous["run_dir"]).resolve()
        if previous.get("schema") != SUMMARY_SCHEMA or not previous.get("available") or previous.get("dry_run") or not previous_dir.is_relative_to(output / "runs"):
            return copied
        if (previous.get("runtime") != runtime
                or any(not isinstance(runtime.get(key), str) or not runtime[key]
                       for key in ("python", "rapidocr", "onnxruntime"))):
            return copied
        configuration = previous["configuration"]
        for record in previous.get("records", []):
            evidence_id = record.get("evidence_id")
            if evidence_id not in jobs or record.get("source_sha256") != hashes[evidence_id] or record.get("status") not in {"pass", "review"}:
                continue
            path = Path(record["result_path"])
            if not path.resolve().is_relative_to((previous_dir / "objects").resolve()):
                continue
            if _hash(path) != record.get("result_sha256"):
                continue
            old_job = _read_json(path).get("job")
            if (not isinstance(old_job, dict) or old_job.get("evidence_id") != evidence_id
                    or old_job.get("source_path") != jobs[evidence_id]["source_path"]):
                continue
            result = _object(path, previous_dir, old_job, hashes[evidence_id], configuration)
            raw_path = _raw_object(previous_dir, result, hashes[evidence_id], runtime)
            if raw_path:
                old_raw_hash = record.get("raw_cache_sha256")
                hash_verified = old_raw_hash and _hash(raw_path) == old_raw_hash
                # Bootstrap older summaries only at their already verified score threshold.
                bootstrap = not old_raw_hash and minimum_score >= configuration.get("minimum_score", 0.5)
                if hash_verified or bootstrap:
                    target = run_dir / "raw-objects" / raw_path.name
                    if not target.exists():
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(raw_path, target)
                        copied["raw_objects"] += 1
            if configuration.get("minimum_score") == minimum_score and old_job == jobs[evidence_id]:
                target = run_dir / "objects" / path.name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
                copied["objects"] += 1
    except (OSError, ValueError, TypeError, KeyError, BridgeFailure):
        # Cache absence, corruption, or identity changes trigger fresh inference.
        return copied
    return copied


def run_external_ocr(folder: Path, *, jobs_manifest: Path | None = None,
                     output_dir: Path | None = None, dry_run: bool = False,
                     doctor_timeout: float = 120, run_timeout: float = 600,
                     db_path: Path | None = None, overrides_path: Path | None = None) -> dict[str, Any]:
    """Return a compact advisory run; every operational failure is fail-open.

    The returned fields are suitable for independent local comparison/persistence.
    No raw OCR text or subprocess output is included in the summary.
    """
    folder = Path(folder).resolve()
    summary: dict[str, Any] = {"schema": SUMMARY_SCHEMA, "run_id": uuid.uuid4().hex,
        "created_at": _now(), "available": False, "status": "unavailable", "dry_run": dry_run,
        "business_validated": False, "records": [], "comparison_records": [], "counts": {}}
    project, origin, reason = discover_project()
    summary["configuration_source"] = origin
    if reason:
        summary["reason"] = reason
        return summary
    assert project is not None
    private_root = (folder / "generated" / "ocr").resolve()
    output = (Path(output_dir) if output_dir else private_root / "rapidocr").resolve()
    if not private_root.is_relative_to(folder) or not output.is_relative_to(private_root):
        summary["reason"] = "output_outside_private_ocr_directory"
        return summary
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0 or value > 3600 for value in (doctor_timeout, run_timeout)):
        summary["reason"] = "invalid_timeout"
        return summary
    try:
        with _output_lock(output):
            run_dir = output / "runs" / summary["run_id"]
            run_dir.mkdir(parents=True, exist_ok=False)
            summary.update(run_dir=str(run_dir), summary_path=str(output / SUMMARY_NAME))
            try:
                if jobs_manifest is None:
                    from .ocr_jobs import build_jobs
                    built = build_jobs(folder, db_path=db_path, overrides_path=overrides_path)
                    payload = built["job_manifest"]
                    summary["comparison_records"] = built["comparison_records"]
                    summary["job_build"] = {key: built[key] for key in ("schema", "unsupported", "excluded", "counts") if key in built}
                    summary.update({key: built.get(key, []) for key in ("unsupported", "excluded", "diagnostics")})
                else:
                    payload = _read_json(Path(jobs_manifest))
                    comparisons_path = Path(jobs_manifest).parent / "comparisons.json"
                    comparison_payload = None
                    if comparisons_path.is_file():
                        comparison_payload = json.loads(comparisons_path.read_text(encoding="utf-8"))
                jobs, hashes = _validate_jobs(payload, folder)
                if jobs_manifest is not None and comparison_payload is not None:
                    summary["comparison_records"] = _validate_comparisons(comparison_payload, jobs, hashes)
                elif jobs_manifest is None:
                    from .ocr_jobs import COMPARISON_SCHEMA
                    _validate_comparisons({"schema": COMPARISON_SCHEMA, "comparison_records": summary["comparison_records"]}, jobs, hashes)
                payload["jobs"] = list(jobs.values())
                manifest = run_dir / "jobs.json"
                _write_json(manifest, payload)
                summary.update(job_manifest_path=str(manifest), job_manifest_sha256=_hash(manifest),
                               configuration_identity=_identity(payload.get("configuration", {})),
                               schemas=EXTERNAL_SCHEMAS)
                if not jobs:
                    summary.update(available=True, status="available", reason="no_executable_jobs")
                else:
                    base = ["uv", "run", "--no-sync", "--project", str(project), "hkclr-ocr"]
                    doctor = base + ["doctor"] + ([] if dry_run else ["--initialize"])
                    code, stdout = _execute(doctor, timeout=doctor_timeout, project=project)
                    if code:
                        raise BridgeFailure("doctor_failed")
                    report = json.loads(stdout)
                    if not isinstance(report, dict) or report.get("initialization") != ("not_requested" if dry_run else "ok"):
                        raise BridgeFailure("doctor_initialization_unavailable")
                    summary["runtime"] = {key: report.get(key) for key in ("python", "rapidocr", "onnxruntime", "initialization")}
                    score = payload.get("configuration", {}).get("minimum_score", 0.5)
                    seeded = {"objects": 0, "raw_objects": 0} if dry_run else _seed_cache(output, run_dir, jobs, hashes, score, summary["runtime"])
                    summary.update(seeded_cache_objects=seeded["objects"], seeded_raw_cache_objects=seeded["raw_objects"])
                    command = base + ["run", str(manifest), "--output", str(run_dir)] + (["--dry-run"] if dry_run else [])
                    code, _ = _execute(command, timeout=run_timeout, project=project)
                    if code:
                        raise BridgeFailure("run_failed")
                    external, records = _consume(run_dir, jobs, hashes, dry_run=dry_run, minimum_score=score, runtime=summary["runtime"])
                    summary.update(available=True, status="available", reason="completed", records=records,
                                   configuration=external["configuration"],
                                   configuration_identity=_identity(external["configuration"]),
                                   counts=dict(Counter(record["status"] for record in records)),
                                   external_counts={key: external.get(key) for key in ("image_path_count", "unique_image_count", "processed", "cache_hits", "raw_cache_hits", "errors")})
            except BridgeFailure as error:
                summary.update(available=False, status="unavailable", reason=str(error), records=[])
            except (OSError, ValueError, TypeError, KeyError, AttributeError, ImportError, subprocess.SubprocessError) as error:
                summary.update(available=False, status="unavailable", reason=f"bridge_{type(error).__name__}", records=[])
            _write_json(run_dir / SUMMARY_NAME, summary)
            _write_json(output / SUMMARY_NAME, summary)
    except BridgeFailure as error:
        summary["reason"] = str(error)
    except OSError:
        summary["reason"] = "output_unavailable"
    return summary
