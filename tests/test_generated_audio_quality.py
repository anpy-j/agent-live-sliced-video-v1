import math
import struct
import wave

import pytest

from agent_video.postprocess.audio_quality import analyze_generated_audio


def audio(path, amplitudes, antiphase=False):
    rate=8000
    with wave.open(str(path),"wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        for amplitude in amplitudes:
            block=[]
            for i in range(rate):
                value=int(32767*amplitude*math.sin(i*math.tau*300/rate))
                block.append(struct.pack('<hh',value,-value if antiphase else value))
            wav.writeframes(b''.join(block))


@pytest.mark.parametrize("amplitudes, expected", [
    ([.2]*5+[.001]*5,"末尾 5.0 秒"),  # Model output can contain a low noise floor, not exact zeros.
    ([0]*10,"全程静音"),
    ([.2]*2+[0]*3+[.2]*5,"连续 3.0 秒"),
    ([.2]*8,"时长异常"),
])
def test_generated_music_rejects_missing_content(tmp_path, amplitudes, expected):
    path=tmp_path/'music.wav'
    audio(path,amplitudes)
    assert expected in analyze_generated_audio(path,10)["error"]


def test_continuous_stereo_with_phase_difference_and_short_ending_is_allowed(tmp_path):
    path=tmp_path/'music.wav'
    audio(path,[.2]*9+[0],antiphase=True)
    quality=analyze_generated_audio(path,10)
    assert not quality["error"]
    assert quality["quiet_tail_seconds"]==1
