from __future__ import annotations

import argparse
from pathlib import Path

from .server import serve


def main() -> None:
    parser = argparse.ArgumentParser(description="Observable live-stream slicing agent")
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    serve(root, args.host, args.port, data_root=args.data_dir)


if __name__ == "__main__":
    main()

