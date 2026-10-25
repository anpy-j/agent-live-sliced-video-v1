"""Media timebase, cache and interrupted-write regression checks."""
import array
import json
import math
import os
import shutil
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import Mock, patch

from agent_video.db import Store
from agent_video.pipeline import run as pipeline
from agent_video.pipeline.errors import RenderError
from agent_video.pipeline.render import build_segments, build_virtual_segments
from agent_video.runner import JobRunner
from agent_video.timeline import TimelineSegment, VirtualTimeline, extract_virtual_timeline_audio, validate_audio_timeline


class IntegrityTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def timeline(self, segments=None):
        return VirtualTimeline('test', 'test', segments or [
            TimelineSegment('one', 0, 1, str(self.root / 'source.wav'), 0, 1)])

    def test_audio_rejects_gaps_overlaps_and_inconsistent_speed(self):
        for second_start in (1.1, .9):
            vt = self.timeline([TimelineSegment('one', 0, 1, 'source', 0, 1),
                TimelineSegment('two', second_start, second_start+1, 'source', 1, 2)])
            with self.assertRaisesRegex(ValueError, '空档或重叠'):
                validate_audio_timeline(vt)
        vt = self.timeline([TimelineSegment('one', 0, 1, 'source', 0, 2, 1.3)])
        with self.assertRaisesRegex(ValueError, '倍速不一致'):
            validate_audio_timeline(vt)

    def test_audio_rejects_nonfinite_negative_and_empty_bounds(self):
        for field, value in [('timeline_start', -1), ('source_start', -1), ('source_end', float('nan')),
                             ('speed', float('inf')), ('speed', 0), ('timeline_end', 0)]:
            vt = self.timeline(); setattr(vt.segments[0], field, value)
            with self.assertRaises(ValueError):
                validate_audio_timeline(vt)

    def test_render_cannot_silently_skip_unmapped_candidates(self):
        clips = [dict(id=0, start=0, end=.5), dict(id=1, start=2, end=3)]
        with self.assertRaisesRegex(RenderError, '未完整映射'):
            build_virtual_segments(clips, self.timeline())

    def test_render_rejects_partly_mapped_gap_and_overlap(self):
        for second_start in (1.1, .9):
            vt = self.timeline([TimelineSegment('one', 0, 1, 'source', 0, 1),
                TimelineSegment('two', second_start, second_start+1, 'source', 1, 2)])
            with self.assertRaisesRegex(RenderError, '空档或重叠'):
                build_virtual_segments([dict(id=0, start=0, end=second_start+1)], vt)
        with self.assertRaises(RenderError):
            build_virtual_segments([dict(id=0, start=0, end=1.5)], self.timeline())

    def test_render_still_maps_valid_contiguous_speed_changes(self):
        vt = self.timeline([TimelineSegment('one', 0, 1, 'a', 10, 11),
            TimelineSegment('two', 1, 2, 'b', 20, 21.3, 1.3)])
        pieces = build_virtual_segments([dict(id=0, start=.5, end=1.5)], vt)
        self.assertEqual([(p['source_start'], p['source_end']) for p in pieces], [(10.5, 11), (20, 20.65)])

    def test_direct_render_rejects_nonfinite_or_negative_ranges(self):
        for start, end in [(float('nan'), 1), (0, float('inf')), (-1, 1)]:
            with self.assertRaises(RenderError):
                build_segments([dict(id=0, start=start, end=end)])

    def test_interrupted_json_write_preserves_previous_complete_snapshot(self):
        path = self.root / 'timeline.json'
        pipeline._dump(str(path), {'clauses': [{'id': 0}]})
        previous = path.read_bytes()
        def interrupted(value, handle, **kwargs):
            handle.write('{"broken":')
            self.assertEqual(path.read_bytes(), previous)
            raise OSError('interrupted')
        with patch.object(pipeline.json, 'dump', side_effect=interrupted):
            with self.assertRaises(OSError):
                pipeline._dump(str(path), {'clauses': []})
        self.assertEqual(path.read_bytes(), previous)
        self.assertEqual(list(self.root.glob('*.tmp')), [])

    def test_rerun_cannot_start_while_old_worker_is_still_writing(self):
        store = Store(self.root / 'agent.db')
        work = self.root / 'work'; work.mkdir()
        order = work / 'order.json'; order.write_text('{"ordered_ids":[0]}')
        jid = store.create_job(title='test', source_path='source', workspace=str(work))
        store.update_job(jid, status='running')
        runner = JobRunner(store, self.root); runner._current = jid
        for status in ('running', 'pausing', 'paused'):
            store.update_job(jid, status=status)
            with patch.object(runner, 'cancel'), patch.object(runner._condition, 'wait_for', return_value=False):
                with self.assertRaisesRegex(ValueError, '仍在停止'):
                    runner.rerun_stage(jid, 'render')
        self.assertEqual(order.read_text(), '{"ordered_ids":[0]}')
        self.assertEqual(runner._queue, [])

    def test_cancel_during_split_export_does_not_mark_job_completed(self):
        store = Store(self.root / 'agent.db')
        work = self.root / 'work'; work.mkdir()
        jid = store.create_job(title='test', source_path='source', workspace=str(work))
        runner = JobRunner(store, self.root)
        manifest = {'segments': [dict(id=i, start=i, end=i+1) for i in range(3)], 'output': 'test.mp4'}
        def render(*args, **kwargs):
            Path(args[2]).write_bytes(b'partial delivery')
            runner._cancelled.add(jid)
        with patch('agent_video.runner.render_segment', side_effect=render) as encode:
            runner._succeed(jid, work, manifest, export_dir=str(self.root / 'export'), export_mode='segments', media='source')
        self.assertEqual(encode.call_count, 1)
        self.assertEqual(store.get_job(jid)['status'], 'cancelled')
        self.assertEqual(store.get_job(jid)['edit_count'], 0)

    def test_cancelled_stage_cannot_schedule_downstream_work(self):
        store = Store(self.root / 'agent.db')
        work = self.root / 'work'; work.mkdir()
        jid = store.create_job(title='test', source_path='source', workspace=str(work))
        runner = JobRunner(store, self.root); runner._cancelled.add(jid)
        runner._succeed_stage(jid, work, 'judge', {}, store.get_job(jid))
        self.assertEqual(store.get_job(jid)['status'], 'cancelled')
        self.assertEqual(runner._queue, [])

    def run_direct(self, source, work):
        words = [dict(w='面料柔软穿着舒适。', s=0, e=2)]
        def ai(model, prompt, schema, timeout):
            if schema is pipeline.DECISION_SCHEMA:
                return {'decisions': [dict(id=0, usable=True, reason='具体信息')]}
            return {'main_product': '外套', 'sections': [{'role': 'hook', 'ids': [0]}], 'ordered_ids': [0]}
        settings = self.root / 'rules.json'
        settings.write_text(json.dumps({'features': {'pinyin': {'enabled': False}, 'filler': {'enabled': False}}}))
        with patch.dict(os.environ, {'LIVECUT_RULE_PIPELINE': str(settings)}), patch.object(
                pipeline, '_default_asr', return_value=([], words, 2)) as asr, patch.object(
                pipeline, 'ai_call', side_effect=ai), patch.object(pipeline, 'render_video'):
            pipeline.run_pipeline(str(source), str(work), target_seconds=(1, 3), plugin_config={'enabled': False})
        return asr.call_count

    def test_same_filename_replacement_and_asr_model_change_invalidate_s1(self):
        source = self.root / 'source.mp4'; source.write_bytes(b'old source')
        work = self.root / 'work'
        self.assertEqual(self.run_direct(source, work), 1)
        self.assertEqual(self.run_direct(source, work), 0)
        original = source.stat()
        source.write_bytes(b'new source')
        os.utime(source, ns=(original.st_atime_ns, original.st_mtime_ns))
        self.assertEqual(self.run_direct(source, work), 1)
        with patch.dict(os.environ, {'DOUYIN_WHISPER_MODEL': 'different-model'}):
            self.assertEqual(self.run_direct(source, work), 1)

    def test_corrupt_direct_s1_is_not_accepted_as_transcription(self):
        source = self.root / 'source.mp4'; source.write_bytes(b'source')
        work = self.root / 'work'
        self.run_direct(source, work)
        path = work / 'clauses.json'
        data = json.loads(path.read_text(encoding='utf-8'))
        data['clauses'][0]['text'] = '被改动的缓存'
        path.write_text(json.dumps(data), encoding='utf-8')
        self.assertEqual(self.run_direct(source, work), 1)


@unittest.skipUnless(shutil.which('ffmpeg'), 'requires ffmpeg')
class AudioIntegrityTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / "quoted'folder"; self.root.mkdir()
        self.source = self.root / 'source.wav'
        self.write_source(440)
        self.output = self.root / 'out.wav'

    def write_source(self, frequency):
        samples = array.array('h', (int(5000 * math.sin(2*math.pi*frequency*i/16000)) for i in range(32000)))
        with wave.open(str(self.source), 'wb') as wav:
            wav.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
            wav.writeframes(samples.tobytes())

    def test_many_speed_changed_segments_have_exact_target_sample_count(self):
        vt = VirtualTimeline('test', segments=[TimelineSegment(str(i), i*.173, (i+1)*.173,
            str(self.source), i*.2249, (i+1)*.2249, 1.3) for i in range(8)])
        extract_virtual_timeline_audio(vt, self.output, self.root)
        with wave.open(str(self.output), 'rb') as wav:
            self.assertEqual(wav.getnframes(), round(8*.173*16000))
            self.assertEqual(wav.getframerate(), 16000)
        with patch('agent_video.timeline.subprocess.run') as call:
            extract_virtual_timeline_audio(vt, self.output, self.root)
        call.assert_not_called()

    def test_sub_ten_millisecond_segment_is_not_silently_dropped(self):
        vt = VirtualTimeline('tiny', segments=[
            TimelineSegment('tiny', 0, .005, str(self.source), 0, .005),
            TimelineSegment('normal', .005, .505, str(self.source), .5, 1)])
        extract_virtual_timeline_audio(vt, self.output, self.root)
        with wave.open(str(self.output), 'rb') as wav:
            self.assertEqual(wav.getnframes(), 8080)

    def test_slow_and_fast_segments_use_supported_atempo_steps(self):
        vt = VirtualTimeline('speeds', segments=[
            TimelineSegment('slow', 0, .5, str(self.source), 0, .125, .25),
            TimelineSegment('fast', .5, .75, str(self.source), .125, 1.125, 4)])
        extract_virtual_timeline_audio(vt, self.output, self.root)
        with wave.open(str(self.output), 'rb') as wav:
            self.assertEqual(wav.getnframes(), 12000)

    def test_media_change_or_corrupt_wav_invalidates_audio_cache(self):
        vt = VirtualTimeline('test', segments=[TimelineSegment('one', 0, 1, str(self.source), 0, 1)])
        extract_virtual_timeline_audio(vt, self.output, self.root)
        before = self.output.read_bytes()
        timestamp = self.source.stat().st_mtime_ns
        self.write_source(880)
        os.utime(self.source, ns=(timestamp+1000000000, timestamp+1000000000))
        extract_virtual_timeline_audio(vt, self.output, self.root)
        self.assertNotEqual(self.output.read_bytes(), before)
        self.output.write_bytes(b'corrupt')
        extract_virtual_timeline_audio(vt, self.output, self.root)
        with wave.open(str(self.output), 'rb') as wav:
            self.assertEqual(wav.getnframes(), 16000)
        self.output.write_bytes(self.output.read_bytes()[:100])
        extract_virtual_timeline_audio(vt, self.output, self.root)
        with wave.open(str(self.output), 'rb') as wav:
            self.assertEqual(len(wav.readframes(16000)), 32000)

    def test_failed_extraction_keeps_previous_complete_audio(self):
        vt = VirtualTimeline('test', segments=[TimelineSegment('one', 0, 1, str(self.source), 0, 1)])
        extract_virtual_timeline_audio(vt, self.output, self.root)
        before = self.output.read_bytes()
        changed = VirtualTimeline('changed', segments=[TimelineSegment('one', 0, .8, str(self.source), 0, .8)])
        with patch('agent_video.timeline.subprocess.run', return_value=Mock(returncode=1, stderr='failed')):
            with self.assertRaises(RuntimeError):
                extract_virtual_timeline_audio(changed, self.output, self.root)
        self.assertEqual(self.output.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
