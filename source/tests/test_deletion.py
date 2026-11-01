import tempfile
import threading
import unittest
from pathlib import Path
from raw_library import Clip
from transfer import inventory_folder
from deletion import deletion_plan,delete_files

class DeletionTests(unittest.TestCase):
    def fixture(self,root):
        a=root/'a.dng';b=root/'b.dng';audio=root/'a.wav';support=root/'notes.txt'
        for path in (a,b,audio,support):path.write_bytes(b"original")
        return Clip(root,'a',[a],audio=audio),Clip(root,'b',[b])

    def test_only_selected_media_deleted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);a,b=self.fixture(root);inv=inventory_folder(root)
            plan=deletion_plan(inv,[a,b],{a.key})
            self.assertEqual(delete_files(plan,inv.device,threading.Event()),(2,''))
            self.assertFalse(a.frames[0].exists());self.assertFalse(a.audio.exists())
            self.assertTrue(b.frames[0].exists());self.assertTrue((root/'notes.txt').exists())

    def test_changed_file_aborts_before_any_deletion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);a,b=self.fixture(root);inv=inventory_folder(root)
            plan=deletion_plan(inv,[a,b],{a.key})
            a.audio.write_bytes(b'changed size')
            count,error=delete_files(plan,inv.device,threading.Event())
            self.assertEqual(count,0);self.assertTrue(error);self.assertTrue(a.frames[0].exists())

    def test_cancel_preserves_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);a,b=self.fixture(root);inv=inventory_folder(root)
            event=threading.Event();event.set()
            count,error=delete_files(deletion_plan(inv,[a,b],{a.key}),inv.device,event)
            self.assertEqual(count,0);self.assertTrue(error);self.assertTrue(a.frames[0].exists())

    def test_shared_audio_is_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);a,b=self.fixture(root);b.audio=a.audio;inv=inventory_folder(root)
            count,error=delete_files(deletion_plan(inv,[a,b],{a.key}),inv.device,threading.Event())
            self.assertEqual(count,1);self.assertFalse(error);self.assertTrue(b.audio.exists())

    def test_dedicated_clip_folder_including_metadata_is_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);folder=root/'A001_001';folder.mkdir()
            frame=folder/'1.dng';frame.write_bytes(b'frame')
            (folder/'SPP_metadata.xmp').write_bytes(b'metadata')
            nested=folder/'support';nested.mkdir();(nested/'extra.txt').write_bytes(b'sidecar')
            clip=Clip(folder,'clip',[frame]);inv=inventory_folder(root)
            count,error=delete_files(deletion_plan(inv,[clip],{clip.key}),inv.device,threading.Event())
            self.assertFalse(error);self.assertEqual(count,3)
            self.assertFalse(folder.exists());self.assertTrue(root.exists())

    def test_shared_folder_preserves_metadata_and_other_clip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);folder=root/'shared';folder.mkdir()
            a,b=self.fixture(folder);inv=inventory_folder(root)
            count,error=delete_files(deletion_plan(inv,[a,b],{a.key}),inv.device,threading.Event())
            self.assertFalse(error);self.assertTrue(folder.exists())
            self.assertTrue(b.frames[0].exists());self.assertTrue((folder/'notes.txt').exists())
