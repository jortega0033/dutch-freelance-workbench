"""TEST-ONLY launcher: the real server with a fixed clock.

    python tests/support/serve_fixed_clock.py --data-dir D --port P --now 2030-04-10T10:00:00Z

Production `python -m workbench` has no way to reach this.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.support.harness import FixedClock  # noqa: E402
from workbench import __version__  # noqa: E402
from workbench.server import create_server  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description="Serve the workbench with a fixed clock (tests only).")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--now", required=True, help="UTC instant, e.g. 2030-04-10T10:00:00Z")
    args = parser.parse_args(argv)

    server = create_server(args.data_dir, args.port, clock=FixedClock(args.now))
    print(f"Dutch Freelance Workbench {__version__} (M1) [FIXED CLOCK {args.now}]", flush=True)
    print(f"URL:      http://127.0.0.1:{server.server_port}/", flush=True)
    print(f"Database: {server.db_path}", flush=True)
    print("Press Ctrl+C to stop.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
