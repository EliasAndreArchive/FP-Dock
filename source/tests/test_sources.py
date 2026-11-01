import tempfile,threading,unittest
from pathlib import Path
from ingest import inspect_source

class SourceTests(unittest.TestCase):
    def test_any_folder_without_cinema(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'video.mp4').write_bytes(b'test')
            inventory,clips=inspect_source(root,threading.Event())
            self.assertEqual(inventory.source,root)
            self.assertEqual(len(clips),1)
    def test_ssd_root_finds_cinema(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);cinema=root/'CINEMA';cinema.mkdir()
            (cinema/'video.mp4').write_bytes(b'test')
            inventory,clips=inspect_source(root,threading.Event())
            self.assertEqual(inventory.source,cinema)
            self.assertEqual(len(clips),1)
    def test_multiple_cinema_folders_are_browsable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for name in ('CINEMA','cinema'):
                if (root/name).exists():continue
                (root/name).mkdir();(root/name/'video.mp4').write_bytes(b'test')
            inventory,clips=inspect_source(root,threading.Event())
            self.assertTrue(clips)
