"""Frozen local service launcher. Bundle assets; keep user data outside the app."""
import multiprocessing
import os
import sys
from pathlib import Path


if __name__ == "__main__":
    multiprocessing.freeze_support()
    # Frozen Python ignores PYTHONIOENCODING; logs must remain readable in Chinese.
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    assets = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    os.environ.setdefault("LIVECUT_ASSET_ROOT", str(assets))
    if "--jianying-decrypt" in sys.argv:
        sys.argv.remove("--jianying-decrypt")
        from agent_video.jianying_crypto import _main
        raise SystemExit(_main())
    elif "--render-multi" in sys.argv:
        sys.argv.remove("--render-multi")
        from agent_video.engine.scripts.render_multi import main
    elif "--editor-transcribe" in sys.argv:
        sys.argv.remove("--editor-transcribe")
        from agent_video.editor.transcribe import main
    else:
        from agent_video.__main__ import main
    main()
