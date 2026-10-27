import hashlib
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

SOURCE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(SOURCE))
from transfer import inventory_folder, transfer_all, hash_file, TransferError, TransferCancelled, SessionLock
from devices import find_cinema, ArrivalTracker, set_startup


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=SOURCE/'tests')
        self.root=Path(self.temp.name)
        self.source=self.root/'card/CINEMA'
        self.destination=self.root/'ssd'
        self.source.mkdir(parents=True)
        self.destination.mkdir()
        (self.source/'clip').mkdir()
        (self.source/'empty').mkdir()
        (self.source/'clip/FRAME_0001.dng').write_bytes(bytes(range(256))*4096)
        (self.source/'clip/FRAME_0002.dng').write_bytes(b'raw two'*20000)
        (self.source/'clip/sound.wav').write_bytes(b'WAV\0'*500)
        (self.source/'metadata.txt').write_bytes(b'metadata is important too\n')
        (self.source/'zero-size').touch()
        self.original={p.relative_to(self.source).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
                       for p in self.source.rglob('*') if p.is_file()}

    def tearDown(self):
        self.temp.cleanup()

    def run_transfer(self,**kwargs):
        return transfer_all(inventory_folder(self.source),self.destination,allow_same_volume=True,**kwargs)

    def assert_source_unchanged(self):
        now={p.relative_to(self.source).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
             for p in self.source.rglob('*') if p.is_file()}
        self.assertEqual(now,self.original)

    def test_named_date_range_transfer_resume_and_distinct_custom_name(self):
        name='2026-09-27 - 2026-09-29 Street clips'
        result=self.run_transfer(folder_name=name)
        self.assertEqual(Path(result['session']).name,name)
        again=self.run_transfer(folder_name=name)
        self.assertEqual(again['session'],result['session'])
        other=self.run_transfer(folder_name='2026-09-27 Forest')
        self.assertNotEqual(other['session'],result['session'])
        self.assertTrue((Path(result['session'])/'Extras/transfer.json').exists())
        self.assert_source_unchanged()

    def test_copy_every_file_and_receipt_then_reverify(self):
        result=self.run_transfer()
        self.assertEqual(result['status'],'verified')
        self.assertEqual(result['verified_files'],5)
        session=Path(result['session'])
        self.assertTrue((session/'empty').is_dir())
        self.assertRegex(session.name,r'^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}(?:-\d+)?$')
        self.assertTrue((session/'Extras/transfer.json').exists())
        self.assertFalse((session/'CINEMA').exists())
        for name,digest in self.original.items():
            self.assertEqual(hash_file(session/name)[0],digest)
        self.assertEqual(len((session/'Extras/checksums.sha256').read_text().splitlines()),5)
        again=self.run_transfer()
        self.assertEqual(again['session'],result['session'])
        self.assertEqual(again['copied_files'],0)
        self.assertEqual(again['reused_files'],5)
        self.assert_source_unchanged()

    def test_cancel_then_resume_without_recopying_verified_files(self):
        cancel=threading.Event()
        def progress(info):
            if info['verified']==1:
                cancel.set()
        with self.assertRaises(TransferCancelled):
            self.run_transfer(cancel=cancel,progress=progress)
        session=next(x for x in self.destination.iterdir() if x.is_dir())
        self.assertEqual(json.loads((session/'Extras/transfer.json').read_text())['status'],'cancelled')
        result=self.run_transfer()
        self.assertEqual(result['session'],str(session))
        self.assertEqual(result['reused_files'],1)
        self.assertEqual(result['verified_files'],5)
        self.assert_source_unchanged()

    def test_conflicting_destination_is_never_overwritten(self):
        result=self.run_transfer()
        target=Path(result['session'])/'metadata.txt'
        altered=b'X'*target.stat().st_size
        target.write_bytes(altered)
        with self.assertRaisesRegex(TransferError,'Checksum mismatch'):
            self.run_transfer()
        self.assertEqual(target.read_bytes(),altered)
        self.assert_source_unchanged()

    def test_hidden_files_and_restart_partial_space(self):
        (self.source/'.camera-metadata').write_bytes(b'hidden sidecar')
        cancel=threading.Event()
        def stop_during_copy(info):
            if info['phase']=='Copying' and info['processed']>100000:
                cancel.set()
        with self.assertRaises(TransferCancelled):
            self.run_transfer(cancel=cancel,progress=stop_during_copy)
        session=next(x for x in self.destination.iterdir() if x.is_dir())
        partials=list((session/'Extras/Partials').glob('*.partial'))
        self.assertTrue(any(p.stat().st_size>0 for p in partials))
        real_usage=shutil.disk_usage
        def check_reclaimed(path):
            self.assertTrue(all(p.stat().st_size==0 for p in partials))
            return real_usage(path)
        with patch('transfer.shutil.disk_usage',side_effect=check_reclaimed):
            result=self.run_transfer()
        self.assertEqual(result['verified_files'],6)
        self.assertEqual((Path(result['session'])/'.camera-metadata').read_bytes(),b'hidden sidecar')

    def test_source_change_prevents_completion(self):
        changed=False
        def progress(info):
            nonlocal changed
            if info['phase']=='Verified' and not changed:
                changed=True
                (self.source/'new-recording.mov').write_bytes(b'recording started after inventory')
        with self.assertRaisesRegex(TransferError,'changed during'):
            self.run_transfer(progress=progress)
        session=next(x for x in self.destination.iterdir() if x.is_dir())
        self.assertNotEqual(json.loads((session/'Extras/transfer.json').read_text())['status'],'verified')

    def test_replaced_source_volume_is_rejected(self):
        from dataclasses import replace
        inventory=inventory_folder(self.source)
        other_card=replace(inventory,device=inventory.device+1)
        with self.assertRaisesRegex(TransferError,'Source card changed'):
            transfer_all(other_card,self.destination,allow_same_volume=True)

    def test_source_removed_mid_copy_is_incomplete(self):
        removed=False
        victim=self.source/'clip/FRAME_0002.dng'
        def progress(info):
            nonlocal removed
            if info['phase']=='Verified' and not removed:
                removed=True
                victim.unlink()
        with self.assertRaises(OSError):
            self.run_transfer(progress=progress)
        session=next(x for x in self.destination.iterdir() if x.is_dir())
        self.assertEqual(json.loads((session/'Extras/transfer.json').read_text())['status'],'incomplete')

    def test_corrupt_readback_never_publishes(self):
        import transfer
        original=transfer.hash_file
        def corrupt(path,*args,**kwargs):
            digest,size=original(path,*args,**kwargs)
            return ('0'*64,size) if path.suffix=='.partial' else (digest,size)
        with patch('transfer.hash_file',side_effect=corrupt):
            with self.assertRaisesRegex(TransferError,'Checksum mismatch'):
                self.run_transfer()
        session=next(x for x in self.destination.iterdir() if x.is_dir())
        self.assertFalse((session/'clip/FRAME_0001.dng').exists())
        self.assert_source_unchanged()

    def test_reject_source_destination_overlap_and_same_volume(self):
        with self.assertRaisesRegex(TransferError,'inside'):
            transfer_all(inventory_folder(self.source),self.source/'clip',allow_same_volume=True)
        with self.assertRaisesRegex(TransferError,'different drive'):
            transfer_all(inventory_folder(self.source),self.destination)
        self.assert_source_unchanged()

    def test_space_check_prevents_copy(self):
        disk=shutil.disk_usage(self.destination)
        with patch('transfer.shutil.disk_usage',return_value=type(disk)(disk.total,disk.total,0)):
            with self.assertRaisesRegex(TransferError,'Not enough'):
                self.run_transfer()
        self.assert_source_unchanged()

    def test_live_session_lock(self):
        path=self.destination/'test.lock'
        with SessionLock(path):
            with self.assertRaisesRegex(TransferError,'Another transfer'):
                with SessionLock(path):
                    self.fail('second lock acquired')

    def test_detect_root_nested_and_reconnect(self):
        nested=self.root/'other/DCIM/100SIGMA/CINEMA'
        nested.mkdir(parents=True)
        self.assertEqual(find_cinema(self.root/'card'),[self.source])
        self.assertEqual(find_cinema(self.root/'other'),[nested])
        tracker=ArrivalTracker()
        device={'key':'E:123','path':str(self.source)}
        self.assertEqual(tracker.update([device]),[])
        self.assertEqual(tracker.update([device]),[])
        tracker.update([])
        self.assertEqual(tracker.update([device]),[device])

    def test_startup_command_is_hidden_and_quotes_paths(self):
        if os.name!='nt':
            self.skipTest('Windows feature')
        import base64
        import subprocess
        exe=self.root/"a user's folder/app.exe"
        shortcut=self.root/'startup/Ingest.lnk'
        with patch('devices.subprocess.run',return_value=subprocess.CompletedProcess([],0,b'',b'')) as run:
            set_startup(True,exe,shortcut)
            args=run.call_args.args[0]
            script=base64.b64decode(args[-1]).decode('utf-16le')
            self.assertIn("a user''s folder",script)
            self.assertIn("--watch",script)
            self.assertEqual(run.call_args.kwargs['creationflags'],subprocess.CREATE_NO_WINDOW)
        shortcut.touch()
        set_startup(False,shortcut=shortcut)
        self.assertFalse(shortcut.exists())

    def test_watcher_ignores_existing_volumes_and_waits_for_cinema(self):
        tracker=ArrivalTracker()
        source={'key':'clip-folder','volume_key':'E:123','path':str(self.source)}
        original={'key':'E:123','external':True}
        self.assertEqual(tracker.update({'sources':[],'volumes':[original]}),[])
        self.assertEqual(tracker.update({'sources':[source],'volumes':[original]}),[])
        tracker.update({'sources':[],'volumes':[]})
        self.assertEqual(tracker.update({'sources':[],'volumes':[original]}),[])
        self.assertEqual(tracker.update({'sources':[source],'volumes':[original]}),[source])
        tracker.update({'sources':[],'volumes':[original]})
        self.assertEqual(tracker.update({'sources':[source],'volumes':[original]}),[])

    def test_camera_extras_folder_cannot_collide_with_verification_files(self):
        (self.source/'Extras').mkdir()
        (self.source/'Extras/transfer.json').write_bytes(b'original camera metadata')
        result=self.run_transfer()
        session=Path(result['session'])
        self.assertEqual((session/'Extras/transfer.json').read_bytes(),b'original camera metadata')
        self.assertTrue((session/'Extras (checks)/checksums.sha256').is_file())
        self.assertEqual(self.run_transfer()['session'],str(session))


if __name__=='__main__':
    unittest.main(verbosity=2)
