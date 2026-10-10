"""Isolated speech worker; results are applied by the editor, never overwrite a project."""
import argparse
import json
import os
import subprocess
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    args = parser.parse_args()
    request = Path(args.request)
    job = json.loads(request.read_text(encoding="utf-8"))
    audio = request.with_suffix(".wav")
    try:
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                        "-ss", str(job["in"]), "-i", job["path"], "-t", str(job["duration"] * job["speed"]),
                        "-vn", "-ac", "1", "-ar", "16000", str(audio)], check=True,
                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        from agent_video.engine.scripts.asr_backend import Transcriber
        rows = Transcriber(backend="faster", model=job["model"], device="cpu", compute_type="int8").transcribe(str(audio), word_timestamps=True)
        result = [{"start": job["start"] + r["start"] / job["speed"],
                   "duration": min(r["end"] / job["speed"], job["duration"]) - r["start"] / job["speed"],
                   "text": r["text"]} for r in rows if r["text"] and r["start"] / job["speed"] < job["duration"]]
        request.with_suffix(".result.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    finally:
        audio.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
