from __future__ import annotations

import argparse
import sys
from pathlib import Path
from http.server import ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from muse.lead_intake import LeadStore, create_request_handler  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Serve the Muse local showcase and consent-gated SQLite inquiry inbox."
    )
    parser.add_argument("--host", default="127.0.0.1", choices=("127.0.0.1",))
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--database", default="data/commerce/leads.sqlite3")
    parser.add_argument("--retention-days", type=int, default=90)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")

    database = Path(args.database).expanduser()
    if not database.is_absolute():
        database = ROOT / database
    resolved_database = database.resolve(strict=False)
    if not resolved_database.is_relative_to(ROOT.resolve(strict=True)):
        parser.error("--database must remain inside the Muse project directory")

    store = LeadStore(database, retention_days=args.retention_days)
    handler = create_request_handler(store, ROOT / "ui-prototype")
    server = ThreadingHTTPServer((args.host, args.port), handler)
    server.daemon_threads = True
    print(f"Muse local preview: http://{args.host}:{args.port}/showcase.html")
    print(f"Local SQLite inbox: {database.relative_to(ROOT)}")
    print("Loopback only. This preview is not a public production service.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
