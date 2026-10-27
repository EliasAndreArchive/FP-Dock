"""Isolated hidden-window notification QA; never changes sign-in settings."""
import ctypes,json,sys
from ctypes import wintypes
from pathlib import Path
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from devices import DeviceEventFilter


def run(root,window_class):
    root=Path(root).resolve()
    root.mkdir(parents=True,exist_ok=True)
    app=QApplication([])
    app.setQuitOnLastWindowClosed(False)
    window=window_class(root/'state',watch_launch=True,detect=False)
    window.device_filter=DeviceEventFilter(window.device_event)
    app.installNativeEventFilter(window.device_filter)
    window.watch.blockSignals(True)
    window.watch.setChecked(True)
    window.watch.blockSignals(False)
    package=Path(sys.executable).parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parent.parent
    source={'path':str(package/'Demo/CINEMA'),'root':'I:\\','label':'QA camera','key':'I:demo','volume_key':'I:card'}
    snapshot={'sources':[source],'volumes':[{'key':'I:card','root':'I:\\','external':True}]}
    window.arrivals.update(snapshot)
    notices=[]
    revealed=[]
    window.tray.showMessage=lambda *args:notices.append(args)
    window.reveal_window=lambda attention=False:revealed.append(attention)
    window.device_debounce.timeout.disconnect()
    window.device_debounce.timeout.connect(lambda:window.devices_ready((True,snapshot),window.device_revision))
    user=ctypes.WinDLL('user32',use_last_error=True)
    user.SendMessageW.argtypes=[wintypes.HWND,wintypes.UINT,wintypes.WPARAM,wintypes.LPARAM]
    handle=int(window.winId())
    volume=(ctypes.c_uint32*5)(20,2,0,1<<8,0)
    result={}
    def send():
        user.SendMessageW(handle,0x219,0x8004,ctypes.addressof(volume))
        user.SendMessageW(handle,0x219,0x8000,ctypes.addressof(volume))
        result['native_revision']=window.device_revision
    def inspect():
        if window.inventory is None or 'generation' in result:
            return
        result['generation']=window.generation
        user.SendMessageW(handle,0x219,0x8000,ctypes.addressof(volume))
        QTimer.singleShot(400,app.quit)
    timer=QTimer()
    timer.timeout.connect(inspect)
    timer.start(10)
    QTimer.singleShot(50,send)
    QTimer.singleShot(10000,app.quit)
    app.exec()
    timer.stop()
    valid=(result.get('native_revision')==2 and window.inventory is not None and revealed==[True]
           and window.generation==result.get('generation') and len(notices)==1)
    result.update(frozen=bool(getattr(sys,'frozen',False)),hidden_native_notifications=valid,
                  clips=len(window.clips),notices=len(notices),reveals=len(revealed),duplicate_scan=False if valid else None)
    window.quitting=True
    window.close()
    app.processEvents()
    (root/'result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    return 0 if valid else 2
