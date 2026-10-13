import json
import os
import tempfile
import unittest
import threading
import urllib.request
from pathlib import Path
from unittest.mock import patch

from agent_video.pinyin_rules import load_rules, save_rule, delete_rule, match_text, normalize
from agent_video.server import Application, Server


class PinyinRulesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'data/pinyin_rules.json'

    def test_crud_normalization_duplicates_and_disabled_rules(self):
        snapshot = save_rule(self.path, {'pinyin': 'XIAN   HUO', 'name': '现货'})
        rule = snapshot['rules'][0]
        self.assertEqual(rule['pinyin'], 'xian huo')
        with self.assertRaises(ValueError):
            save_rule(self.path, {'pinyin': 'xian huo'})
        disabled = save_rule(self.path, {**rule, 'enabled': False}, rule['id'])
        self.assertNotEqual(snapshot['revision'], disabled['revision'])
        self.assertFalse(match_text('县货', disabled)['hits'])
        self.assertEqual(load_rules(self.path), disabled)
        self.assertEqual(delete_rule(self.path, rule['id'])['rules'], [])

    def test_homophones_offsets_phrase_pronunciation_and_boundaries(self):
        snapshot = save_rule(self.path, {'pinyin': 'xian huo'})
        for text in ('现货', '线货', '县货'):
            result = match_text('准备了' + text, snapshot)
            self.assertEqual(result['hits'][0]['text'], text)
            self.assertEqual(result['hits'][0]['start'], 3)
        for text in ('走线很好看', '先给大家开', '现100货', '现abc货'):
            self.assertFalse(match_text(text, snapshot)['hits'])
        self.assertTrue(match_text('现，货', snapshot)['hits'])
        self.assertIn('yin hang', match_text('银行', snapshot)['pinyin'])
        for invalid in ('xian...kai', 'xian * kai', 'xian1 huo4', 'xianhuo', '', 'blah huo'):
            with self.assertRaises(ValueError):
                normalize(invalid)

    def test_shared_filters_and_snapshot_in_smart_pipeline(self):
        from agent_video.pipeline.filter import filter_clauses as v1
        from agent_video.pipeline_v3.filter import filter_clauses as v3
        from agent_video.smart_v3.pipeline import coarse_filter
        snapshot = save_rule(self.path, {'pinyin': 'xian huo'})
        with patch.dict(os.environ, {'LIVECUT_PINYIN_RULES': str(self.path)}):
            for filter_fn in (v1, v3):
                rows = [{'id': 0, 'text': '我们给大家准备了线货', 'start': 0, 'end': 3}]
                filter_fn(rows)
                self.assertFalse(rows[0]['usable'])
                self.assertEqual(rows[0]['reason'], 'pinyin_rule')
                self.assertEqual(rows[0]['pinyin_hits'][0]['text'], '线货')
            result = coarse_filter([{'id': 'u0', 'text': '这里还有县货。'}])
            self.assertFalse(result['kept'])
            self.assertEqual(result['pinyin_rules'], snapshot)

    def test_completed_task_preview_reads_all_s2_without_changing_artifacts(self):
        app = Application(Path.cwd(), self.root)
        source = self.root / 'source.mp4'; source.write_bytes(b'fixture')
        job = app.create_job({'source_path': str(source), 'title': '试匹配'})
        workspace = Path(job['workspace'])
        file = workspace / 'clauses.filtered.json'
        content = json.dumps({'clauses': [
            {'id': 0, 'text': '线货', 'start': 0, 'end': 2, 'usable': True},
            {'id': 1, 'text': '县货', 'start': 2, 'end': 4, 'usable': False, 'reason': 'duplicate'},
            {'id': 2, 'text': '面料柔软', 'start': 4, 'end': 6, 'usable': True}]}, ensure_ascii=False)
        file.write_text(content, encoding='utf-8')
        save_rule(self.path, {'pinyin': 'xian huo'})
        with self.assertRaises(ValueError):
            app.test_pinyin_rules({'job_id': job['id']})
        app.store.update_job(job['id'], status='completed')
        self.assertEqual(app.pinyin_test_jobs()['jobs'][0]['id'], job['id'])
        result = app.test_pinyin_rules({'job_id': job['id']})
        self.assertEqual((result['total'], result['matched'], result['newly_rejected']), (3, 2, 1))
        self.assertEqual(file.read_text(encoding='utf-8'), content)
        self.assertEqual(app.store.get_job(job['id'])['status'], 'completed')

    def test_http_crud_and_smart_completed_task(self):
        app = Application(Path.cwd(), self.root)
        smart_job = app.smart.store.create({'product_name': '马甲'})
        app.smart.workspace(smart_job['id']).mkdir()
        smart_job['status'] = 'completed'; app.smart.store.save(smart_job)
        (app.smart.workspace(smart_job['id']) / 'S2.json').write_text(json.dumps({
            'kept': [{'id': 'u0', 'text': '准备了线货', 'start': 0, 'end': 3}],
            'removed': [{'id': 'u1', 'text': '面料柔软', 'start': 3, 'end': 6,
                         'filter_reason': 'exact_duplicate'}]}, ensure_ascii=False), encoding='utf-8')
        server = Server(('127.0.0.1', 0), app)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        def request(method, route, body=None):
            raw = json.dumps(body).encode() if body is not None else None
            with urllib.request.urlopen(urllib.request.Request(
                    f'http://127.0.0.1:{server.server_port}/api/rules/pinyin' + route,
                    data=raw, method=method, headers={'Content-Type': 'application/json'})) as response:
                return json.loads(response.read())
        try:
            self.assertEqual(request('GET', '')['rules'], [])
            saved = request('POST', '', {'pinyin': 'xian huo'})
            rule = saved['rules'][0]
            jobs = request('GET', '/jobs')['jobs']
            self.assertEqual(jobs[0]['kind'], 'smart')
            result = request('POST', '/test', {'kind': 'smart', 'job_id': smart_job['id']})
            self.assertEqual((result['total'], result['matched'], result['newly_rejected']), (2, 1, 1))
            request('PUT', '/' + rule['id'], {**rule, 'enabled': False})
            self.assertEqual(request('POST', '/test', {'kind': 'smart', 'job_id': smart_job['id']})['matched'], 0)
            other = request('POST', '', {'pinyin': 'xiang nai er'})['rules'][-1]
            self.assertEqual(request('POST', '/test', {'kind': 'smart', 'job_id': smart_job['id'], 'rule_id': other['id']})['matched'], 0)
            self.assertEqual(request('POST', '/test', {'kind': 'smart', 'job_id': smart_job['id'], 'rule_id': rule['id']})['matched'], 1)
            request('DELETE', '/' + other['id'])
            self.assertEqual(request('DELETE', '/' + rule['id'])['rules'], [])
        finally:
            server.shutdown(); thread.join(); server.server_close()


if __name__ == '__main__':
    unittest.main()
