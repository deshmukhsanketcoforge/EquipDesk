"""Export the REST API's OpenAPI schema to `openapi.json` (the committed contract).

    python -m scripts.export_openapi            # write openapi.json (make openapi)
    python -m scripts.export_openapi --check    # exit 1 if it is out of date

tests/test_openapi.py fails when the committed file drifts from the app.
"""

import argparse
import json
import sys
from pathlib import Path

from server.main import create_app

OPENAPI_FILE = Path(__file__).resolve().parent.parent / "openapi.json"


def schema_text() -> str:
    """The app's OpenAPI schema as the committed file holds it. Building the
    schema needs no database: the app's engine connects lazily, on a request."""
    return json.dumps(create_app().openapi(), indent=2) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export the OpenAPI schema to openapi.json.")
    parser.add_argument("--out", type=Path, default=OPENAPI_FILE)
    parser.add_argument("--check", action="store_true", help="fail if the file is out of date")
    args = parser.parse_args(argv)

    text = schema_text()
    if args.check:
        current = args.out.read_text() if args.out.exists() else None
        if current != text:
            print(f"{args.out} is out of date: run `make openapi`.", file=sys.stderr)
            return 1
        return 0
    args.out.write_text(text)
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
