import copy
import json
import shutil
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from PIL import Image
from unittest.mock import patch

from agent_video.editor.model import ident, new_project, validate
from agent_video.editor.render import render
from agent_video.editor.service import Conflict, EditorService
from agent_video.server import Application, Server


class EditorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.service = EditorService(self.root)

    def tearDown(self):
        self.service.close()
        self.tmp.cleanup()

    def test_project_persistence_and_stale_save(self):
        p = self.service.create({"title": "中文项目"})
        p["timelines"].append({"id": ident(), "name": "另一成片", "tracks": [
            {"id": ident(), "kind": "subtitle", "clips": [{"id": ident(), "start": 0,
             "duration": 2, "text": "字幕", "style": "yellow"}]}]})
        stale = copy.deepcopy(p)
        saved = self.service.save(p["id"], p)
        self.assertEqual(len(self.service.get(p["id"])["timelines"]), 2)
        self.assertEqual(saved["revision"], 1)
        with self.assertRaises(Conflict):
            self.service.save(p["id"], stale)
        self.assertFalse((self.root / "data" / "agent.db").exists())

    def test_invalid_media_and_bounds_rejected(self):
        p = new_project()
        p["timelines"][0]["tracks"][0]["clips"] = [{"id": ident(), "asset_id": "unknown", "duration": 1}]
        with self.assertRaisesRegex(ValueError, "未导入"):
            validate(p, {})
        p["timelines"][0]["tracks"][0]["clips"][0]["start"] = float("nan")
        with self.assertRaises(ValueError):
            validate(p, {})

    def test_cancel_can_find_jobs_outside_recent_export_list(self):
        self.service.stop_event.set()
        self.service.wake.set()
        self.service.worker.join(timeout=3)
        with self.service.connect() as con:
            for i in range(105):
                job = {"id": str(i), "status": "queued", "progress": 0}
                con.execute("INSERT INTO exports VALUES (?,?,?,?)", (str(i), "p", json.dumps(job), i))
        self.assertEqual(len(self.service.exports()["exports"]), 100)
        self.assertEqual(self.service.cancel("0")["status"], "cancelled")
        self.assertEqual(self.service.get_export("0")["status"], "cancelled")

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
    def test_real_multitrack_render_and_media_ranges(self):
        source = self.root / "source.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=red:s=160x240:r=30:d=2",
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:v", "libx264", "-c:a", "aac", str(source)],
                       check=True, capture_output=True)
        p = self.service.create({"title": "测试"})
        imported = self.service.register(p["id"], str(source))
        p, a = imported["project"], imported["asset"]
        p["width"], p["height"] = 160, 240
        p["timelines"][0]["tracks"][0]["clips"] = [
            {"id": ident(), "asset_id": a["id"], "in": 0, "start": 0, "duration": 1},
            {"id": ident(), "asset_id": a["id"], "in": 1, "start": .8, "duration": 1, "transition": "fade"}]
        p["timelines"][0]["tracks"][1]["clips"] = [{"id": ident(), "start": .2, "duration": .8,
                                                       "text": "测试字幕", "font_size": 20, "style": "highlight"}]
        p["postprocess"]["progress"] = True
        p = self.service.save(p["id"], p)
        out = self.root / "output.mp4"
        render(p, out)
        self.assertTrue(out.is_file())
        result = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(out)]))
        self.assertAlmostEqual(float(result["format"]["duration"]), 1.8, delta=.1)
        self.assertEqual(next(s for s in result["streams"] if s["codec_type"] == "audio")["sample_rate"], "44100")
        # Registry path metadata cannot be replaced through an editing save.
        p["assets"][0]["path"] = "forged.mp4"
        self.assertEqual(self.service.save(p["id"], p)["assets"][0]["path"], str(source))
        self.assertTrue(self.service.cache(p["id"], a["id"], "thumbnail").is_file())

        app = Application(self.root / "http")
        app._editor = self.service
        server = Server(("127.0.0.1", 0), app)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/api/editor/projects/{p['id']}/assets/{a['id']}"
            request = urllib.request.Request(url, headers={"Range": "bytes=-12"})
            with urllib.request.urlopen(request) as response:
                self.assertEqual(response.status, 206)
                self.assertEqual(response.read(), source.read_bytes()[-12:])
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(urllib.request.Request(url, headers={"Range": "bytes=999999999-"}))
            self.assertEqual(error.exception.code, 416)
            with patch.dict("os.environ", {"LIVECUT_DESKTOP_TOKEN": "desktop-secret"}):
                with self.assertRaises(urllib.error.HTTPError) as denied:
                    urllib.request.urlopen(url)
                self.assertEqual(denied.exception.code, 403)
                with urllib.request.urlopen(urllib.request.Request(url, headers={"X-LiveCut-Desktop": "desktop-secret", "Range": "bytes=0-3"})) as response:
                    self.assertEqual(response.status, 206)
                    self.assertEqual(len(response.read()), 4)
        finally:
            server.shutdown()
            server.server_close()

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
    def test_rotated_sticker_wipe_slide_and_reserved_progress(self):
        source = self.root / "sticker.png"
        Image.new("RGBA", (80, 40), (0, 255, 0, 255)).save(source)
        p = self.service.create({"title": "角落贴纸"})
        imported = self.service.register(p["id"], str(source))
        p, a = imported["project"], imported["asset"]
        p["width"], p["height"] = 160, 240
        p["timelines"][0]["tracks"][0]["clips"] = [
            {"id": ident(), "asset_id": a["id"], "start": 0, "duration": .5, "rotation": 22, "scale": .5, "transition": "wipe"},
            {"id": ident(), "asset_id": a["id"], "start": .4, "duration": .5, "transition": "slide", "scale": .4}]
        p["postprocess"].update(progress=True, pip_asset=a["id"], pip_opacity=.01, sticker_asset=a["id"])
        p = self.service.save(p["id"], p)
        out = self.root / "corners.mp4"
        render(p, out)
        self.assertTrue(out.exists())
        graph = next(self.root.glob(".livecut-*/render.filter")).read_text()
        self.assertIn("scale=160:234,pad=160:240", graph)
        self.assertIn("geq=", graph)
        self.assertIn("rotw(", graph)
        self.assertEqual(graph.count("eof_action=pass"), 7)


if __name__ == "__main__":
    unittest.main()
