"""Exercise the real faster-whisper/PyAV boundary without model downloads."""
import importlib.util
import math
import struct
import tempfile
import unittest
import wave
from pathlib import Path


@unittest.skipUnless(importlib.util.find_spec('faster_whisper') and importlib.util.find_spec('av'), 'portable ASR dependencies are optional')
class AudioDecodeTest(unittest.TestCase):
    def test_stereo_audio_decodes_and_resamples_for_whisper(self):
        import numpy as np
        from faster_whisper.audio import decode_audio
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / '语音输入.wav'
            samples = b''.join(struct.pack('<hh', int(8000*math.sin(2*math.pi*440*i/48000)),
                                         int(16000*math.sin(2*math.pi*440*i/48000)))
                               for i in range(48000))
            with wave.open(str(path), 'wb') as audio:
                audio.setnchannels(2); audio.setsampwidth(2); audio.setframerate(48000)
                audio.writeframes(samples)
            decoded = decode_audio(str(path), sampling_rate=16000)
            self.assertEqual(decoded.shape, (16000,))
            self.assertEqual(decoded.dtype, np.float32)
            self.assertTrue(np.isfinite(decoded).all())
            self.assertGreater(float(np.max(np.abs(decoded))), 0.1)
