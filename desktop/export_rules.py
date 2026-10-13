"""Export the current rule list as an explicit desktop build resource."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent_video.app_runtime import user_data_dir
from agent_video.pinyin_rules import export_bundled_rules


def current_rules_source(root=ROOT):
    explicit = os.environ.get("LIVECUT_PINYIN_RULES")
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if not path.is_file():
            raise ValueError("指定的拼音规则文件不存在：" + str(path))
        return path
    workspace = os.environ.get("LIVECUT_WORKSPACE") or os.environ.get("LIVE_CUT_DATA_DIR")
    if not workspace:
        data = user_data_dir()
        config = data / "desktop.json"
        if config.is_file():
            workspace = json.loads(config.read_text(encoding="utf-8")).get("workspace")
        workspace = workspace or str(data)
    path = Path(workspace).expanduser() / "data" / "pinyin_rules.json"
    return path if path.is_file() else Path(root) / "data" / "pinyin_rules.json"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / ".runtime/package-defaults/pinyin_rules.json")
    args = parser.parse_args()
    source = args.source or current_rules_source()
    exported = export_bundled_rules(source, args.output)
    print(f"Exported {len(exported['rules'])} pinyin rules: {source} -> {args.output}")


if __name__ == "__main__":
    main()
