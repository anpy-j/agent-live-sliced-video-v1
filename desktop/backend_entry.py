"""Frozen local service launcher. Bundle assets; keep user data outside the app."""
import multiprocessing
import os
import sys
from pathlib import Path


if __name__ == "__main__":
    multiprocessing.freeze_support()
    assets = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    os.environ.setdefault("LIVECUT_ASSET_ROOT", str(assets))
    if "--render-multi" in sys.argv:
        sys.argv.remove("--render-multi")
        from agent_video.engine.scripts.render_multi import main
    elif "--editor-transcribe" in sys.argv:
        sys.argv.remove("--editor-transcribe")
        from agent_video.editor.transcribe import main
    else:
        from agent_video.__main__ import main
    main()
