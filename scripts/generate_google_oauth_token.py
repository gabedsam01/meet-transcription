from __future__ import annotations

import argparse
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow


DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a Google OAuth token.json for the Drive worker."
    )
    parser.add_argument(
        "--client-secrets",
        required=True,
        help="Path to oauth-client.json downloaded from Google Cloud.",
    )
    parser.add_argument(
        "--token-file",
        required=True,
        help="Where to write the generated token.json file.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    client_secrets = Path(args.client_secrets)
    token_file = Path(args.token_file)

    flow = InstalledAppFlow.from_client_secrets_file(
        str(client_secrets), scopes=DRIVE_SCOPES
    )
    credentials = flow.run_local_server(
        port=0,
        access_type="offline",
        prompt="consent",
    )

    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(credentials.to_json(), encoding="utf-8")
    print(f"OAuth token written to {token_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
