"""SQLite state database for reimbursement batches."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = 2
SNAPSHOT_SCHEMA = "prepare-reimbursements.state.snapshot.v2"


def utc_now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def json_loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    return json.loads(value)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def merge_reimbursement_type(existing: str | None, incoming: str) -> str:
    if not existing:
        return incoming
    if existing == incoming:
        return existing
    if existing == "mixed" or incoming == "mixed":
        return "mixed"
    return "mixed"


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    migrate(connection)
    return connection


def migrate(connection: sqlite3.Connection) -> None:
    current = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if current > SCHEMA_VERSION:
        raise RuntimeError(f"Database schema version {current} is newer than supported version {SCHEMA_VERSION}")

    if current < 1:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS batches (
                id INTEGER PRIMARY KEY,
                batch_folder TEXT NOT NULL UNIQUE,
                batch_label TEXT NOT NULL,
                reimbursement_type TEXT NOT NULL DEFAULT 'normal',
                source_manifest_path TEXT,
                source_export_path TEXT,
                profile_json TEXT NOT NULL DEFAULT '{}',
                summary_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY,
                batch_id INTEGER NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
                source_order_index INTEGER NOT NULL,
                source TEXT NOT NULL,
                order_no TEXT NOT NULL,
                taobao_order_detail_url TEXT,
                alipay_trade_no TEXT,
                alipay_detail_url TEXT,
                order_date TEXT,
                order_datetime TEXT,
                shop TEXT,
                status TEXT,
                item_label TEXT,
                amount_rmb REAL NOT NULL DEFAULT 0,
                shipping_rmb REAL,
                item_count INTEGER NOT NULL DEFAULT 0,
                document_type TEXT,
                missing_receipt_reason TEXT,
                evidence_required_json TEXT NOT NULL DEFAULT '[]',
                raw_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(batch_id, source, order_no)
            );

            CREATE TABLE IF NOT EXISTS order_items (
                id INTEGER PRIMARY KEY,
                order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
                item_index INTEGER NOT NULL,
                name TEXT,
                link TEXT,
                style TEXT,
                quantity TEXT,
                item_amount_rmb REAL,
                raw_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(order_id, item_index)
            );

            CREATE TABLE IF NOT EXISTS evidence_files (
                id INTEGER PRIMARY KEY,
                order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
                evidence_kind TEXT NOT NULL,
                expected_filename TEXT,
                actual_path TEXT,
                relative_path TEXT,
                raw_path TEXT,
                source_path TEXT,
                file_name TEXT,
                file_size INTEGER,
                sha256 TEXT,
                width INTEGER,
                height INTEGER,
                capture_method TEXT,
                validation_status TEXT NOT NULL DEFAULT 'unknown',
                warnings_json TEXT NOT NULL DEFAULT '[]',
                details_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(order_id, evidence_kind)
            );

            CREATE TABLE IF NOT EXISTS validation_results (
                id INTEGER PRIMARY KEY,
                batch_id INTEGER NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
                order_id INTEGER REFERENCES orders(id) ON DELETE CASCADE,
                scope TEXT NOT NULL,
                status TEXT NOT NULL,
                warnings_json TEXT NOT NULL DEFAULT '[]',
                tool TEXT NOT NULL,
                details_json TEXT NOT NULL DEFAULT '{}',
                checked_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS generated_artifacts (
                id INTEGER PRIMARY KEY,
                batch_id INTEGER NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
                artifact_kind TEXT NOT NULL,
                path TEXT NOT NULL,
                relative_path TEXT,
                file_size INTEGER,
                sha256 TEXT,
                generated_at TEXT NOT NULL,
                details_json TEXT NOT NULL DEFAULT '{}',
                UNIQUE(batch_id, artifact_kind, path)
            );

            CREATE INDEX IF NOT EXISTS idx_orders_batch ON orders(batch_id, source_order_index);
            CREATE INDEX IF NOT EXISTS idx_items_order ON order_items(order_id, item_index);
            CREATE INDEX IF NOT EXISTS idx_evidence_order ON evidence_files(order_id, evidence_kind);
            CREATE INDEX IF NOT EXISTS idx_validation_batch ON validation_results(batch_id, checked_at);
            CREATE INDEX IF NOT EXISTS idx_artifacts_batch ON generated_artifacts(batch_id, artifact_kind);
            PRAGMA user_version = 1;
            """
        )
        current = 1

    if current < 2:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS travel_expense_rows (
                id INTEGER PRIMARY KEY,
                batch_id INTEGER NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
                source_row_index INTEGER NOT NULL,
                expense_date TEXT,
                destination TEXT,
                category TEXT NOT NULL,
                category_label TEXT NOT NULL,
                currency TEXT NOT NULL,
                amount REAL NOT NULL DEFAULT 0,
                raw_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(batch_id, source_row_index, category, currency)
            );

            CREATE TABLE IF NOT EXISTS travel_itinerary_rows (
                id INTEGER PRIMARY KEY,
                batch_id INTEGER NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
                itinerary_index INTEGER NOT NULL,
                trip_date TEXT,
                origin TEXT,
                destination TEXT,
                purpose TEXT,
                raw_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(batch_id, itinerary_index)
            );

            CREATE TABLE IF NOT EXISTS travel_evidence_files (
                id INTEGER PRIMARY KEY,
                batch_id INTEGER NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
                evidence_index INTEGER NOT NULL,
                evidence_kind TEXT NOT NULL,
                expected_filename TEXT,
                actual_path TEXT,
                relative_path TEXT,
                raw_path TEXT,
                source_path TEXT,
                file_name TEXT,
                file_size INTEGER,
                sha256 TEXT,
                width INTEGER,
                height INTEGER,
                capture_method TEXT,
                validation_status TEXT NOT NULL DEFAULT 'unknown',
                warnings_json TEXT NOT NULL DEFAULT '[]',
                details_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(batch_id, evidence_index, evidence_kind)
            );

            CREATE INDEX IF NOT EXISTS idx_travel_expenses_batch
                ON travel_expense_rows(batch_id, source_row_index);
            CREATE INDEX IF NOT EXISTS idx_travel_itinerary_batch
                ON travel_itinerary_rows(batch_id, itinerary_index);
            CREATE INDEX IF NOT EXISTS idx_travel_evidence_batch
                ON travel_evidence_files(batch_id, evidence_index, evidence_kind);
            PRAGMA user_version = 2;
            """
        )
        current = 2

    connection.commit()


def upsert_batch(
    connection: sqlite3.Connection,
    *,
    batch_folder: Path,
    reimbursement_type: str,
    source_manifest_path: Path,
    source_export_path: Path | None,
    profile: dict[str, Any],
    summary: dict[str, Any],
) -> int:
    now = utc_now()
    batch_label = batch_folder.name
    connection.execute(
        """
        INSERT INTO batches (
            batch_folder, batch_label, reimbursement_type, source_manifest_path,
            source_export_path, profile_json, summary_json, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(batch_folder) DO UPDATE SET
            batch_label = excluded.batch_label,
            reimbursement_type = excluded.reimbursement_type,
            source_manifest_path = excluded.source_manifest_path,
            source_export_path = excluded.source_export_path,
            profile_json = excluded.profile_json,
            summary_json = excluded.summary_json,
            updated_at = excluded.updated_at
        """,
        (
            str(batch_folder),
            batch_label,
            reimbursement_type,
            str(source_manifest_path),
            str(source_export_path) if source_export_path else None,
            json_dumps(profile),
            json_dumps(summary),
            now,
            now,
        ),
    )
    row = connection.execute("SELECT id FROM batches WHERE batch_folder = ?", (str(batch_folder),)).fetchone()
    return int(row["id"])


def upsert_order(
    connection: sqlite3.Connection,
    *,
    batch_id: int,
    index: int,
    order: dict[str, Any],
) -> int:
    now = utc_now()
    source = str(order.get("source") or "taobao")
    order_no = str(order["order_no"])
    connection.execute(
        """
        INSERT INTO orders (
            batch_id, source_order_index, source, order_no,
            taobao_order_detail_url, alipay_trade_no, alipay_detail_url,
            order_date, order_datetime, shop, status, item_label, amount_rmb,
            shipping_rmb, item_count, document_type, missing_receipt_reason,
            evidence_required_json, raw_json, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(batch_id, source, order_no) DO UPDATE SET
            source_order_index = excluded.source_order_index,
            taobao_order_detail_url = excluded.taobao_order_detail_url,
            alipay_trade_no = excluded.alipay_trade_no,
            alipay_detail_url = excluded.alipay_detail_url,
            order_date = excluded.order_date,
            order_datetime = excluded.order_datetime,
            shop = excluded.shop,
            status = excluded.status,
            item_label = excluded.item_label,
            amount_rmb = excluded.amount_rmb,
            shipping_rmb = excluded.shipping_rmb,
            item_count = excluded.item_count,
            document_type = excluded.document_type,
            missing_receipt_reason = excluded.missing_receipt_reason,
            evidence_required_json = excluded.evidence_required_json,
            raw_json = excluded.raw_json,
            updated_at = excluded.updated_at
        """,
        (
            batch_id,
            index,
            source,
            order_no,
            order.get("taobao_order_detail_url"),
            order.get("alipay_trade_no"),
            order.get("alipay_detail_url"),
            order.get("date"),
            order.get("datetime"),
            order.get("shop"),
            order.get("status"),
            order.get("item_label"),
            float(order.get("amount_rmb") or 0),
            order.get("shipping_rmb"),
            int(order.get("item_count") or len(order.get("items") or [])),
            order.get("document_type"),
            order.get("missing_receipt_reason"),
            json_dumps(order.get("evidence_required") or []),
            json_dumps(order),
            now,
            now,
        ),
    )
    row = connection.execute(
        "SELECT id FROM orders WHERE batch_id = ? AND source = ? AND order_no = ?",
        (batch_id, source, order_no),
    ).fetchone()
    return int(row["id"])


def replace_items(connection: sqlite3.Connection, *, order_id: int, items: Iterable[dict[str, Any]]) -> int:
    now = utc_now()
    connection.execute("DELETE FROM order_items WHERE order_id = ?", (order_id,))
    count = 0
    for item_index, item in enumerate(items, 1):
        connection.execute(
            """
            INSERT INTO order_items (
                order_id, item_index, name, link, style, quantity,
                item_amount_rmb, raw_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                order_id,
                item_index,
                item.get("name"),
                item.get("link"),
                item.get("style"),
                item.get("quantity"),
                item.get("item_amount_rmb"),
                json_dumps(item),
                now,
                now,
            ),
        )
        count += 1
    return count


def upsert_evidence(connection: sqlite3.Connection, *, order_id: int, evidence: dict[str, Any]) -> None:
    now = utc_now()
    connection.execute(
        """
        INSERT INTO evidence_files (
            order_id, evidence_kind, expected_filename, actual_path, relative_path,
            raw_path, source_path, file_name, file_size, sha256, width, height,
            capture_method, validation_status, warnings_json, details_json,
            created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(order_id, evidence_kind) DO UPDATE SET
            expected_filename = excluded.expected_filename,
            actual_path = excluded.actual_path,
            relative_path = excluded.relative_path,
            raw_path = excluded.raw_path,
            source_path = excluded.source_path,
            file_name = excluded.file_name,
            file_size = excluded.file_size,
            sha256 = excluded.sha256,
            width = excluded.width,
            height = excluded.height,
            capture_method = excluded.capture_method,
            validation_status = excluded.validation_status,
            warnings_json = excluded.warnings_json,
            details_json = excluded.details_json,
            updated_at = excluded.updated_at
        """,
        (
            order_id,
            evidence["evidence_kind"],
            evidence.get("expected_filename"),
            evidence.get("actual_path"),
            evidence.get("relative_path"),
            evidence.get("raw_path"),
            evidence.get("source_path"),
            evidence.get("file_name"),
            evidence.get("file_size"),
            evidence.get("sha256"),
            evidence.get("width"),
            evidence.get("height"),
            evidence.get("capture_method"),
            evidence.get("validation_status", "unknown"),
            json_dumps(evidence.get("warnings") or []),
            json_dumps(evidence.get("details") or {}),
            now,
            now,
        ),
    )


def replace_validation_results(
    connection: sqlite3.Connection,
    *,
    batch_id: int,
    tool: str,
    results: Iterable[dict[str, Any]],
) -> int:
    connection.execute("DELETE FROM validation_results WHERE batch_id = ? AND tool = ?", (batch_id, tool))
    count = 0
    for result in results:
        connection.execute(
            """
            INSERT INTO validation_results (
                batch_id, order_id, scope, status, warnings_json, tool, details_json, checked_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                batch_id,
                result.get("order_id"),
                result["scope"],
                result["status"],
                json_dumps(result.get("warnings") or []),
                tool,
                json_dumps(result.get("details") or {}),
                result.get("checked_at") or utc_now(),
            ),
        )
        count += 1
    return count


def replace_artifacts(
    connection: sqlite3.Connection,
    *,
    batch_id: int,
    artifacts: Iterable[dict[str, Any]],
) -> int:
    connection.execute("DELETE FROM generated_artifacts WHERE batch_id = ?", (batch_id,))
    now = utc_now()
    count = 0
    for artifact in artifacts:
        connection.execute(
            """
            INSERT INTO generated_artifacts (
                batch_id, artifact_kind, path, relative_path, file_size, sha256,
                generated_at, details_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                batch_id,
                artifact["artifact_kind"],
                artifact["path"],
                artifact.get("relative_path"),
                artifact.get("file_size"),
                artifact.get("sha256"),
                artifact.get("generated_at") or now,
                json_dumps(artifact.get("details") or {}),
            ),
        )
        count += 1
    return count


def upsert_artifacts(
    connection: sqlite3.Connection,
    *,
    batch_id: int,
    artifacts: Iterable[dict[str, Any]],
) -> int:
    now = utc_now()
    count = 0
    for artifact in artifacts:
        connection.execute(
            """
            INSERT INTO generated_artifacts (
                batch_id, artifact_kind, path, relative_path, file_size, sha256,
                generated_at, details_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(batch_id, artifact_kind, path) DO UPDATE SET
                relative_path = excluded.relative_path,
                file_size = excluded.file_size,
                sha256 = excluded.sha256,
                generated_at = excluded.generated_at,
                details_json = excluded.details_json
            """,
            (
                batch_id,
                artifact["artifact_kind"],
                artifact["path"],
                artifact.get("relative_path"),
                artifact.get("file_size"),
                artifact.get("sha256"),
                artifact.get("generated_at") or now,
                json_dumps(artifact.get("details") or {}),
            ),
        )
        count += 1
    return count


def replace_travel_expenses(
    connection: sqlite3.Connection,
    *,
    batch_id: int,
    expenses: Iterable[dict[str, Any]],
) -> int:
    now = utc_now()
    connection.execute("DELETE FROM travel_expense_rows WHERE batch_id = ?", (batch_id,))
    count = 0
    for expense in expenses:
        connection.execute(
            """
            INSERT INTO travel_expense_rows (
                batch_id, source_row_index, expense_date, destination,
                category, category_label, currency, amount, raw_json,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                batch_id,
                int(expense["source_row_index"]),
                expense.get("expense_date") or expense.get("date"),
                expense.get("destination"),
                expense["category"],
                expense.get("category_label") or expense["category"],
                expense["currency"],
                float(expense.get("amount") or 0),
                json_dumps(expense.get("raw") or expense),
                now,
                now,
            ),
        )
        count += 1
    return count


def replace_travel_itineraries(
    connection: sqlite3.Connection,
    *,
    batch_id: int,
    itineraries: Iterable[dict[str, Any]],
) -> int:
    now = utc_now()
    connection.execute("DELETE FROM travel_itinerary_rows WHERE batch_id = ?", (batch_id,))
    count = 0
    for itinerary in itineraries:
        connection.execute(
            """
            INSERT INTO travel_itinerary_rows (
                batch_id, itinerary_index, trip_date, origin, destination,
                purpose, raw_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                batch_id,
                int(itinerary["itinerary_index"]),
                itinerary.get("trip_date") or itinerary.get("date"),
                itinerary.get("origin"),
                itinerary.get("destination"),
                itinerary.get("purpose"),
                json_dumps(itinerary.get("raw") or itinerary),
                now,
                now,
            ),
        )
        count += 1
    return count


def replace_travel_evidence_files(
    connection: sqlite3.Connection,
    *,
    batch_id: int,
    evidence_files: Iterable[dict[str, Any]],
) -> int:
    now = utc_now()
    connection.execute("DELETE FROM travel_evidence_files WHERE batch_id = ?", (batch_id,))
    count = 0
    for evidence in evidence_files:
        connection.execute(
            """
            INSERT INTO travel_evidence_files (
                batch_id, evidence_index, evidence_kind, expected_filename,
                actual_path, relative_path, raw_path, source_path, file_name,
                file_size, sha256, width, height, capture_method,
                validation_status, warnings_json, details_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                batch_id,
                int(evidence["evidence_index"]),
                evidence["evidence_kind"],
                evidence.get("expected_filename"),
                evidence.get("actual_path"),
                evidence.get("relative_path"),
                evidence.get("raw_path"),
                evidence.get("source_path"),
                evidence.get("file_name"),
                evidence.get("file_size"),
                evidence.get("sha256"),
                evidence.get("width"),
                evidence.get("height"),
                evidence.get("capture_method"),
                evidence.get("validation_status", "unknown"),
                json_dumps(evidence.get("warnings") or []),
                json_dumps(evidence.get("details") or {}),
                now,
                now,
            ),
        )
        count += 1
    return count


def snapshot(connection: sqlite3.Connection, *, batch_id: int, db_path: Path) -> dict[str, Any]:
    batch = connection.execute("SELECT * FROM batches WHERE id = ?", (batch_id,)).fetchone()
    if batch is None:
        raise ValueError(f"Batch id {batch_id} not found")

    orders = []
    order_rows = connection.execute(
        "SELECT * FROM orders WHERE batch_id = ? ORDER BY source_order_index, id",
        (batch_id,),
    ).fetchall()
    for order_row in order_rows:
        order_id = int(order_row["id"])
        item_rows = connection.execute(
            "SELECT * FROM order_items WHERE order_id = ? ORDER BY item_index",
            (order_id,),
        ).fetchall()
        evidence_rows = connection.execute(
            "SELECT * FROM evidence_files WHERE order_id = ? ORDER BY evidence_kind",
            (order_id,),
        ).fetchall()
        orders.append(
            {
                "index": order_row["source_order_index"],
                "source": order_row["source"],
                "order_no": order_row["order_no"],
                "date": order_row["order_date"],
                "shop": order_row["shop"],
                "status": order_row["status"],
                "item_label": order_row["item_label"],
                "amount_rmb": order_row["amount_rmb"],
                "taobao_order_detail_url": order_row["taobao_order_detail_url"],
                "alipay_trade_no": order_row["alipay_trade_no"],
                "alipay_detail_url": order_row["alipay_detail_url"],
                "items": [
                    {
                        "index": item["item_index"],
                        "name": item["name"],
                        "style": item["style"],
                        "quantity": item["quantity"],
                        "item_amount_rmb": item["item_amount_rmb"],
                        "link": item["link"],
                    }
                    for item in item_rows
                ],
                "evidence": [
                    {
                        "kind": evidence["evidence_kind"],
                        "status": evidence["validation_status"],
                        "expected_filename": evidence["expected_filename"],
                        "relative_path": evidence["relative_path"],
                        "raw_path": evidence["raw_path"],
                        "size": [evidence["width"], evidence["height"]]
                        if evidence["width"] and evidence["height"]
                        else None,
                        "sha256": evidence["sha256"],
                        "warnings": json_loads(evidence["warnings_json"], []),
                    }
                    for evidence in evidence_rows
                ],
            }
        )

    artifacts = [
        {
            "kind": artifact["artifact_kind"],
            "relative_path": artifact["relative_path"],
            "path": artifact["path"],
            "sha256": artifact["sha256"],
            "details": json_loads(artifact["details_json"], {}),
        }
        for artifact in connection.execute(
            "SELECT * FROM generated_artifacts WHERE batch_id = ? ORDER BY artifact_kind, path",
            (batch_id,),
        ).fetchall()
    ]

    travel_expenses = [
        {
            "source_row_index": row["source_row_index"],
            "date": row["expense_date"],
            "destination": row["destination"],
            "category": row["category"],
            "category_label": row["category_label"],
            "currency": row["currency"],
            "amount": row["amount"],
            "raw": json_loads(row["raw_json"], {}),
        }
        for row in connection.execute(
            """
            SELECT * FROM travel_expense_rows
            WHERE batch_id = ?
            ORDER BY source_row_index, category, currency
            """,
            (batch_id,),
        ).fetchall()
    ]
    travel_itinerary = [
        {
            "index": row["itinerary_index"],
            "date": row["trip_date"],
            "origin": row["origin"],
            "destination": row["destination"],
            "purpose": row["purpose"],
            "raw": json_loads(row["raw_json"], {}),
        }
        for row in connection.execute(
            """
            SELECT * FROM travel_itinerary_rows
            WHERE batch_id = ?
            ORDER BY itinerary_index
            """,
            (batch_id,),
        ).fetchall()
    ]
    travel_evidence = [
        {
            "index": row["evidence_index"],
            "kind": row["evidence_kind"],
            "status": row["validation_status"],
            "expected_filename": row["expected_filename"],
            "relative_path": row["relative_path"],
            "raw_path": row["raw_path"],
            "source_path": row["source_path"],
            "size": [row["width"], row["height"]] if row["width"] and row["height"] else None,
            "sha256": row["sha256"],
            "warnings": json_loads(row["warnings_json"], []),
            "details": json_loads(row["details_json"], {}),
        }
        for row in connection.execute(
            """
            SELECT * FROM travel_evidence_files
            WHERE batch_id = ?
            ORDER BY evidence_index, evidence_kind
            """,
            (batch_id,),
        ).fetchall()
    ]

    return {
        "schema": SNAPSHOT_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "generated_at": utc_now(),
        "database": str(db_path),
        "batch": {
            "folder": batch["batch_folder"],
            "label": batch["batch_label"],
            "reimbursement_type": batch["reimbursement_type"],
            "source_manifest_path": batch["source_manifest_path"],
            "source_export_path": batch["source_export_path"],
            "profile": json_loads(batch["profile_json"], {}),
            "summary": json_loads(batch["summary_json"], {}),
        },
        "orders": orders,
        "travel": {
            "expenses": travel_expenses,
            "itinerary": travel_itinerary,
            "evidence": travel_evidence,
        },
        "artifacts": artifacts,
    }
