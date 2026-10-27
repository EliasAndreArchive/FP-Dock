import os,sys,time,unittest
os.environ['QT_QPA_PLATFORM']='offscreen'
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication
from playback_slider import PlaybackSlider


class PlayheadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def test_intermediate_positions_are_monotonic_and_seek_value_stays_exact(self):
        slider=PlaybackSlider();slider.setRange(0,100)
        slider.sync(20);slider.sync(21,True,1/24)
        positions=[slider.position_at(slider.started+x/240) for x in range(11)]
        self.assertTrue(all(a<=b for a,b in zip(positions,positions[1:])))
        self.assertGreater(positions[5],20);self.assertLess(positions[5],21)
        self.assertEqual(slider.value(),21)
        slider.timer.stop();slider.deleteLater()

    def test_loop_and_paused_seek_snap_without_animating_backwards(self):
        slider=PlaybackSlider();slider.setRange(0,100)
        slider.sync(99);slider.sync(0,True,1/24)
        self.assertEqual(slider.position_at(),0);self.assertFalse(slider.timer.isActive())
        slider.sync(50,False)
        self.assertEqual(slider.position_at(),50)
        self.assertEqual(slider.value(),50)
        slider.deleteLater()
