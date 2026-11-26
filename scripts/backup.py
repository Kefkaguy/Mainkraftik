"""Create a consistent archive and verify it in a fresh receiving database."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))
from intelligence.backup import create_and_verify


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--off-host",
        action="store_true",
        help="Attest receiving machine differs from the production source host",
    )
    args = parser.parse_args()
    print(json.dumps(create_and_verify(args.output, args.off_host), indent=2))


if __name__ == "__main__":
    main()
