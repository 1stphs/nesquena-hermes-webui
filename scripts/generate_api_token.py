#!/usr/bin/env python3
"""Generate a WebUI API token and a matching api_tokens.json record."""
from __future__ import annotations

import argparse
import hashlib
import json
import secrets


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a long-lived token for /api/auth/token-login."
    )
    parser.add_argument("--id", default="digital-employee-local-test")
    parser.add_argument("--name", default="digital_employee local test")
    parser.add_argument("--allowed-origin", action="append", dest="allowed_origins")
    parser.add_argument("--expires-at", default=None)
    parser.add_argument("--bytes", type=int, default=48)
    args = parser.parse_args()

    token = secrets.token_urlsafe(args.bytes)
    token_hash = "sha256:" + hashlib.sha256(token.encode()).hexdigest()
    record = {
        "id": args.id,
        "name": args.name,
        "token_hash": token_hash,
        "enabled": True,
        "expires_at": args.expires_at,
        "allowed_origins": args.allowed_origins or ["*"],
    }

    print("token =", token)
    print()
    print(json.dumps({"tokens": [record]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
