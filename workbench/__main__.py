"""Command line entry point: python -m workbench --data-dir DIR [--port 8000]."""

import argparse
import sqlite3
import sys

if sys.version_info < (3, 12):
    sys.exit("Dutch Freelance Workbench needs Python 3.12 or newer.")

from . import __version__
from .db import DatabaseRejected
from .server import PortUnavailable, create_server


def port(text):
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {text!r}") from None
    if not 0 <= value <= 65535:
        raise argparse.ArgumentTypeError("port must be between 0 and 65535")
    return value


def build_parser():
    parser = argparse.ArgumentParser(
        prog="python -m workbench",
        description="Dutch Freelance Workbench: a local-only lead tracker. All data stays in one folder.",
    )
    parser.add_argument("--data-dir", required=True, help="folder for the database (created if missing)")
    parser.add_argument("--port", type=port, default=8000, help="port to listen on (default: 8000)")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        server = create_server(args.data_dir, args.port)
    except DatabaseRejected as exc:
        print(f"Error: {exc.reason}. The file was left unchanged: {exc.path}", file=sys.stderr, flush=True)
        return 2
    except PortUnavailable as exc:
        print(f"Error: {exc}. Is another copy already running? Try --port with a different number.",
              file=sys.stderr, flush=True)
        return 3
    except OSError as exc:
        print(f"Error: could not start ({exc.strerror or type(exc).__name__}).", file=sys.stderr, flush=True)
        return 1
    except sqlite3.Error as exc:
        print(f"Error: could not open the database ({type(exc).__name__}). Check --data-dir.",
              file=sys.stderr, flush=True)
        return 1

    print(f"Dutch Freelance Workbench {__version__} (M1)", flush=True)
    print(f"URL:      http://127.0.0.1:{server.server_port}/", flush=True)
    print(f"Database: {server.db_path}", flush=True)
    print("Press Ctrl+C to stop.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
