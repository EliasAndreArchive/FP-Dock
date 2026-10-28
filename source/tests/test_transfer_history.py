import json
import tempfile
import threading
import unittest
from pathlib import Path
from raw_library import Clip
from transfer import inventory_folder,transfer_all,select_inventory
from transfer_history import find_transferred

class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name).resolve()
        self.source=self.root/'CINEMA';self.source.mkdir()
        self.destination=self.root/'exports';self.destination.mkdir()
        self.clips=[]
        for name in ('a','b'):
            folder=self.source/name;folder.mkdir()
            frame=folder/'0001.dng';frame.write_bytes(name.encode()*12)
            self.clips.append(Clip(folder,name,[frame]))
        self.inventory=inventory_folder(self.source)
    def tearDown(self):self.tmp.cleanup()
    def transfer(self,clips=None):
        inventory=self.inventory if clips is None else select_inventory(self.inventory,self.clips,{c.key for c in clips})
        result=transfer_all(inventory,self.destination,allow_same_volume=True,folder_name='2026-10-26 Forest')
        return Path(result['session'])
    def check(self):return find_transferred(self.inventory,self.clips,self.destination,threading.Event())
    def test_existing_receipt_format_without_new_metadata(self):
        session=self.transfer()
        self.assertEqual(set(self.check()),{c.key for c in self.clips})
    def test_selected_export_does_not_mark_other_clips(self):
        self.transfer([self.clips[0]])
        self.assertEqual(set(self.check()),{self.clips[0].key})
    def test_missing_copy_does_not_hide_other_valid_clip(self):
        session=self.transfer();(session/'a/0001.dng').unlink()
        self.assertEqual(set(self.check()),{self.clips[1].key})
    def test_changed_destination_is_not_confirmed(self):
        session=self.transfer();(session/'a/0001.dng').write_bytes(b'corrupt!!!!!')
        self.assertEqual(set(self.check()),{self.clips[1].key})
    def test_changed_source_is_not_confirmed(self):
        self.transfer();self.clips[0].frames[0].write_bytes(b'new recording')
        self.inventory=inventory_folder(self.source)
        self.assertEqual(set(self.check()),{self.clips[1].key})
    def test_incomplete_manifest_never_confirms(self):
        session=self.transfer();p=session/'Extras/transfer.json'
        data=json.loads(p.read_text());data['status']='incomplete';p.write_text(json.dumps(data))
        self.assertEqual(self.check(),{})
    def test_receipt_path_traversal_is_not_followed(self):
        session=self.transfer();p=session/'Extras/checksums.sha256'
        p.write_text('0'*64+'  ../escape\n'+'0'*64+'  b/0001.dng\n')
        self.assertEqual(self.check(),{})
