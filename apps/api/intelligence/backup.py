"""Consistent, compressed PostgreSQL binary-COPY archive and isolated restore test.

Only the independent application database can be a source. Restore always creates
a fresh database; it never replaces or drops an existing database or directory.
"""

import hashlib
import json
import secrets
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from psycopg import sql

from intelligence.config import settings
from intelligence.db import connection, migrate


def make_backup(output: Path):
    lexical = str(output).replace("\\", "/").lower()
    if "/home/ubuntu/minecraft" in lexical:
        raise ValueError("Protected Minecraft resources must not be accessed")
    if output.exists():
        raise ValueError("Choose a new backup filename; existing backups are never overwritten")
    output.parent.mkdir(parents=True, exist_ok=True)
    with connection() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        tables = [
            r["tablename"]
            for r in conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
            ).fetchall()
        ]
        manifest = {
            "format": 1,
            "application": "keftrade-intelligence",
            "created_at": datetime.now(UTC).isoformat(),
            "migrations": [
                r["name"] for r in conn.execute("SELECT name FROM schema_migrations ORDER BY name").fetchall()
            ],
            "tables": [],
        }
        with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED) as archive:
            for table in tables:
                count = conn.execute(
                    sql.SQL("SELECT count(*) AS n FROM {}").format(sql.Identifier(table))
                ).fetchone()["n"]
                with archive.open(f"tables/{table}.bin", "w") as target:
                    with conn.cursor().copy(
                        sql.SQL("COPY {} TO STDOUT WITH (FORMAT BINARY)").format(sql.Identifier(table))
                    ) as copy:
                        for block in copy:
                            target.write(block)
                manifest["tables"].append({"name": table, "rows": count})
            archive.writestr("manifest.json", json.dumps(manifest))
    return manifest


def restore_test(output: Path):
    from urllib.parse import urlsplit, urlunsplit

    uri = urlsplit(settings.database_url)
    # Restore verification takes place on the receiving local cluster only.
    if uri.hostname not in {"localhost", "127.0.0.1"} or uri.port != 5543:
        raise ValueError("Restore verification requires the independent receiving LOCAL cluster on port 5543")
    restored = "intelligence_restore_" + secrets.token_hex(6)
    with zipfile.ZipFile(output) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        if manifest.get("application") != "keftrade-intelligence" or manifest.get("format") != 1:
            raise ValueError("Unsupported application backup")
        with psycopg.connect(urlunsplit(uri._replace(path="/postgres")), autocommit=True) as admin:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(restored)))
        original = settings.database_url
        settings.database_url = urlunsplit(uri._replace(path="/" + restored))
        try:
            migrate()
            with connection() as conn:
                existing = {
                    r["tablename"]
                    for r in conn.execute(
                        "SELECT tablename FROM pg_tables WHERE schemaname='public'"
                    ).fetchall()
                }
                if set(r["name"] for r in manifest["tables"]) != existing:
                    raise ValueError("Backup schema does not match the current migrations")
                current = [
                    r["name"]
                    for r in conn.execute("SELECT name FROM schema_migrations ORDER BY name").fetchall()
                ]
                if current != manifest["migrations"]:
                    raise ValueError("Migration history mismatch")
                conn.execute("SET CONSTRAINTS ALL DEFERRED")
                # Only this freshly generated restore DB is truncated; never
                # the source or any existing user database.
                conn.execute(
                    sql.SQL("TRUNCATE {} CASCADE").format(
                        sql.SQL(",").join(sql.Identifier(t) for t in existing)
                    )
                )
                # FK-safe order follows the migration dependency graph.
                order = [
                    "schema_migrations",
                    "securities",
                    "watchlist_versions",
                    "training_universe_versions",
                    "source_documents",
                    "ingest_runs",
                    "daily_bars",
                    "corporate_actions",
                    "financial_facts",
                    "calculated_metrics",
                    "dataset_manifests",
                    "model_versions",
                    "forecast_runs",
                    "forecasts",
                    "forecast_outcomes",
                    "evaluation_reports",
                    "evaluation_access_log",
                    "vision_models",
                    "chart_detections",
                    "detection_outcomes",
                    "jobs",
                    "scheduler_marks",
                    "sessions",
                    "login_attempts",
                    "research_plans",
                    "research_candidates",
                    "research_experiments",
                    "research_predictions",
                    "research_audits",
                    "job_events",
                    "worker_status",
                    "information_versions",
                    "information_fetches",
                    "filing_events",
                    "feature_snapshots",
                    "tuning_plans",
                    "tuning_events",
                    "tuning_trials",
                    "candidate_results",
                    "shadow_protocols",
                    "shadow_artifacts",
                    "shadow_events",
                    "shadow_verdicts",
                ]
                if set(order) != existing:
                    raise ValueError("Restore dependency order must include every migrated table")
                counts = {}
                for table in order:
                    with archive.open(f"tables/{table}.bin") as source:
                        with conn.cursor().copy(
                            sql.SQL("COPY {} FROM STDIN WITH (FORMAT BINARY)").format(sql.Identifier(table))
                        ) as copy:
                            while block := source.read(1024 * 1024):
                                copy.write(block)
                    counts[table] = conn.execute(
                        sql.SQL("SELECT count(*) AS n FROM {}").format(sql.Identifier(table))
                    ).fetchone()["n"]
                if counts != {r["name"]: r["rows"] for r in manifest["tables"]}:
                    raise ValueError("Restored row counts do not match the consistent backup snapshot")
                # Restore SERIAL positions so later inserts cannot collide.
                for r in conn.execute(
                    "SELECT table_name,column_name,column_default FROM information_schema.columns WHERE table_schema='public' AND column_default LIKE 'nextval(%'"
                ).fetchall():
                    seq = conn.execute(
                        "SELECT pg_get_serial_sequence(%s,%s) AS s", (r["table_name"], r["column_name"])
                    ).fetchone()["s"]
                    maximum = conn.execute(
                        sql.SQL("SELECT max({}) AS n FROM {}").format(
                            sql.Identifier(r["column_name"]), sql.Identifier(r["table_name"])
                        )
                    ).fetchone()["n"]
                    conn.execute(
                        "SELECT setval(%s::regclass,%s,%s)", (seq, maximum or 1, maximum is not None)
                    )
        finally:
            settings.database_url = original
    return {"restored_database": restored, "row_counts": counts, "restore_verified": True}


def create_and_verify(output: Path, off_host=False):
    manifest = make_backup(output)
    result = restore_test(output)
    receipt = {
        "application": "keftrade-intelligence",
        "created_at": manifest["created_at"],
        "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "bytes": output.stat().st_size,
        "off_host": off_host,
        **result,
    }
    output.with_suffix(".receipt.json").write_text(json.dumps(receipt, indent=2))
    return receipt
