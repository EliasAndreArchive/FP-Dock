import tempfile,unittest
from pathlib import Path
import numpy as np
import tifffile
from raw_library import Clip,load_metadata,exposure_label

class ExposureMetadataTests(unittest.TestCase):
    def test_camera_tags_and_fractional_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            frame=Path(tmp)/'1.dng'
            tifffile.imwrite(frame,np.zeros((8,8),dtype=np.uint16),extratags=[
                (34855,'H',1,800,False),(33434,'2I',1,(1,48),False),(33437,'2I',1,(28,10),False)])
            clip=load_metadata(Clip(frame.parent,'clip',[frame]))
            self.assertEqual(clip.iso,800);self.assertAlmostEqual(clip.shutter,1/48)
            self.assertAlmostEqual(clip.aperture,2.8)
            self.assertEqual(exposure_label(clip),'ISO 800 · Shutter 1/48 s · Aperture f/2.8')
    def test_missing_tags_are_explicit(self):
        self.assertEqual(exposure_label(Clip(Path('.'),'clip',[Path('missing.dng')])),'ISO — · Shutter — · Aperture —')
