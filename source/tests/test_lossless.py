import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
import numpy as np
import tifffile

SOURCE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(SOURCE))
from live_gpu import calibrate_bayer,unpack_bayer,PackedBayer


class LosslessTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=SOURCE/'tests')
        self.root=Path(self.temp.name)
        demo=next((SOURCE.parent/'Demo/CINEMA').glob('*/*.dng'),None)
        if demo is None:
            demo=next(p for p in (SOURCE.parent/'Demo/CINEMA').rglob('*') if p.suffix.lower()=='.dng')
        with tifffile.TiffFile(demo) as image:
            page=image.pages[0]
            self.sensor=page.asarray()
            self.tags=[(tag.code,tag.dtype,tag.count,tag.value,False) for tag in page.tags.values() if tag.code>=33421]

    def tearDown(self):
        self.temp.cleanup()

    def write(self,name,compression=None):
        path=self.root/name
        tifffile.imwrite(path,self.sensor,photometric=32803,metadata=None,extratags=self.tags,
            compression=compression,compressionargs={'lossless':True,'bitspersample':12} if compression else None,
            bitspersample=12,rowsperstrip=self.sensor.shape[0])
        return path

    def test_packed_first_frame_switches_to_lossless_jpeg(self):
        first=self.write('first.dng')
        compressed=self.write('second.dng','jpeg')
        before=hashlib.sha256(compressed.read_bytes()).hexdigest()
        expected,model=calibrate_bayer(first)
        packed,_=unpack_bayer(first,model,True)
        self.assertIsInstance(packed,PackedBayer)
        pixels,_=unpack_bayer(compressed,model,True)
        np.testing.assert_array_equal(pixels,expected)
        self.assertEqual({layout[2] for layout,valid in model.verified_layouts.items() if valid},{1,7})
        self.assertEqual(before,hashlib.sha256(compressed.read_bytes()).hexdigest())

    def test_compressed_first_frame_and_repeated_decode(self):
        path=self.write('lossless.dng','jpeg')
        reference,model=calibrate_bayer(path)
        for _ in range(3):
            pixels,_=unpack_bayer(path,model,True)
            np.testing.assert_array_equal(pixels,reference)
        self.assertEqual(model.direct_compression,7)


if __name__=='__main__':
    unittest.main()
