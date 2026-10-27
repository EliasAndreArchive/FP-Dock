import sys,tempfile,unittest,struct
from pathlib import Path
from types import SimpleNamespace
import numpy as np,tifffile
SOURCE=Path(__file__).resolve().parents[1];sys.path.insert(0,str(SOURCE))
from export_naming import session_name,clip_recording_days,safe_label,movie_day


class NamingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=SOURCE/'tests');self.root=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()

    def test_requested_single_and_multiple_day_names_ignore_unselected_clips(self):
        a=SimpleNamespace(recording_days=('2026-09-26',))
        b=SimpleNamespace(recording_days=('2026-09-27','2026-09-29'))
        self.assertEqual(session_name([a],'Forest'),'2026-09-26 Forest')
        self.assertEqual(session_name([b],'Street clips'),'2026-09-27 - 2026-09-29 Street clips')
        self.assertEqual(session_name([a,b],'Trip'),'2026-09-26 - 2026-09-29 Trip')
        self.assertEqual(session_name([a],''),'2026-09-26')

    def test_metadata_is_used_instead_of_copy_time_and_includes_last_frame(self):
        paths=[]
        for i,date in enumerate(('2026:09:27 23:59:59','2026:09:28 00:00:01')):
            path=self.root/f'{i}.dng'
            tifffile.imwrite(path,np.zeros((4,4),np.uint16),datetime=date,metadata=None)
            paths.append(path)
        clip=SimpleNamespace(frames=paths)
        days,fallback=clip_recording_days(clip)
        self.assertEqual(days,('2026-09-27','2026-09-28'));self.assertFalse(fallback)
        self.assertEqual(clip.recorded_at,'2026-09-27 23:59:59')
        self.assertEqual(clip.recorded_until,'2026-09-28 00:00:01')
        from export_naming import recorded_label
        self.assertEqual(recorded_label(clip,compact=True),'27.09.2026 · 23:59')

    def test_missing_metadata_has_an_explicit_file_date_fallback(self):
        path=self.root/'clip.dng';tifffile.imwrite(path,np.zeros((4,4),np.uint16),metadata=None)
        days,fallback=clip_recording_days(SimpleNamespace(frames=[path]))
        self.assertTrue(fallback);self.assertRegex(days[0],r'^\d{4}-\d{2}-\d{2}$')
        self.assertEqual(safe_label('  Forest:/ odd? name. '),'Forest odd name')

    def test_movie_creation_metadata(self):
        from datetime import datetime,timezone
        seconds=int((datetime(2026,9,29,tzinfo=timezone.utc)-datetime(1904,1,1,tzinfo=timezone.utc)).total_seconds())
        data=struct.pack('>4sII',b'\0\0\0\0',seconds,seconds)
        mvhd=struct.pack('>I4s',8+len(data),b'mvhd')+data
        path=self.root/'clip.mov';path.write_bytes(struct.pack('>I4s',8+len(mvhd),b'moov')+mvhd)
        self.assertEqual(movie_day(path),'2026-09-29')
