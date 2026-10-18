"""剪映草稿的文件夹归类与备注存储。

只保存用户自己的整理信息（文件夹名称、备注），不修改剪映草稿本身。
数据保存在本地 JSON 文件中，草稿以列表接口返回的 id 作为键。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

MAX_FOLDER_NAME = 40
MAX_NOTE_LENGTH = 60


def _empty() -> dict[str, Any]:
    return {"folders": [], "drafts": {}}


def _clean_folder(value: Any) -> str:
    return " ".join(str(value or "").split())[:MAX_FOLDER_NAME]


def _clean_note(value: Any) -> str:
    return str(value or "").replace("\r\n", "\n").strip()[:MAX_NOTE_LENGTH]


def load_library(path: str | Path) -> dict[str, Any]:
    """读取整理数据；文件缺失或损坏时返回空结构，不抛错。"""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _empty()
    if not isinstance(raw, dict):
        return _empty()

    folders: list[str] = []
    seen: set[str] = set()
    for item in raw.get("folders") or []:
        name = _clean_folder(item)
        if name and name.casefold() not in seen:
            seen.add(name.casefold())
            folders.append(name)

    drafts: dict[str, dict[str, str]] = {}
    raw_drafts = raw.get("drafts")
    if isinstance(raw_drafts, dict):
        for key, value in raw_drafts.items():
            if not isinstance(value, dict):
                continue
            folder = _clean_folder(value.get("folder"))
            note = _clean_note(value.get("note"))
            if not folder and not note:
                continue
            drafts[str(key)] = {"folder": folder, "note": note}
    return {"folders": folders, "drafts": drafts}


def _save(path: str | Path, data: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
        os.replace(temp, target)
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise


def entry_for(path: str | Path, draft_id: str) -> dict[str, str]:
    entry = load_library(path)["drafts"].get(str(draft_id))
    if not entry:
        return {"folder": "", "note": ""}
    return {"folder": entry.get("folder", ""), "note": entry.get("note", "")}


def update_draft(
    path: str | Path,
    draft_id: str,
    *,
    folder: Any = None,
    note: Any = None,
) -> dict[str, Any]:
    """更新单个草稿的文件夹/备注。只更新显式传入的字段。"""
    draft_id = str(draft_id or "").strip()
    if not draft_id:
        raise ValueError("草稿 id 不能为空")
    data = load_library(path)
    entry = data["drafts"].get(draft_id) or {"folder": "", "note": ""}

    if folder is not None:
        name = _clean_folder(folder)
        entry["folder"] = name
        if name and name.casefold() not in {item.casefold() for item in data["folders"]}:
            data["folders"].append(name)
    if note is not None:
        entry["note"] = _clean_note(note)

    if entry["folder"] or entry["note"]:
        data["drafts"][draft_id] = entry
    else:
        data["drafts"].pop(draft_id, None)

    _save(path, data)
    return {"folders": data["folders"], "folder": entry["folder"], "note": entry["note"]}
