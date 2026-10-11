import json
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from agent_video.postprocess import PostprocessService


def test_acestep_roundtrip_imports_generated_audio_and_metadata(tmp_path):
    music = tmp_path / "fake-generated.wav"
    with wave.open(str(music), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\0\0"*16000)
    received = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append((self.path, body))
            data = {"task_id": "cpu-test"} if self.path == "/release_task" else [
                {"task_id": "cpu-test", "status": 1, "result": json.dumps([{"file": "/v1/audio?path=generated.wav", "seed_value": "42"}])}]
            payload = json.dumps({"code": 200, "data": data}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            payload = music.read_bytes()
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    service = PostprocessService(tmp_path)
    try:
        service.settings({"output_dir": str(tmp_path / "out"), "acestep_url": f"http://127.0.0.1:{server.server_port}"})
        job = service.music.start({"prompt": "soft piano", "duration": 10})
        deadline = time.monotonic()+10
        while time.monotonic()<deadline:
            current = service.music.jobs()["jobs"][0]
            if current["status"] != "running":
                break
            time.sleep(.05)
        assert current["status"] == "completed", current
        asset = service.asset(service.library_id, current["asset_id"])
        assert asset["kind"] == "audio"
        assert current["result"]["seed_value"] == "42"
        assert received[0][1]["thinking"] is False
        assert received[0][1]["lyrics"] == "[Instrumental]"
        assert received[1][1]["task_id_list"] == ["cpu-test"]
    finally:
        service.close()
        server.shutdown()
        server.server_close()
