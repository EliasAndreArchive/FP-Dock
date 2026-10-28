import os
os.environ['QT_QPA_PLATFORM']='offscreen'
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

SOURCE=Path(__file__).resolve().parents[1]
PACKAGE=SOURCE.parent
sys.path.insert(0,str(SOURCE))
from ingest import QApplication, Ingest, STYLE, EXTRA_STYLE, load_fonts
from transfer import transfer_all


class UiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])
        load_fonts()
        cls.app.setStyle('Fusion')
        cls.app.setStyleSheet(STYLE+EXTRA_STYLE)

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=SOURCE/'tests')
        self.root=Path(self.temp.name)
        self.window=Ingest(self.root/'state',detect=False)
        self.window.show()

    def tearDown(self):
        self.window.quitting=True
        self.window.close()
        self.app.processEvents()
        self.temp.cleanup()

    def wait(self,fn,timeout=15):
        start=time.monotonic()
        while not fn():
            self.app.processEvents()
            time.sleep(.005)
            if time.monotonic()-start>timeout:
                self.fail('Timed out waiting for UI')
        self.app.processEvents()

    def load_demo(self):
        self.window.load_source(PACKAGE/'Demo/CINEMA')
        self.wait(lambda:self.window.inventory is not None and not self.window.thumbnails_loading)

    def test_grid_preview_and_verified_transfer(self):
        w=self.window
        self.assertFalse(w.transfer_button.isEnabled())
        self.load_demo()
        self.assertEqual(len(w.clips),12)
        self.assertTrue(w.grid.isWrapping())
        self.assertFalse(w.transfer_button.isEnabled())
        visible=[w.grid.visualItemRect(w.grid.item(i)) for i in range(12)]
        self.assertGreater(len({r.top() for r in visible}),1)
        w.search.setText('Last light')
        self.assertEqual(sum(not w.grid.item(i).isHidden() for i in range(12)),1)
        w.search.clear()
        w.open_preview(w.grid.item(0))
        p=w.preview
        self.wait(lambda:not p.canvas.image.isNull())
        self.wait(lambda:p.shown>=4)
        p.pause()
        p.seek(12)
        self.wait(lambda:p.shown==12)
        p.change(1)
        self.wait(lambda:p.position==1 and not p.canvas.image.isNull())
        p.pause()
        destination=self.root/'SSD'
        destination.mkdir()
        w.destination=str(destination)
        w.update_actions()
        self.assertTrue(w.transfer_button.isEnabled())
        def local_test_copy(*args,**kwargs):
            return transfer_all(*args,**kwargs,allow_same_volume=True)
        with patch('ingest.transfer_all',side_effect=local_test_copy):
            w.start_transfer()
            self.assertTrue(w.busy)
            self.assertTrue(w.cancel_button.isEnabled())
            self.assertFalse(w.preview.play.isEnabled())
            self.assertFalse(w.grid.isEnabled())
            self.wait(lambda:not w.busy,30)
        self.assertIsNotNone(w.last_result)
        self.assertEqual(w.last_result['status'],'verified')
        self.assertEqual(w.last_result['verified_files'],93)
        self.assertEqual(w.progress.value(),1000)
        self.assertIn('verified',w.status.text())

    def test_detect_reconnect_and_opt_in_watcher(self):
        w=self.window
        source={'path':str(PACKAGE/'Demo/CINEMA'),'root':'E:\\','key':'E:demo','label':'FP / SD'}
        with patch('ingest.set_startup') as startup:
            w.watch.setChecked(True)
            startup.assert_called_once_with(True)
            w.arrivals.update([])
            w.hide()
            w.devices_ready((True,[source]))
            self.wait(lambda:w.inventory is not None)
            self.assertTrue(w.isVisible())
            self.assertEqual(w.source_key,'E:demo')
            generation=w.generation
            w.devices_ready((True,[source]))
            self.assertEqual(w.generation,generation)
            w.devices_ready((True,[]))
            self.assertFalse(w.transfer_button.isEnabled())
            w.devices_ready((True,[source]))
            self.wait(lambda:w.inventory is not None and w.generation>generation)
            w.last_result={'session':str(self.root)}
            w.status.setText('All files verified')
            w.devices_ready((True,[]))
            self.assertTrue(w.transfer_button.isEnabled())
            self.assertIn('verified copy is still available',w.status.text())
            self.assertEqual(w.grid.count(),0)
            w.watch.setChecked(False)
            self.assertEqual(startup.call_args.args,(False,))
        self.assertFalse(w.settings['watch'])

    def test_real_mouse_checkboxes_and_bulk_selection(self):
        from PySide6.QtTest import QTest
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QAbstractItemView
        from ingest import IngestCards
        w=self.window
        self.load_demo()
        self.assertFalse(w.preview.isWindow())
        self.assertEqual(w.grid.selectionMode(),QAbstractItemView.ExtendedSelection)
        w.check_all(False)
        first=w.grid.item(0)
        point=IngestCards.check_rect(w.grid.visualItemRect(first)).center().toPoint()
        QTest.mouseClick(w.grid.viewport(),Qt.LeftButton,Qt.NoModifier,point)
        self.assertEqual(first.checkState(),Qt.Checked)
        self.assertEqual(len(w.selected_keys),1)
        QTest.mouseClick(w.grid.viewport(),Qt.LeftButton,Qt.NoModifier,point)
        self.assertEqual(first.checkState(),Qt.Unchecked)
        self.assertEqual(len(w.selected_keys),0)
        for i in (0,1,2):
            w.grid.item(i).setSelected(True)
        w.check_highlighted(True)
        self.assertEqual(len(w.selected_keys),3)
        w.grid.setFocus()
        QTest.keyClick(w.grid,Qt.Key_Space,Qt.ControlModifier)
        self.assertEqual(len(w.selected_keys),0)
        QTest.mouseClick(w.grid.viewport(),Qt.LeftButton,Qt.NoModifier,point)
        last=w.grid.item(3)
        end=IngestCards.check_rect(w.grid.visualItemRect(last)).center().toPoint()
        QTest.mouseClick(w.grid.viewport(),Qt.LeftButton,Qt.ShiftModifier,end)
        self.assertEqual(len(w.selected_keys),4)
        self.assertFalse((self.root/'state/previews').exists())

    def test_playback_scale_changes_live_and_persists(self):
        import json
        from preview_color import REC709
        w=self.window
        self.load_demo()
        p=w.preview
        self.wait(lambda:not p.canvas.image.isNull())
        self.wait(lambda:abs(p.stack.height()-p.stack.width()*p.canvas.image.height()/p.canvas.image.width())<=2)
        original=p.canvas.image.width()
        p.scale_combo.setCurrentIndex(1)
        self.wait(lambda:p.canvas.image.width()==original//2)
        self.assertEqual(w.settings['playback_scale'],4)
        self.assertEqual(json.loads(w.state_file.read_text())['playback_scale'],4)
        p.start()
        p.scale_combo.setCurrentIndex(2)
        self.wait(lambda:p.canvas.image.width()==original//4)
        self.assertTrue(p.playing)
        p.pause()

    def test_native_reconnect_reveals_and_stale_scan_is_ignored(self):
        w=self.window
        source={'path':str(PACKAGE/'Demo/CINEMA'),'root':'I:\\','key':'I:demo','volume_key':'I:card','label':'Camera'}
        snapshot={'sources':[source],'volumes':[{'key':'I:card','root':'I:\\','external':True}]}
        w.arrivals.update(snapshot)
        w.watch.blockSignals(True)
        w.watch.setChecked(True)
        w.watch.blockSignals(False)
        old=w.device_revision
        w.device_event('removal',['I:\\'])
        w.device_event('arrival',['I:\\'])
        w.devices_ready((True,snapshot),old)
        w.device_debounce.stop()
        self.assertIsNone(w.source)
        with patch.object(w,'reveal_window') as reveal,patch.object(w.tray,'showMessage') as notice:
            w.devices_ready((True,snapshot),w.device_revision)
            self.wait(lambda:w.inventory is not None)
            reveal.assert_called_once_with(attention=True)
            notice.assert_called_once()
            w.devices_ready((True,snapshot),w.device_revision)
            reveal.assert_called_once()

    def test_source_scan_error_is_not_transfer_ready(self):
        w=self.window
        w.load_source(self.root/'missing/CINEMA')
        self.wait(lambda:not w.scanning)
        self.assertIsNone(w.inventory)
        self.assertFalse(w.transfer_button.isEnabled())
        self.assertIn('disconnected',w.header_notice.text())
        self.assertNotIn('disconnected',w.status.text())

    def test_clip_checkboxes_and_visual_settings_preserve_selection(self):
        from PySide6.QtCore import Qt
        from preview_color import DAVINCI
        w=self.window
        self.load_demo()
        w.check_all(False)
        self.assertFalse(w.selected_keys)
        self.assertFalse(w.transfer_button.isEnabled())
        w.grid.item(0).setCheckState(Qt.Checked)
        self.assertEqual(len(w.selected_keys),1)
        self.assertEqual(len(w.selected_inventory.files),49)
        selected=set(w.selected_keys)
        self.assertFalse(hasattr(w.preview,'view_combo'))
        w.preview.set_visual_settings(w.preview_view,False)
        self.assertEqual(w.selected_keys,selected)
        from preview_color import default_output_view
        self.assertEqual(w.preview_view,default_output_view())
        self.assertTrue(w.auto_exposure)
        self.assertTrue(w.preview.auto_exposure)
        self.assertFalse(hasattr(w.preview,'auto_check'))
        w.search.setText('does not match the selected clip')
        self.assertEqual(w.selected_keys,selected)
        w.check_all(True)
        self.assertEqual(len(w.selected_keys),12)
        self.assertEqual(len(w.selected_inventory.files),93)

    def test_delete_warning_is_immediate_and_repeatable(self):
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QMessageBox
        self.load_demo()
        w=self.window
        seen=[]
        def cancel_warning():
            dialog=self.app.activeModalWidget()
            self.assertIsInstance(dialog,QMessageBox)
            self.assertIn('irreversible',dialog.informativeText())
            self.assertEqual(dialog.defaultButton(),dialog.button(QMessageBox.Cancel))
            seen.append(dialog)
            dialog.button(QMessageBox.Cancel).click()
        with patch('ingest.deletion_plan') as plan,patch('ingest.delete_files') as delete:
            for _ in range(3):
                QTimer.singleShot(0,cancel_warning)
                w.delete_selected()
                self.assertFalse(w.busy)
                self.assertIsNone(w.deletion_dialog)
            plan.assert_not_called()
            delete.assert_not_called()
        self.assertEqual(len(seen),3)

    def test_delete_validation_runs_after_confirmation_off_ui_thread(self):
        import threading
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QMessageBox
        self.load_demo()
        w=self.window
        ui_thread=threading.get_ident()
        worker_threads=[]
        def plan(*args):
            worker_threads.append(threading.get_ident())
            return ()
        def confirm_warning():
            dialog=self.app.activeModalWidget()
            for b in dialog.buttons():
                if dialog.buttonRole(b)==QMessageBox.DestructiveRole:b.click();return
            self.fail('Missing explicit delete button')
        with patch('ingest.deletion_plan',side_effect=plan),patch('ingest.delete_files',return_value=(0,'')) as delete:
            QTimer.singleShot(0,confirm_warning)
            w.delete_selected()
            self.wait(lambda:not w.busy)
            delete.assert_called_once()
        self.assertTrue(worker_threads)
        self.assertNotEqual(worker_threads[0],ui_thread)

    def test_confirm_really_deletes_only_disposable_selected_clip(self):
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QMessageBox
        from raw_library import Clip
        from transfer import inventory_folder
        w=self.window
        source=self.root/'CINEMA';source.mkdir()
        selected=source/'selected.dng';selected.write_bytes(b'disposable test frame')
        kept=source/'kept.dng';kept.write_bytes(b'keep this test frame')
        a=Clip(source,'Selected',[selected]);b=Clip(source,'Kept',[kept])
        w.source=source;w.inventory=inventory_folder(source);w.clips=[a,b];w.selected_keys={a.key}
        def confirm_warning():
            dialog=self.app.activeModalWidget()
            for button in dialog.buttons():
                if dialog.buttonRole(button)==QMessageBox.DestructiveRole:button.click();return
            self.fail('Missing delete confirmation')
        QTimer.singleShot(0,confirm_warning)
        w.delete_selected()
        self.wait(lambda:not w.busy)
        self.assertFalse(selected.exists())
        self.assertTrue(kept.exists())


if __name__=='__main__':
    unittest.main(verbosity=2)
