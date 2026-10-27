import sys,tempfile,unittest
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from display_lut import load_cube,resolve_view
from preview_color import render_xyz,intermediate_encode,XYZ_TO_DWG,post_lut_exposure,meter_exposure_ev,pre_lut_highlights


def write_cube(path,size=3,nonlinear=False):
    rows=['LUT_3D_SIZE '+str(size),'DOMAIN_MIN 0 0 0','DOMAIN_MAX 1 1 1']
    for b in np.linspace(0,1,size):
        for g in np.linspace(0,1,size):
            for r in np.linspace(0,1,size):
                values=(r*r+.1*g*b,g*g+.1*r*b,b*b+.1*r*g) if nonlinear else (r,g,b)
                rows.append(' '.join(map(str,values)))
    path.write_text('\n'.join(rows),encoding='utf-8')
    return path


class CubeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.root=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()

    def test_identity_and_all_six_fractional_orderings(self):
        cube=load_cube(write_cube(self.root/'identity.cube'))
        rng=np.random.default_rng(43)
        samples=np.concatenate((rng.random((2000,3)),np.array([[0,0,0],[1,1,1],[1,0,1]])))
        np.testing.assert_allclose(cube.apply(samples),samples,atol=1e-7)

    def test_tetrahedral_interpolation_uses_four_vertices(self):
        cube=load_cube(write_cube(self.root/'nonlinear.cube',nonlinear=True))
        p=np.array([.4,.3,.1],np.float32)
        # Cell fractions .8,.6,.2: vertices 000,100,110,111.
        table=cube.table
        a,b,c,d=table[0,0,0],table[0,0,1],table[0,1,1],table[1,1,1]
        expected=a+.8*(b-a)+.6*(c-b)+.2*(d-c)
        np.testing.assert_allclose(cube.apply(p),expected,atol=1e-7)

    def test_output_lut_gets_intermediate_without_an_extra_gamma(self):
        path=write_cube(self.root/'identity.cube')
        xyz=np.array([[[.171,.18,.196],[.7,.8,.9]]],np.float32)
        before=xyz.copy();original=path.read_bytes()
        result=render_xyz(xyz,resolve_view(path))
        expected=np.round(np.clip(post_lut_exposure(intermediate_encode(pre_lut_highlights(xyz)@XYZ_TO_DWG.T),0),0,1)*255).astype(np.uint8)
        np.testing.assert_array_equal(result,expected)
        np.testing.assert_array_equal(xyz,before)
        self.assertEqual(path.read_bytes(),original)

    def test_invalid_luts_rejected(self):
        examples=['LUT_3D_SIZE 3\n0 0 0','LUT_3D_SIZE 66',
                  'LUT_1D_SIZE 2\n0 0 0\n1 1 1',
                  'LUT_3D_SIZE 2\nDOMAIN_MAX 0 0 0\n'+('0 0 0\n'*8),
                  'LUT_3D_SIZE 2\n'+('nan 0 0\n'*8)]
        for i,content in enumerate(examples):
            path=self.root/f'bad{i}.cube';path.write_text(content)
            with self.subTest(i=i),self.assertRaises(ValueError):load_cube(path)

    def test_resolve_input_range(self):
        path=write_cube(self.root/'range.cube',2)
        path.write_text(path.read_text().replace('DOMAIN_MIN 0 0 0\nDOMAIN_MAX 1 1 1','LUT_3D_INPUT_RANGE -1 1'))
        np.testing.assert_allclose(load_cube(path).apply([0,0,0]),[.5,.5,.5])

    def test_post_lut_brightness_keeps_highlights_separate_and_black_fixed(self):
        linear=np.array([0,.02,.18,.6,.9,.98,.999],np.float32)
        encoded=np.repeat((linear**(1/2.4))[:,None],3,axis=1)
        result=post_lut_exposure(encoded,3)
        self.assertTrue(np.all(np.diff(result[:,0])>0))
        self.assertEqual(result[0,0],0)
        self.assertLess(result[-1,0],1)
        self.assertGreater(result[2,0],encoded[2,0])

    def test_meter_runs_on_lut_output(self):
        path=write_cube(self.root/'identity.cube')
        xyz=np.broadcast_to(np.array([.095,.1,.109],np.float32),(16,16,3)).copy()
        view=resolve_view(path);ev=meter_exposure_ev(xyz,view)
        encoded=load_cube(path).apply(intermediate_encode(xyz@XYZ_TO_DWG.T))
        adjusted=post_lut_exposure(encoded,ev)
        y=adjusted**2.4@np.array([.2126,.7152,.0722],np.float32)
        np.testing.assert_allclose(np.max(adjusted),.98,atol=2e-5)


    def test_very_dark_post_lut_frame_is_lifted_beyond_four_stops(self):
        from unittest.mock import patch,Mock
        xyz=np.ones((16,16,3),np.float32)
        for middle in (.002,.00005):
            encoded=np.full_like(xyz,middle**(1/2.4))
            cube=Mock();cube.apply.return_value=encoded
            with patch('preview_color.is_resolve_view',return_value=True),patch('preview_color.view_cube',return_value=cube):
                ev=meter_exposure_ev(xyz,'test')
            self.assertGreater(ev,4)
            adjusted=post_lut_exposure(encoded,ev)
            np.testing.assert_allclose(np.max(adjusted),.98,atol=2e-5)


    def test_ettr_does_not_darkening_whole_scene_for_tiny_reflection(self):
        from preview_color import ettr_exposure_ev
        encoded=np.full((1000,3),.2,np.float32)
        encoded[0]=[.99,.8,.5]
        ev=ettr_exposure_ev(encoded)
        self.assertGreater(ev,0)
        result=post_lut_exposure(encoded,ev)
        self.assertLessEqual(float(result.max()),1)
        self.assertAlmostEqual(float(np.percentile(np.max(result,axis=1),99.5)),.98,places=4)

    def test_shadow_lift_opens_dark_detail_without_clipping_or_reversing_tones(self):
        linear=np.linspace(0,1,1000,dtype=np.float32)
        encoded=np.repeat(linear[:,None],3,axis=1)**(1/2.4)
        result=post_lut_exposure(encoded,0)**2.4
        self.assertTrue(np.all(np.diff(result[:,0])>0))
        self.assertGreater(result[20,0],3*linear[20])
        self.assertLess(abs(result[900,0]-linear[900]),.0001)
        self.assertEqual(result[0,0],0)
        self.assertEqual(result[-1,0],1)

    def test_pre_lut_highlights_preserve_mids_and_scene_highlight_order(self):
        linear=np.array([0,.02,.18,.35,.5,.7,.9,1,4,16],np.float32)
        xyz=np.repeat(linear[:,None],3,axis=1)
        adjusted=pre_lut_highlights(xyz)
        np.testing.assert_allclose(adjusted[:4,1],linear[:4],atol=1e-6)
        self.assertTrue(np.all(np.diff(adjusted[:,1])>0))
        self.assertLess(adjusted[7,1],.7)
        self.assertLess(adjusted[-1,1],1)

    def test_highlights_are_reduced_before_lut_and_exposure_is_after(self):
        from unittest.mock import patch,Mock
        xyz=np.full((16,16,3),4,np.float32)
        cube=Mock();cube.apply.return_value=np.full_like(xyz,.4)
        with patch('preview_color.is_resolve_view',return_value=True),patch('preview_color.view_cube',return_value=cube):
            result=render_xyz(xyz,'test',ev=2)
            meter_exposure_ev(xyz,'test')
        expected=intermediate_encode(pre_lut_highlights(xyz)@XYZ_TO_DWG.T)
        for call in cube.apply.call_args_list:np.testing.assert_allclose(call.args[0],expected)
        np.testing.assert_array_equal(result,np.round(post_lut_exposure(cube.apply.return_value,2)*255).astype(np.uint8))
