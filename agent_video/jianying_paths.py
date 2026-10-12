"""Resolve Jianying's draft-relative media placeholders."""
from pathlib import Path
import re


def resolve_draft_media_placeholder(raw_path: str, draft_dir: Path | None) -> str:
    """Leave ordinary paths unchanged; resolve placeholders against the project root."""
    match = re.match(r"^##_draftpath_placeholder_[^/\\]+?_##[/\\](.*)$", raw_path)
    if not match:
        return raw_path
    if draft_dir is None:
        raise ValueError("剪映素材路径包含草稿占位符，需要提供草稿文件路径才能解析")
    root = Path(draft_dir)
    # A timeline lives in <project>/Timelines/<timeline-id>/draft_content.json.
    if root.parent.name.lower() == "timelines":
        root = root.parent.parent
    relative = Path(match.group(1).replace("\\", "/"))
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"剪映草稿占位符中的素材路径无效: {raw_path}")
    return str(root / relative)
