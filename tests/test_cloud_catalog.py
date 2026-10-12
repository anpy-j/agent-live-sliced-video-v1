import json
import sqlite3
import tempfile
import unittest
import threading
import urllib.request
import urllib.error
from types import SimpleNamespace
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from pathlib import Path

from agent_video.cloud_catalog import catalog, output_file
from agent_video.server import Handler


class CatalogTests(unittest.TestCase):
    def test_missing_databases_do_not_get_created(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(catalog(folder),{'items':[],'errors':[]})
            self.assertEqual(list(Path(folder).iterdir()),[])

    def test_exports_and_v3_are_read_only_and_preserve_task_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);video=root/'成片.mp4';video.write_bytes(b'fixture')
            db=root/'data/editor/editor.db';db.parent.mkdir(parents=True)
            with sqlite3.connect(db) as con:
                con.execute('CREATE TABLE exports(data TEXT)')
                for i in range(2):
                    con.execute('INSERT INTO exports VALUES(?)',(json.dumps({'id':f'edit-{i}','title':'针织衫','status':'completed','output':str(video)}),))
            con.close()
            before=db.read_bytes()
            result=catalog(root)
            self.assertEqual(len(result['items']),2)
            self.assertNotEqual(result['items'][0]['id'],result['items'][1]['id'])
            self.assertEqual(output_file(root,result['items'][0]['id']),video)
            self.assertEqual(db.read_bytes(),before)
            with self.assertRaises(KeyError):output_file(root,'../../outside')
            video.unlink();self.assertEqual(catalog(root)['items'],[])

    def test_http_catalog_media_ranges_and_desktop_auth(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);media=root/'video.mp4';media.write_bytes(b'0123456789')
            database=root/'data/editor/editor.db';database.parent.mkdir(parents=True)
            con=sqlite3.connect(database)
            con.execute('CREATE TABLE exports(data TEXT)')
            con.execute('INSERT INTO exports VALUES(?)',(json.dumps({'id':'one','status':'completed','output':str(media)}),))
            con.commit();con.close()
            server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.app=SimpleNamespace(root=root)
            thread=threading.Thread(target=server.serve_forever);thread.start()
            base=f'http://127.0.0.1:{server.server_port}'
            try:
                with patch.dict('os.environ',{'LIVECUT_DESKTOP_TOKEN':'session-test'}):
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        urllib.request.urlopen(base+'/api/cloud/catalog')
                    self.assertEqual(error.exception.code,403)
                    request=urllib.request.Request(base+'/api/cloud/catalog',headers={'X-LiveCut-Desktop':'session-test'})
                    with urllib.request.urlopen(request) as response:
                        item=json.load(response)['items'][0]
                    request=urllib.request.Request(base+item['content_url'],headers={'X-LiveCut-Desktop':'session-test','Range':'bytes=2-4'})
                    with urllib.request.urlopen(request) as response:
                        self.assertEqual(response.status,206);self.assertEqual(response.read(),b'234')
                    request=urllib.request.Request(base+'/api/cloud/catalog/unknown/content',headers={'X-LiveCut-Desktop':'session-test'})
                    with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(request)
                    self.assertEqual(error.exception.code,404)
            finally:
                server.shutdown();server.server_close();thread.join()
