"""Smart V3 shares the platform reader and its compatibility recovery."""
from __future__ import annotations

import sys

from ..jianying_crypto import (
    JianyingDecryptError,
    decrypt_jianying_file,
    find_jianying_install_dir,
    _worker_decrypt,
    _main,
)


if __name__ == "__main__":
    raise SystemExit(_main())
