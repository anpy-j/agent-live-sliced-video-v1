import copy
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_video import breath, rule_pipeline
from agent_video.pipeline import run as pipeline
from agent_video.server import Application


class BreathTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.settings = self.root / 'rules.json'
        env = patch.dict(os.environ, {'LIVECUT_RULE_PIPELINE': str(self.settings)})
        env.start(); self.addCleanup(env.stop)
        self.clips = [{'id': 1, 'text': '版型很好', 'start': 0., 'end': 3.}]
        self.words = [{'s': .5, 'e': 2.5}]

    def test_default_off_strict_bypass(self):
        self.assertFalse(rule_pipeline.load_settings()['features']['breath']['enabled'])
        original = copy.deepcopy(self.clips)
        with patch.object(breath, 'calibrate', side_effect=AssertionError('must not run')):
            self.assertIsNone(pipeline._calibrate_breath(self.clips, 'missing.mp4'))
        self.assertEqual(original, self.clips)
        rule_pipeline.save_setting(self.settings, 'breath', {'enabled': True})
        with patch.object(breath, 'calibrate', return_value={'ok': True}) as call:
            self.assertEqual(pipeline._calibrate_breath(self.clips, 'missing.mp4'), {'ok': True})
            call.assert_called_once()
        rule_pipeline.save_setting(self.settings, 'breath', {'enabled': False})
        with patch.object(breath, 'calibrate', side_effect=AssertionError('must not run')):
            self.assertIsNone(pipeline._calibrate_breath(self.clips, 'missing.mp4'))

    def test_speech_guard_snapshot_and_no_expansion(self):
        original = copy.deepcopy(self.clips)
        with patch.object(breath, 'pauses', return_value=[(0., .55), (2.45, 3.)]):
            result = breath.calibrate(self.clips, 'source', words=self.words)
        self.assertEqual(self.clips, original)
        self.assertEqual(result['cleaned_candidates'][0]['start'], .47)
        self.assertEqual(result['cleaned_candidates'][0]['end'], 2.53)
        self.assertEqual(breath.restore(result, self.clips), result['cleaned_candidates'])
        result['cleaned_candidates'][0]['start'] = -.1
        with self.assertRaises(ValueError):
            breath.restore(result, self.clips)
        self.assertIs(breath.restore(None, self.clips), self.clips)

    def test_asr_edge_correction_is_bounded_and_does_not_delete_a_token(self):
        words = [{'s':0., 'e':.3}, {'s':2.7, 'e':3.}]
        with patch.object(breath, 'pauses', return_value=[(0., .7), (2.3, 3.)]):
            result = breath.calibrate(self.clips, 'source', words=words)
        self.assertEqual(result['cleaned_candidates'][0]['start'], .12)
        self.assertEqual(result['cleaned_candidates'][0]['end'], 2.88)

    def test_virtual_source_and_speed_mapping(self):
        from agent_video.timeline import VirtualTimeline, TimelineSegment
        virtual = VirtualTimeline('test', segments=[TimelineSegment('s',0,3,'source.mp4',100,106,2)])
        def detect(media, start, end):
            self.assertEqual(media,'source.mp4')
            return [(100.,101.)] if start == 100 else [(105.,106.)]
        with patch.object(breath,'pauses',side_effect=detect):
            result = breath.calibrate(self.clips,'unused',virtual,words=self.words)
        self.assertEqual(result['cleaned_candidates'][0]['start'],.42)
        self.assertEqual(result['cleaned_candidates'][0]['end'],2.58)

    def test_s4_on_snapshot_render_reuse_and_off_removes_snapshot(self):
        from agent_video.filler_rules import clips_digest
        work = self.root / 'work'; work.mkdir()
        (work/'timeline.json').write_text(json.dumps({'clauses':[
            {**self.clips[0], 'usable': True, 'unit': 0}], 'duration':3}))
        (work/'clauses.json').write_text(json.dumps({'words':self.words}))
        response = {'main_product':'外套', 'sections':[{'role':'hook','ids':[1]}], 'ordered_ids':[1]}
        # Keep this test independent from the user's actual filler rule configuration.
        with patch.dict(os.environ, {'LIVECUT_FILLER_RULES':str(self.root/'filler.json')}), \
                patch.object(pipeline,'ai_call',return_value=response), \
                patch.object(pipeline,'render_video'):
            rule_pipeline.save_setting(self.settings,'breath',{'enabled':False})
            baseline = pipeline.run_pipeline_stage('source', str(work), 'order', target_seconds=(2,4))
            self.assertNotIn('breath_detection',baseline)
            with patch.object(breath,'pauses',return_value=[(0.,.5),(2.5,3.)]) as detector:
                rule_pipeline.save_setting(self.settings,'breath',{'enabled':True})
                enabled = pipeline.run_pipeline_stage('source', str(work), 'order', target_seconds=(2,4))
                self.assertTrue(detector.called)
            self.assertEqual(baseline['filler_cleanup'],enabled['filler_cleanup'])
            self.assertEqual(enabled['breath_detection']['cleaned_digest'],
                             clips_digest(enabled['breath_detection']['cleaned_candidates']))
            rule_pipeline.save_setting(self.settings,'breath',{'enabled':False})
            with patch.object(breath,'calibrate',side_effect=AssertionError('render must reuse saved cuts')):
                rendered = pipeline.run_pipeline_stage('source',str(work),'render',target_seconds=(2,4))
            self.assertAlmostEqual(rendered['segments'][0]['start'],.42)
            self.assertAlmostEqual(rendered['segments'][0]['end'],2.58)
            disabled = pipeline.run_pipeline_stage('source',str(work),'order',target_seconds=(2,4))
            self.assertEqual(disabled,baseline)

    def test_no_words_no_pause_and_all_silence_preserve_cuts(self):
        with patch.object(breath, 'pauses', side_effect=AssertionError('no words')):
            self.assertEqual(breath.calibrate(self.clips, 'source')['cleaned_candidates'], self.clips)
        for found in ([], [(0., 1.), (2., 3.)]):
            with patch.object(breath, 'pauses', return_value=found):
                self.assertEqual(breath.calibrate(self.clips, 'source', words=self.words)['cleaned_candidates'], self.clips)

    @unittest.skipUnless(shutil.which('ffmpeg'), 'ffmpeg required')
    def test_real_audio_and_isolated_full_trial(self):
        media = self.root / 'source.mp4'
        subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=c=blue:s=160x240:r=25:d=3',
                        '-f','lavfi','-i',"aevalsrc=if(between(t\\,0.5\\,2.5)\\,0.4*sin(2*PI*440*t)\\,0):s=16000:d=3",
                        '-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-shortest',str(media)],check=True,
                       capture_output=True)
        result = breath.calibrate(self.clips, str(media), words=self.words)
        self.assertAlmostEqual(result['cleaned_candidates'][0]['start'], .42, delta=.04)
        self.assertAlmostEqual(result['cleaned_candidates'][0]['end'], 2.58, delta=.04)
        app = Application(Path(__file__).resolve().parents[1], self.root / 'app')
        work = self.root / 'job'; work.mkdir()
        (work/'order.json').write_text('{}')
        (work/'clauses.json').write_text(json.dumps({'words': self.words}))
        (work/'manifest.json').write_text(json.dumps({'segments': self.clips*2}))
        job_id = app.store.create_job(title='气口测试',source_path=str(media),workspace=str(work))
        app.store.update_job(job_id,status='completed')
        before = {p.name:p.read_bytes() for p in work.iterdir()}
        trial = app.breath_trials.start({'job_id': job_id})
        deadline = time.monotonic()+90
        while time.monotonic()<deadline:
            state = app.breath_trials.read(trial['id'])
            if state['status'] != 'running': break
            time.sleep(.2)
        self.assertEqual(state['status'], 'completed', state)
        self.assertEqual(len(state['rows']), 2)
        self.assertEqual(state['changed'], 2)
        self.assertEqual(before, {p.name:p.read_bytes() for p in work.iterdir()})
        for name in ('before.mp4','after.mp4','before-0.mp4','after-0.mp4'):
            self.assertTrue(app.breath_trials.media(trial['id'],name).is_file())
        for token, name in [('../x','before.mp4'),(trial['id'],'../manifest.json')]:
            with self.assertRaises(ValueError): app.breath_trials.media(token,name)
        duration = lambda name: float(subprocess.check_output(['ffprobe','-v','error','-show_entries',
            'format=duration','-of','default=nw=1:nk=1',str(app.breath_trials.media(trial['id'],name))]))
        self.assertGreater(duration('before.mp4')-duration('after.mp4'), 1.4)


if __name__ == '__main__':
    unittest.main()
