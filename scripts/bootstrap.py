"""Create only this project's local environment; never overwrite user secrets."""

import argparse
import secrets
from pathlib import Path

from argon2 import PasswordHasher


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sec-email", required=True)
    args = parser.parse_args()
    if "@" not in args.sec_email:
        parser.error("A SEC contact email is required")
    root = Path(__file__).resolve().parents[1]
    if (root / ".env").exists():
        parser.error("Existing .env preserved; edit it explicitly")
    password, db_password = secrets.token_urlsafe(20), secrets.token_urlsafe(24)
    values = {
        "INTELLIGENCE_DATABASE_URL": f"postgresql://intelligence:{db_password}@127.0.0.1:5543/intelligence",
        "INTELLIGENCE_ADMIN_USERNAME": "admin",
        "INTELLIGENCE_ADMIN_PASSWORD_HASH": PasswordHasher().hash(password),
        "INTELLIGENCE_ORIGIN": "http://127.0.0.1:3100",
        "INTELLIGENCE_SEC_USER_AGENT": f"KefTradeIntelligence/0.1 {args.sec_email}",
        "INTELLIGENCE_ALPACA_KEY": "",
        "INTELLIGENCE_ALPACA_SECRET": "",
        "INTELLIGENCE_FEED": "sip",
        "INTELLIGENCE_ENVIRONMENT": "development",
        "INTELLIGENCE_MIN_FREE_GB": "1",
    }
    (root / ".env").write_text("\n".join(f"{k}={v}" for k, v in values.items()) + "\n")
    (root / ".local").mkdir(exist_ok=True)
    (root / ".local/access.txt").write_text(
        f"Local app: http://127.0.0.1:3100/intelligence\nUsername: admin\nPassword: {password}\n"
    )
    print(
        "Local environment created. Read .local/access.txt for credentials; configure market-data keys in .env."
    )


if __name__ == "__main__":
    main()
