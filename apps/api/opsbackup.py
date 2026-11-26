"""Consistent archives for the two isolated databases; restore never replaces data."""

import argparse
import hashlib
import json
import secrets
import zipfile
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
from psycopg import sql


@contextmanager
def database(kind, override=None):
    if kind == "research":
        from intelligence.config import settings
        from intelligence.db import connection as connect
        from intelligence.db import migrate

        attr = "database_url"
    elif kind == "paper":
        from paperpilot.config import settings
        from paperpilot.db import connect, migrate

        attr = "paper_database_url"
    else:
        raise ValueError("Unknown independent application database")
    original = getattr(settings, attr)
    try:
        if override:
            setattr(settings, attr, override)
        yield connect, migrate, getattr(settings, attr)
    finally:
        setattr(settings, attr, original)


def table_names(conn):
    return {r["tablename"] for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")}


def table_order(conn, tables):
    dependencies = {t: set() for t in tables}
    for r in conn.execute("""SELECT child.relname AS child,parent.relname AS parent FROM pg_constraint c
        JOIN pg_class child ON child.oid=c.conrelid JOIN pg_class parent ON parent.oid=c.confrelid
        JOIN pg_namespace n ON n.oid=child.relnamespace WHERE c.contype='f' AND n.nspname='public' """):
        if r["child"] != r["parent"]:
            dependencies[r["child"]].add(r["parent"])
    result = []
    while dependencies:
        ready = sorted(t for t, d in dependencies.items() if not d - set(result))
        if not ready:
            raise ValueError("Cyclic schema requires explicit restore procedure")
        result.extend(ready)
        for t in ready:
            del dependencies[t]
    return result


def export(path, kind):
    with database(kind) as (connect, _, uri), connect() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        tables = table_order(conn, table_names(conn))
        manifest = {
            "application": "keftrade-intelligence",
            "format": 2,
            "kind": kind,
            "created_at": datetime.now(UTC).isoformat(),
            "postgres_major": int(conn.info.server_version / 10000),
            "tables": [],
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path, "x", compression=zipfile.ZIP_DEFLATED) as archive:
            for t in tables:
                count = conn.execute(
                    sql.SQL("SELECT count(*) AS n FROM {}").format(sql.Identifier(t))
                ).fetchone()["n"]
                with (
                    archive.open(f"tables/{t}.bin", "w") as target,
                    conn.cursor().copy(
                        sql.SQL("COPY {} TO STDOUT WITH(FORMAT BINARY)").format(sql.Identifier(t))
                    ) as source,
                ):
                    for block in source:
                        target.write(block)
                manifest["tables"].append({"name": t, "rows": count})
            archive.writestr("manifest.json", json.dumps(manifest))
    return {
        "application": "keftrade-intelligence",
        "kind": kind,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "bytes": path.stat().st_size,
        "created_at": manifest["created_at"],
        "rows": sum(t["rows"] for t in manifest["tables"]),
    }


def restore_empty(path, kind, acknowledgement, override=None):
    with database(kind, override) as (connect, migrate, uri), zipfile.ZipFile(path) as archive:
        name = urlsplit(uri).path.removeprefix("/")
        if acknowledgement != name:
            raise ValueError("Explicit new database acknowledgement required")
        manifest = json.loads(archive.read("manifest.json"))
        if (
            manifest.get("application") != "keftrade-intelligence"
            or manifest.get("format") != 2
            or manifest.get("kind") != kind
        ):
            raise ValueError("Wrong application/kind archive")
        with connect() as conn:
            if table_names(conn):
                raise ValueError("Restore refused: target has existing tables; no overwrite permitted")
            if manifest["postgres_major"] != int(conn.info.server_version / 10000):
                raise ValueError("Binary restore requires same PostgreSQL major version")
        migrate()
        if kind == 'paper' and any(t['name'].startswith('learning_') for t in manifest['tables']):
            from paperpilot.experiment import migrate as migrate_learning
            migrate_learning()
        with connect() as conn:
            existing = table_names(conn)
            if {t["name"] for t in manifest["tables"]} != existing:
                raise ValueError("Archive/current migration schema mismatch")
            # This database was verified completely empty before migration.
            conn.execute(
                sql.SQL("TRUNCATE {} CASCADE").format(
                    sql.SQL(",").join(sql.Identifier(t) for t in sorted(existing))
                )
            )
            counts = {}
            for t in table_order(conn, existing):
                with (
                    archive.open(f"tables/{t}.bin") as source,
                    conn.cursor().copy(
                        sql.SQL("COPY {} FROM STDIN WITH(FORMAT BINARY)").format(sql.Identifier(t))
                    ) as target,
                ):
                    while block := source.read(1024 * 1024):
                        target.write(block)
                counts[t] = conn.execute(
                    sql.SQL("SELECT count(*) AS n FROM {}").format(sql.Identifier(t))
                ).fetchone()["n"]
            if counts != {t["name"]: t["rows"] for t in manifest["tables"]}:
                raise ValueError("Restored counts do not match consistent snapshot")
            for r in conn.execute(
                "SELECT table_name,column_name FROM information_schema.columns WHERE table_schema='public' AND column_default LIKE 'nextval(%'"
            ).fetchall():
                seq = conn.execute(
                    "SELECT pg_get_serial_sequence(%s,%s) AS s", (r["table_name"], r["column_name"])
                ).fetchone()["s"]
                maximum = conn.execute(
                    sql.SQL("SELECT max({}) AS n FROM {}").format(
                        sql.Identifier(r["column_name"]), sql.Identifier(r["table_name"])
                    )
                ).fetchone()["n"]
                conn.execute("SELECT setval(%s::regclass,%s,%s)", (seq, maximum or 1, maximum is not None))
    return {
        "application": "keftrade-intelligence",
        "kind": kind,
        "restore_verified": True,
        "restored_database": name,
        "row_counts": counts,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "bytes": path.stat().st_size,
    }


def verify_local(path, kind):
    with database(kind) as (_, _, uri):
        target = urlsplit(uri)
        if target.hostname not in {"127.0.0.1", "localhost"} or target.port != 5543:
            raise ValueError("Off-host restore verification must use independent local port 5543")
        prefix = "intelligence_restore_" if kind == "research" else "intelligence_paper_test_"
        name = prefix + secrets.token_hex(6)
        with psycopg.connect(urlunsplit(target._replace(path="/postgres")), autocommit=True) as admin:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        receipt = restore_empty(path, kind, name, urlunsplit(target._replace(path="/" + name)))
        receipt.update(off_host=True, created_at=datetime.now(UTC).isoformat())
        path.with_suffix(".receipt.json").write_text(json.dumps(receipt, indent=2))
        return receipt


def main():
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["export", "restore-new", "verify-local"])
    p.add_argument("path", type=Path)
    p.add_argument("--kind", choices=["research", "paper"], required=True)
    p.add_argument("--ack-new-database")
    args = p.parse_args()
    if "/home/ubuntu/minecraft" in str(args.path).replace("\\", "/").lower():
        p.error("Protected path")
    if args.action == "export":
        result = export(args.path, args.kind)
    elif args.action == "restore-new":
        result = restore_empty(args.path, args.kind, args.ack_new_database)
    else:
        result = verify_local(args.path, args.kind)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
