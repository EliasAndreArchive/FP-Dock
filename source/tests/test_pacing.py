import sys,time,threading,unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
SOURCE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(SOURCE))
from PySide6.QtWidgets import QApplication
from raw_library import scan_folder
from live_worker import LiveRawWorker
from preview_color import DAVINCI


class BufferPacingTests(unittest.TestCase):
    def test_slow_frame_is_prepared_before_clock_starts_without_skips(self):
        app=QApplication.instance() or QApplication([])
        clip=scan_folder(SOURCE.parent/'Demo/CINEMA')[0][0]
        worker=LiveRawWorker()
        state={'buffering':True};shown=[];progress=[]
        def buffering(data,epoch):
            state['buffering']=data['active'];progress.append(data)
        worker.buffering.connect(buffering)
        worker.frameReady.connect(lambda image,index,epoch:shown.append(index) if not state['buffering'] else None)
        def delayed(path,*args):
            started=time.perf_counter()
            time.sleep(.2 if path==clip.frames[5] else .005)
            return np.zeros((216,384,3),np.uint8),time.perf_counter()-started
        with patch.object(LiveRawWorker,'_cpu_decode',side_effect=delayed):
            worker.start();worker.configure(clip,DAVINCI,True,True)
            try:
                deadline=time.monotonic()+6
                while len(shown)<14 and time.monotonic()<deadline:
                    app.processEvents();time.sleep(.001)
                self.assertEqual(shown[:14],list(range(14)))
                self.assertTrue(any(not x['active'] for x in progress))
                self.assertTrue(all(x['ready']<=x['target'] for x in progress))
            finally:
                worker.halt();worker.wait();app.processEvents()
