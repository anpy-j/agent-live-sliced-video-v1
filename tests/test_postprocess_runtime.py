from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent_video.postprocess.runtime import CpuMusicRuntime


def runtime(tmp_path, monkeypatch, url="http://127.0.0.1:8001"):
    monkeypatch.setenv("LIVECUT_ASSET_ROOT", str(tmp_path))
    service = SimpleNamespace(root=tmp_path, settings=lambda: {"acestep_url": url})
    result = CpuMusicRuntime(service)
    result.python.parent.mkdir(parents=True)
    result.python.touch()
    api = result.checkout / "acestep" / "api_server.py"
    api.parent.mkdir(parents=True)
    api.touch()
    monkeypatch.setattr(result, "status", lambda: {"installed": True, "running": False, "ready": False})
    return result


def test_cpu_launch_is_isolated_and_reuses_owned_process(tmp_path, monkeypatch):
    item = runtime(tmp_path, monkeypatch)
    spawn = Mock(return_value=Mock(pid=12345))
    monkeypatch.setattr("agent_video.postprocess.runtime.subprocess.Popen", spawn)
    result = item.start()
    assert result["running"] and result["managed"]
    args, kwargs = spawn.call_args
    assert args[0][-4:] == ["--host", "127.0.0.1", "--port", "8001"]
    assert kwargs["env"]["ACESTEP_DEVICE"] == "cpu"
    assert kwargs["env"]["ACESTEP_INIT_LLM"] == "false"
    assert kwargs["cwd"] == item.checkout
    monkeypatch.setattr(item, "status", lambda: result)
    assert item.start() == result
    assert spawn.call_count == 1


def test_external_service_is_never_owned_or_stopped(tmp_path, monkeypatch):
    item = runtime(tmp_path, monkeypatch)
    spawn = Mock()
    stop = Mock()
    monkeypatch.setattr("agent_video.postprocess.runtime.subprocess.Popen", spawn)
    monkeypatch.setattr("agent_video.postprocess.runtime.subprocess.run", stop)
    monkeypatch.setattr(item, "status", lambda: {"ready": True, "running": False, "managed": False})
    assert item.start()["ready"]
    item.close()
    spawn.assert_not_called()
    stop.assert_not_called()


def test_launch_rejects_remote_address(tmp_path, monkeypatch):
    item = runtime(tmp_path, monkeypatch, "https://example.com")
    with pytest.raises(ValueError, match="启动本机 CPU 服务"):
        item.start()


def test_stop_protects_external_service_and_active_music(tmp_path, monkeypatch):
    item = runtime(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="本应用启动"):
        item.stop()
    item.process = Mock()
    item.service.music = SimpleNamespace(jobs=lambda: {"jobs": [{"status": "running"}]})
    close = Mock()
    monkeypatch.setattr(item, "close", close)
    with pytest.raises(ValueError, match="音乐正在生成"):
        item.stop()
    close.assert_not_called()
    item.service.music = SimpleNamespace(jobs=lambda: {"jobs": [{"status": "completed"}]})
    item.stop()
    close.assert_called_once()
    assert item.process is None
