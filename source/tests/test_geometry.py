import sys,unittest,tempfile,hashlib
from pathlib import Path
import numpy as np
import tifffile
SOURCE=Path(__file__).resolve().parents[1];sys.path.insert(0,str(SOURCE))
from dng_geometry import image_rectangle,visible_rectangle,crop_linear
from raw_library import Clip,load_metadata,decode_linear_xyz
from live_gpu import calibrate_bayer


class GeometryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=SOURCE/'tests');self.root=Path(self.temp.name)
        demo=next(p for p in (SOURCE.parent/'Demo/CINEMA').rglob('*') if p.suffix.lower()=='.dng')
        with tifffile.TiffFile(demo) as image:
            page=image.pages[0];self.pixels=page.asarray()
            self.tags=[(t.code,t.dtype,t.count,t.value,False) for t in page.tags.values() if t.code>=33421 and t.code not in (50719,50720,50829)]
    def tearDown(self):self.temp.cleanup()
    def write(self,name,origin,size,active=None):
        path=self.root/name
        tags=self.tags+[(50719,'I',2,origin,False),(50720,'I',2,size,False),(50829,'I',4,active or (0,0,*self.pixels.shape),False)]
        tifffile.imwrite(path,self.pixels,photometric=32803,metadata=None,extratags=tags)
        return path
    def test_each_clip_uses_its_own_crop_dimensions(self):
        a=self.write('a.dng',(8,5),(752,420));b=self.write('b.dng',(8,4),(640,360))
        ca=load_metadata(Clip(self.root,'A',[a]));cb=load_metadata(Clip(self.root,'B',[b]))
        self.assertEqual((ca.width,ca.height),(752,420));self.assertEqual((cb.width,cb.height),(640,360))
        _,ma=calibrate_bayer(a);_,mb=calibrate_bayer(b)
        self.assertEqual(ma.display_crop,(5,8,420,752));self.assertEqual(mb.display_crop,(4,8,360,640))
    def test_odd_crop_offset_preserves_half_resolution_and_raw_bytes(self):
        path=self.write('odd.dng',(8,5),(752,420));before=hashlib.sha256(path.read_bytes()).hexdigest()
        image=decode_linear_xyz(path,'Fast')
        self.assertEqual(image.shape,(210,376,3))
        self.assertEqual(before,hashlib.sha256(path.read_bytes()).hexdigest())
    def test_default_crop_is_relative_to_active_area(self):
        path=self.write('active.dng',(8,5),(640,360),(2,4,430,764))
        self.assertEqual(image_rectangle(path),(7,12,360,640))
        self.assertEqual(visible_rectangle(path,(2,4,428,760)),(5,8,360,640))
    def test_fractional_half_pixel_crop_interpolates_without_bayer_shift(self):
        image=np.repeat(np.arange(216,dtype=np.float32)[:,None,None],384,axis=1)
        result=crop_linear(image,(5,8,420,752),(432,768))
        self.assertEqual(result.shape,(210,376,1))
        self.assertAlmostEqual(float(result[0,0,0]),2.5)
        self.assertAlmostEqual(float(result[-1,0,0]),211.5)
    def test_invalid_crop_falls_back_to_valid_active_area(self):
        path=self.write('invalid.dng',(999,999),(640,360),(2,4,430,764))
        self.assertEqual(image_rectangle(path),(2,4,428,760))

    def test_non_square_default_scale_changes_display_aspect_only(self):
        from dng_geometry import pixel_aspect
        path=self.root/'scaled.dng'
        tags=[t for t in self.tags if t[0]!=50718]+[(50719,'I',2,(8,4),False),(50720,'I',2,(640,360),False),(50718,'2I',2,(2,1,1,1),False)]
        tifffile.imwrite(path,self.pixels,photometric=32803,metadata=None,extratags=tags)
        before=hashlib.sha256(path.read_bytes()).hexdigest()
        self.assertEqual(pixel_aspect(path),2)
        image=decode_linear_xyz(path,'Balanced')
        self.assertEqual(image.shape[:2],(180,640))
        _,model=calibrate_bayer(path)
        self.assertEqual(model.pixel_aspect,2)
        self.assertEqual(before,hashlib.sha256(path.read_bytes()).hexdigest())
