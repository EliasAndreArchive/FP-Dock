import hashlib
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np

SOURCE=Path(__file__).resolve().parents[1]
PACKAGE=SOURCE.parent
sys.path.insert(0,str(SOURCE))
from preview_color import intermediate_encode,intermediate_decode,film5_encode,auto_exposure_ev,render_xyz,DAVINCI,BLACKMAGIC,SRGB
from raw_library import scan_folder,develop_frame,Clip
from transfer import inventory_folder,select_inventory,transfer_all,TransferError


class ColourTests(unittest.TestCase):
    def test_official_intermediate_reference_and_roundtrip(self):
        values=np.array([0,.18,1,10,100],np.float32)
        np.testing.assert_allclose(intermediate_encode(values),[0,.336043,.513837,.756599,1],atol=1e-6)
        np.testing.assert_allclose(intermediate_decode(intermediate_encode(values)),values,atol=1e-4)

    def test_film5_reference(self):
        self.assertAlmostEqual(float(film5_encode(.18)),.38356164,places=6)
        self.assertAlmostEqual(float(film5_encode(0)),.09246575,places=6)

    def test_auto_exposure_is_fixed_and_lifts_dark_linear_frames(self):
        xyz=np.full((32,32,3),.02,np.float32)
        ev=auto_exposure_ev(xyz)
        self.assertGreater(ev,2)
        self.assertLessEqual(ev,4)
        self.assertGreater(render_xyz(xyz,DAVINCI,ev).mean(),render_xyz(xyz,DAVINCI,0).mean())
        self.assertEqual(auto_exposure_ev(np.zeros((32,32,3),np.float32)),0)

    def test_raw_visual_changes_leave_source_bytes_unchanged(self):
        clip=scan_folder(PACKAGE/'Demo/CINEMA')[0][0]
        path=clip.frames[0]
        before=hashlib.sha256(path.read_bytes()).hexdigest()
        original,_=develop_frame(path,'Fast',DAVINCI,False)
        automatic,ev=develop_frame(path,'Fast',DAVINCI,True)
        film,_=develop_frame(path,'Fast',BLACKMAGIC,True,ev)
        display,_=develop_frame(path,'Fast',SRGB,True,ev)
        self.assertEqual(automatic.dtype,np.uint8)
        self.assertGreater(automatic.mean(),original.mean())
        self.assertFalse(np.array_equal(automatic,film))
        self.assertFalse(np.array_equal(film,display))
        self.assertEqual(before,hashlib.sha256(path.read_bytes()).hexdigest())

    def test_direct_tiff_reader_matches_libraw_pixels(self):
        from live_gpu import calibrate_bayer,unpack_bayer
        clip=scan_folder(PACKAGE/'Demo/CINEMA')[0][0]
        _,model=calibrate_bayer(clip.frames[0])
        self.assertIsNotNone(model.direct_ifd)
        for path in clip.frames[:3]:
            fast,_=unpack_bayer(path,model)
            reference,_=unpack_bayer(path)
            np.testing.assert_array_equal(fast,reference)


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=SOURCE/'tests')
        self.root=Path(self.temp.name)
        self.source=self.root/'CINEMA'
        self.dest=self.root/'SSD'
        self.source.mkdir()
        self.dest.mkdir()
        for name in ['A_0001.dng','A_0002.dng','B_0001.dng','A.wav','B.wav','metadata.txt']:
            (self.source/name).write_bytes(('source '+name).encode())
        self.clips=scan_folder(self.source)[0]
        self.selected=next(c for c in self.clips if c.name=='A')

    def tearDown(self):
        self.temp.cleanup()

    def test_only_checked_sequence_and_audio_transfer(self):
        inventory=select_inventory(inventory_folder(self.source),self.clips,{self.selected.key})
        self.assertEqual({e.relative for e in inventory.files},{'A_0001.dng','A_0002.dng','A.wav','metadata.txt'})
        result=transfer_all(inventory,self.dest,allow_same_volume=True)
        copied=Path(result['session'])
        self.assertTrue((copied/'A.wav').exists())
        self.assertFalse((copied/'B.wav').exists())
        self.assertFalse((copied/'B_0001.dng').exists())
        self.assertEqual(result['verified_files'],4)
        again=transfer_all(inventory,self.dest,allow_same_volume=True)
        self.assertEqual(again['reused_files'],4)

    def test_new_frame_in_selected_clip_prevents_false_completion(self):
        inventory=select_inventory(inventory_folder(self.source),self.clips,{self.selected.key})
        def progress(info):
            if info['verified']==1:
                (self.source/'A_0003.dng').write_bytes(b'new frame')
        with self.assertRaisesRegex(TransferError,'changed during'):
            transfer_all(inventory,self.dest,progress=progress,allow_same_volume=True)

    def test_unselected_video_is_not_added_as_support_data(self):
        (self.source/'other.mov').write_bytes(b'not selected')
        inventory=select_inventory(inventory_folder(self.source),self.clips,{self.selected.key})
        self.assertNotIn('other.mov',{e.relative for e in inventory.files})
        with self.assertRaisesRegex(TransferError,'at least one'):
            select_inventory(inventory,self.clips,set())


class LiveRawTests(unittest.TestCase):
    def test_live_calibration_and_unpack_do_not_write_originals(self):
        from live_gpu import calibrate_bayer,unpack_bayer
        clip=scan_folder(PACKAGE/'Demo/CINEMA')[0][0]
        path=clip.frames[0]
        before=hashlib.sha256(path.read_bytes()).hexdigest()
        pixels,model=calibrate_bayer(path)
        unpacked,elapsed=unpack_bayer(path)
        np.testing.assert_array_equal(pixels,unpacked)
        self.assertEqual(pixels.dtype,np.uint16)
        self.assertEqual(model.camera_xyz.shape,(3,3))
        self.assertGreaterEqual(elapsed,0)
        self.assertEqual(before,hashlib.sha256(path.read_bytes()).hexdigest())


if __name__=='__main__':
    unittest.main(verbosity=2)
