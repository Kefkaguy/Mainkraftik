import argparse
import getpass
import json

from intelligence.db import connection, migrate
from intelligence.jobs import dispatch, worker
from intelligence.security import hasher
from intelligence.storage import jsonb
from intelligence.util import json_default


def main():
    parser = argparse.ArgumentParser(description="Independent financial research; no trading commands")
    parser.add_argument(
        "command",
        choices=[
            "migrate",
            "refresh",
            "issue",
            "resolve",
            "evaluate",
            "research",
            "audit",
            "monitor",
            "information",
            "information-backfill",
            "tune",
            "nominate",
            "shadow-status",
            "worker",
            "password-hash",
            "register",
            "expand-watchlist",
            "vision-approve",
        ],
    )
    parser.add_argument("--symbols", nargs="+")
    parser.add_argument("--symbol")
    parser.add_argument("--model-id")
    parser.add_argument("--weight-sha256")
    parser.add_argument("--license-reference")
    parser.add_argument("--runtime-verified", action="store_true")
    args = parser.parse_args()
    if args.command == "migrate":
        migrate()
        result = {"status": "migrated"}
    elif args.command == "password-hash":
        password = getpass.getpass("New administrator password (minimum 14 characters): ")
        if len(password) < 14:
            parser.error("Use at least 14 characters")
        print(hasher.hash(password))
        return
    elif args.command == "worker":
        worker()
        return
    elif args.command == "information-backfill":
        from intelligence.information import refresh_information
        from intelligence.ir_metadata import enrich_ir_publications

        result = refresh_information(backfill=True)
        result['publication_metadata']=enrich_ir_publications()
    elif args.command == "shadow-status":
        from intelligence.prospective import progress_and_verdict

        result = progress_and_verdict()
    elif args.command == "register":
        from intelligence.main import valid_symbol
        from intelligence.providers import lookup_company

        symbol = valid_symbol((args.symbol or "").upper())
        company = lookup_company(symbol)
        with connection() as conn:
            conn.execute(
                "INSERT INTO securities(symbol,name,cik) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
                (company["symbol"], company["name"], company["cik"]),
            )
        result = company
    elif args.command == "expand-watchlist":
        symbols = "MSI AXON AAPL MSFT NVDA AMD GOOGL META AMZN TSLA ADBE CRM ORCL AVGO QCOM COST WMT HD JNJ MRK ABBV XOM CVX CAT GE".split()
        with connection() as conn:
            for symbol in ("MSI", "AXON"):
                if not conn.execute(
                    "SELECT 1 FROM financial_facts WHERE symbol=%s LIMIT 1", (symbol,)
                ).fetchone():
                    parser.error("MSI and AXON financial ingestion must complete before expansion")
                if not conn.execute(
                    "SELECT 1 FROM forecast_outcomes o JOIN forecasts f ON f.id=o.forecast_id WHERE f.symbol=%s AND f.horizon=5 LIMIT 1",
                    (symbol,),
                ).fetchone():
                    parser.error(
                        "MSI and AXON must each have a resolved prospective 5-day baseline before expansion"
                    )
            known = {
                r["symbol"]
                for r in conn.execute("SELECT symbol FROM securities WHERE cik IS NOT NULL").fetchall()
            }
            missing = set(symbols) - known
            if missing:
                parser.error("Register verified identities first: " + ", ".join(sorted(missing)))
            conn.execute("INSERT INTO watchlist_versions(symbols) VALUES(%s)", (jsonb(symbols),))
        result = {"symbols": symbols, "training_universe_changed": False}
    elif args.command == "vision-approve":
        import re

        if not args.license_reference or not re.fullmatch(r"[a-f0-9]{64}", args.weight_sha256 or ""):
            parser.error("Reviewed license reference and exact SHA256 required")
        with connection() as conn:
            row = conn.execute(
                "UPDATE vision_models SET weight_sha256=%s,license_review_reference=%s,license_cleared=true,runtime_verified=%s WHERE id=%s RETURNING id",
                (args.weight_sha256, args.license_reference, args.runtime_verified, args.model_id),
            ).fetchone()
            if not row:
                parser.error("Unknown model ID")
        result = {"model": row["id"], "license_attestation_recorded": True}
    else:
        payload = {"symbols": args.symbols} if args.command == "refresh" else {"symbol": args.symbol}
        result = dispatch(args.command, payload)
    print(json.dumps(result, default=json_default, indent=2))


if __name__ == "__main__":
    main()
