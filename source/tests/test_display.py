import sys,unittest
from pathlib import Path
import numpy as np
SOURCE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(SOURCE))
from preview_color import REC709,VIEWS,intermediate_encode,dwg_intermediate_to_rec709,DWG_TO_REC709
from raw_library import develop_frame


class DisplayTests(unittest.TestCase):
    def test_neutral_highlights_roll_off_without_a_hard_white_clip(self):
        levels=np.array([0,.18,.6,1,2,4,8,16],np.float32)
        neutral=np.repeat(levels[:,None],3,axis=1)
        result=dwg_intermediate_to_rec709(intermediate_encode(neutral))
        self.assertTrue(np.all(np.diff(result[:,0])>0))
        self.assertTrue(np.all(result[-1]<1))
        np.testing.assert_allclose(result[1],.18**(1/2.4),atol=2e-4)
        self.assertNotIn('sRGB display',VIEWS)

    def test_out_of_gamut_highlights_are_compressed_into_display_range(self):
        target=np.array([[-.3,.4,8],[10,-.1,.2],[.1,5,-1]],np.float32)
        dwg=target@np.linalg.inv(DWG_TO_REC709).T
        result=dwg_intermediate_to_rec709(intermediate_encode(dwg))
        self.assertTrue(np.isfinite(result).all())
        self.assertTrue(np.all((result>0)&(result<1)))

    def test_cpu_scaling_changes_pixel_count_and_keeps_metered_exposure(self):
        path=next(p for p in (SOURCE.parent/'Demo/CINEMA').rglob('*') if p.suffix.lower()=='.dng')
        half,ev=develop_frame(path,'Fast',REC709,True,preview_size=(960,640),preview_reduction=2)
        quarter,ev2=develop_frame(path,'Fast',REC709,True,preview_size=(480,320),preview_reduction=4)
        eighth,ev3=develop_frame(path,'Fast',REC709,True,preview_size=(240,160),preview_reduction=8)
        self.assertEqual(half.shape[1],quarter.shape[1]*2)
        self.assertEqual(half.shape[0],eighth.shape[0]*4)
        self.assertEqual(ev,ev2)
        self.assertEqual(ev,ev3)
