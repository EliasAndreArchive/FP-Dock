import os,sys,time,tempfile,json,unittest
os.environ['QT_QPA_PLATFORM']='offscreen'
from pathlib import Path
from unittest.mock import patch
import test_ui as fixtures
PACKAGE=fixtures.PACKAGE
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt,QPoint,QPointF,QPropertyAnimation
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication,QLabel
from ingest import Ingest
from preview_color import DAVINCI,REC709,default_output_view


class BrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.UiTests.setUpClass.__func__(cls)
    setUp=fixtures.UiTests.setUp
    tearDown=fixtures.UiTests.tearDown
    wait=fixtures.UiTests.wait
    load_demo=fixtures.UiTests.load_demo
    def test_space_controls_playback_without_changing_checked_clips(self):
        self.load_demo();w=self.window
        before=set(w.selected_keys)
        w.grid.setFocus()
        QTest.keyClick(w.grid,Qt.Key_Space)
        self.assertTrue(w.preview.playing)
        QTest.keyClick(w.grid,Qt.Key_Space)
        self.assertFalse(w.preview.playing)
        self.assertEqual(w.selected_keys,before)

    def test_checkbox_toggles_highlighted_group_and_preserves_other_clips(self):
        from ingest import IngestCards
        self.load_demo();w=self.window
        w.grid.clearSelection()
        for i in range(3):w.grid.item(i).setSelected(True)
        item=w.grid.item(1)
        point=IngestCards.check_rect(w.grid.visualItemRect(item)).center().toPoint()
        QTest.mouseClick(w.grid.viewport(),Qt.LeftButton,Qt.NoModifier,point)
        self.assertTrue(all(w.grid.item(i).checkState()==Qt.Unchecked for i in range(3)))
        self.assertEqual(w.grid.item(3).checkState(),Qt.Checked)
        self.assertFalse(hasattr(w,'mark_selected'))
        self.assertFalse(hasattr(w,'unmark_selected'))
        self.assertEqual(w.hint.parent(),w.grid.parent())

    def test_loading_hover_and_reveal_animations_finish(self):
        w=self.window
        w.load_source(PACKAGE/'Demo/CINEMA')
        self.assertTrue(w.empty.active)
        self.assertTrue(w.empty.timer.isActive())
        self.wait(lambda:w.inventory is not None and not w.thumbnails_loading)
        self.assertFalse(w.empty.timer.isActive())
        self.assertTrue(w.grid.motion_timer.isActive())
        self.wait(lambda:not w.grid.motion_timer.isActive(),2)
        self.assertEqual(w.grid.reveal_progress(0),1)
        w.grid.set_hover(0)
        self.wait(lambda:w.grid.hover_value(0)>=.99,1)
        w.grid.set_hover(-1)
        self.wait(lambda:not w.grid.motion_timer.isActive(),1)
        self.assertEqual(w.grid.hover_value(0),0)

    def test_mouse_wheel_scrolls_through_intermediate_pixel_values(self):
        w=self.window
        self.load_demo()
        w.resize(1180,720)
        w.grid.setFixedHeight(280)  # Ensure overflow independently of footer / window layout.
        self.app.processEvents()
        bar=w.grid.verticalScrollBar()
        self.assertGreater(bar.maximum(),0)
        event=QWheelEvent(QPointF(100,100),QPointF(100,100),QPoint(),QPoint(0,-120),Qt.NoButton,Qt.NoModifier,Qt.NoScrollPhase,False)
        w.grid.wheelEvent(event)
        self.assertEqual(w.grid.scroll_animation.state(),QPropertyAnimation.Running)
        self.wait(lambda:bar.value()>0,1)
        self.assertLess(bar.value(),112)
        self.wait(lambda:w.grid.scroll_animation.state()!=QPropertyAnimation.Running,1)
        self.assertEqual(bar.value(),min(112,bar.maximum()))

    def test_disconnect_clears_stale_clips_and_ignores_old_load_result(self):
        w=self.window
        self.load_demo()
        self.assertEqual(w.grid.count(),12)
        old_generation=w.generation
        w.disconnect_source()
        w.source_ready((False,"[WinError 3] file missing"),old_generation)
        self.assertEqual(w.grid.count(),0)
        self.assertIsNone(w.preview.clip)
        self.assertIsNone(w.inventory)
        self.assertIn('disconnected',w.header_notice.text())
        self.assertNotIn('disconnected',w.status.text())
        self.assertNotIn('WinError',w.empty.text())
        self.assertFalse(w.empty.active)

    def test_brand_and_fixed_bundled_output_preview(self):
        w=self.window
        self.assertEqual(w.windowTitle(),'FP Dock')
        self.assertFalse(hasattr(w.preview,'view_combo'))
        self.assertEqual(w.preview_view,default_output_view())
        w.preview.set_visual_settings(REC709,True)
        self.assertEqual(w.preview.view,default_output_view())
        self.assertFalse(hasattr(w.preview,'lut_button'))
        self.assertFalse(hasattr(w,'choose_display_lut'))
        self.assertFalse(any(label.pixmap() and not label.pixmap().isNull() for label in w.header.findChildren(QLabel)))

    def test_cards_stay_hidden_until_all_thumbnails_are_ready(self):
        w=self.window
        w.load_source(PACKAGE/'Demo/CINEMA')
        self.wait(lambda:w.inventory is not None)
        self.assertTrue(w.grid.isHidden())
        self.assertTrue(w.thumbnails_loading)
        self.wait(lambda:not w.thumbnails_loading)
        self.assertTrue(w.grid.isVisible())
        self.assertTrue(all(w.grid.item(i).data(Qt.UserRole+2) for i in range(w.grid.count())))

    def test_refresh_reuses_only_valid_in_memory_thumbnails(self):
        self.load_demo();w=self.window
        with patch('ingest.decode_frame',side_effect=AssertionError('Thumbnail should be in RAM')) as decoder:
            w.refresh()
            self.wait(lambda:w.inventory is not None and not w.thumbnails_loading)
            decoder.assert_not_called()

    def test_clip_loading_animation_precedes_playback(self):
        self.load_demo();p=self.window.preview
        p.open_clip(0,autoplay=True)
        self.assertTrue(p.buffering)
        self.assertIs(p.stack.currentWidget(),p.loading)
        self.assertTrue(p.loading.active)
        self.assertEqual(p.shown,0)
        self.wait(lambda:not p.buffering)
        self.assertIs(p.stack.currentWidget(),p.canvas)
        self.wait(lambda:p.shown>=3)
        p.pause()

    def test_high_refresh_timer_and_zero_size_entrance(self):
        from browser_motion import refresh_interval
        from types import SimpleNamespace
        widget=SimpleNamespace(screen=lambda:SimpleNamespace(refreshRate=lambda:180.0))
        self.assertEqual(refresh_interval(widget),5)
        self.assertEqual(self.window.grid.scroll_animation.timer.timerType(),Qt.PreciseTimer)
        self.window.grid._intro_started=time.monotonic()+1
        self.assertEqual(self.window.grid.reveal_progress(0),0)
