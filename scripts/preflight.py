"""Read-only deployment budget gate. Never scans or cleans host directories."""

import argparse
import json
import shutil
from pathlib import Path


def check_budget(
    free_bytes, installation_bytes, temporary_bytes, reserve_bytes=10 * 1024**3
):
    remaining = free_bytes - installation_bytes - temporary_bytes
    return {
        "passed": remaining >= reserve_bytes,
        "free_bytes": free_bytes,
        "installation_bytes": installation_bytes,
        "temporary_bytes": temporary_bytes,
        "projected_remaining_bytes": remaining,
        "minimum_remaining_bytes": reserve_bytes,
    }


def protected(path):
    # Lexical check first: never inspect even the protected directory itself.
    normalized = str(path).replace("\\", "/").rstrip("/").lower()
    return normalized == "/home/ubuntu/minecraft" or normalized.startswith(
        "/home/ubuntu/minecraft/"
    )


def main():
    p = argparse.ArgumentParser(description="No-deletion deployment resource gate")
    p.add_argument("--destination", default="/opt/keftrade-intelligence")
    p.add_argument(
        "--installed-bytes",
        type=int,
        required=True,
        help="Measured uncompressed image + initial database/artifact bytes",
    )
    p.add_argument(
        "--temporary-bytes",
        type=int,
        required=True,
        help="Measured peak archive/unpack/restore temporary bytes",
    )
    p.add_argument("--backup-receipt", type=Path, required=True)
    args = p.parse_args()
    if protected(args.destination) or protected(args.backup_receipt):
        p.error("Protected Minecraft resources must not be accessed")
    if args.installed_bytes <= 0 or args.temporary_bytes <= 0:
        p.error(
            "Actual positive measurements are required; estimates do not clear deployment"
        )
    target = Path(args.destination)
    if target.as_posix() != "/opt/keftrade-intelligence":
        p.error("Production destination must be /opt/keftrade-intelligence")
    # /opt need not contain the new project yet. Reading the containing
    # filesystem's free-space counter does not enumerate its directories.
    free = shutil.disk_usage("/opt").free
    receipt = json.loads(args.backup_receipt.read_text())
    backup_ok = (
        receipt.get("application") == "keftrade-intelligence"
        and receipt.get("restore_verified") is True
        and receipt.get("sha256")
        and receipt.get("off_host") is True
    )
    result = check_budget(free, args.installed_bytes, args.temporary_bytes)
    result["backup_verified"] = bool(backup_ok)
    result["passed"] = result["passed"] and bool(backup_ok)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
