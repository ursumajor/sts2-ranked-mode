"""Start the server:  python -m ranked_server [--host 127.0.0.1] [--port 8000] [--log-file server.log]

Works the same on Windows and Linux. Settings come from environment variables and server/.env.
"""

from __future__ import annotations

import argparse
import os
import sys

import uvicorn

from .config import SERVER_DIR, load_env_file


def main() -> None:
    load_env_file()
    parser = argparse.ArgumentParser(prog="python -m ranked_server", description="STS2 Ranked Mode server")
    parser.add_argument("--host", default=os.environ.get("RANKED_HOST", "127.0.0.1"),
                        help="interface to listen on (default 127.0.0.1; the Cloudflare tunnel connects locally)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("RANKED_PORT", "8000")))
    parser.add_argument("--log-file", default=os.environ.get("RANKED_LOG_FILE", ""),
                        help="append all output to this file (relative paths are inside server/)")
    args = parser.parse_args()

    if args.log_file:
        path = args.log_file if os.path.isabs(args.log_file) else os.path.join(SERVER_DIR, args.log_file)
        log = open(path, "a", buffering=1, encoding="utf-8")
        sys.stdout = sys.stderr = log

    uvicorn.run("ranked_server.app:create_app", factory=True, host=args.host, port=args.port,
                proxy_headers=True, forwarded_allow_ips="127.0.0.1")


if __name__ == "__main__":
    main()
