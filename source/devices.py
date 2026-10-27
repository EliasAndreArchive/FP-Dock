"""Mounted-media discovery and optional per-user login startup."""
from __future__ import annotations

import base64
import ctypes
import os
import plistlib
import struct
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path
from PySide6.QtCore import QAbstractNativeEventFilter,QStorageInfo


def usb_drive(root):
    if os.name != "nt":
        return False
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateFileW("\\\\.\\" + str(root)[:2], 0, 3, None, 3, 0, None)
    if handle == ctypes.c_void_p(-1).value:
        return False
    try:
        query = ctypes.create_string_buffer(struct.pack("III", 0, 0, 0))
        output = ctypes.create_string_buffer(4096)
        returned = wintypes.DWORD()
        ok = kernel.DeviceIoControl(handle, 0x2D1400, query, len(query), output, len(output), ctypes.byref(returned), None)
        # STORAGE_DEVICE_DESCRIPTOR.BusType is a DWORD at byte 28. BusTypeUsb=7.
        return bool(ok and returned.value >= 32 and struct.unpack_from("I", output.raw, 28)[0] == 7)
    finally:
        kernel.CloseHandle(handle)


def mounted_sources():
    return storage_snapshot()["sources"]


def storage_snapshot():
    if os.name != "nt":
        return unix_storage_snapshot()
    kernel=ctypes.WinDLL("kernel32",use_last_error=True)
    kernel.SetThreadErrorMode.argtypes=[wintypes.DWORD,ctypes.POINTER(wintypes.DWORD)]
    previous=wintypes.DWORD()
    changed=kernel.SetThreadErrorMode(0x8001,ctypes.byref(previous))
    try:
        return _mounted_sources(snapshot=True)
    finally:
        if changed:
            kernel.SetThreadErrorMode(previous.value,None)



def unix_storage_snapshot():
    """Poll mounted media in standard macOS / Linux mount directories."""
    sources=[];volumes=[]
    prefixes=('/Volumes/',) if sys.platform=='darwin' else ('/media/','/run/media/','/mnt/')
    for volume in QStorageInfo.mountedVolumes():
        volume.refresh()
        if not volume.isValid() or not volume.isReady():continue
        root=volume.rootPath()
        if not root:continue
        external=not volume.isRoot() and root.startswith(prefixes)
        device=bytes(volume.device()).decode(errors='replace')
        key=f'{root}:{device}'
        volumes.append({'root':root,'key':key,'external':external})
        if not external:continue
        for cinema in find_cinema(Path(root)):
            sources.append({'path':str(cinema),'root':root,'label':volume.displayName() or 'Camera / SD card',
                            'volume_key':key,'key':f'{key}:{cinema}'})
    return {'sources':sources,'volumes':volumes}


def _mounted_sources(snapshot=False):
    """Only removable or USB-backed mounted volumes, including USB fixed disks."""
    if os.name != "nt":
        return []
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetLogicalDriveStringsW.argtypes = [wintypes.DWORD, wintypes.LPWSTR]
    kernel.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
    kernel.GetVolumeInformationW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR, wintypes.DWORD]
    buf = ctypes.create_unicode_buffer(1024)
    size = kernel.GetLogicalDriveStringsW(len(buf), buf)
    roots = buf[:size].split("\0")
    sources = []
    volumes = []
    for root in filter(None, roots):
        kind = kernel.GetDriveTypeW(root)
        if kind not in (2, 3):
            continue
        external=kind==2 or usb_drive(root)
        name, fs = ctypes.create_unicode_buffer(256), ctypes.create_unicode_buffer(64)
        serial, component, flags = wintypes.DWORD(), wintypes.DWORD(), wintypes.DWORD()
        kernel.GetVolumeInformationW(root, name, len(name), ctypes.byref(serial), ctypes.byref(component), ctypes.byref(flags), fs, len(fs))
        volume_key=f"{root.casefold()}:{serial.value}"
        volumes.append({"root":root,"serial":serial.value,"key":volume_key,"external":external})
        if not external:
            continue
        for cinema in find_cinema(Path(root)):
            sources.append({"path": str(cinema), "root": root, "label": name.value or "Camera / SD card",
                            "serial": serial.value,"volume_key":volume_key, "key": f"{root.casefold()}:{serial.value}:{str(cinema).casefold()}"})
    return {"sources":sources,"volumes":volumes} if snapshot else sources


def find_cinema(root, max_depth=4):
    """Look at the root and standard camera-media trees, never crawl an entire SSD."""
    found = []
    def walk(folder, depth):
        if depth > max_depth:
            return
        try:
            with os.scandir(folder) as scan:
                children = list(scan)
            for child in children:
                try:
                    if not child.is_dir(follow_symlinks=False) or child.is_symlink():
                        continue
                    info = child.stat(follow_symlinks=False)
                except OSError:
                    continue
                if getattr(info, "st_file_attributes", 0) & 0x400:
                    continue
                if child.name.casefold() == "cinema":
                    found.append(Path(child.path))
                elif depth > 0 or child.name.casefold() in ("dcim", "private"):
                    walk(Path(child.path), depth+1)
        except OSError:
            return
    walk(Path(root), 0)
    return sorted(found, key=lambda p: str(p).casefold())


class ArrivalTracker:
    def __init__(self):
        self.present = None
        self.eligible=set()
        self.opened={}
        self.volume_roots={}
        self.pending_roots=set()
        self.connection_marks={}

    @staticmethod
    def root_key(root):
        return str(root).replace('/','\\').rstrip('\\').casefold()

    def note_connection(self,roots):
        now=time.monotonic()
        for root in roots:
            root=self.root_key(root)
            if now-self.connection_marks.get(root,-10)<.75:
                continue
            self.pending_roots.add(root)
            self.connection_marks[root]=now

    def note_removal(self,roots):
        roots={self.root_key(root) for root in roots}
        for root in roots:
            self.connection_marks.pop(root,None)
        removed={volume for volume,root in self.volume_roots.items() if root in roots}
        self.pending_roots.difference_update(roots)
        self.eligible.difference_update(removed)
        self.opened={key:volume for key,volume in self.opened.items() if volume not in removed}
        if self.present is not None:
            self.present.difference_update(removed)

    def update(self, sources):
        if isinstance(sources,dict):
            snapshot=sources
            sources=snapshot["sources"]
            current={v["key"] for v in snapshot["volumes"]}
            self.volume_roots.update({v['key']:self.root_key(v['root']) for v in snapshot['volumes'] if v.get('root')})
        else:
            current={s.get("volume_key",s["key"]) for s in sources}
        self.volume_roots.update({s.get('volume_key',s['key']):self.root_key(s['root']) for s in sources if s.get('root')})
        pending={v for v in current if self.volume_roots.get(v) in self.pending_roots}
        if self.present is None:
            self.present=current
            self.opened={s["key"]:s.get("volume_key",s["key"]) for s in sources if s.get('volume_key',s['key']) not in pending}
        removed=self.present-current
        self.eligible.difference_update(removed)
        self.eligible.update(current-self.present)
        # An arrival can be a reinserted card on an unchanged drive letter/serial.
        # Only re-arm once for each native notification burst.
        for volume in pending:
            self.opened={key:v for key,v in self.opened.items() if v!=volume}
        self.eligible.update(pending)
        self.opened={key:volume for key,volume in self.opened.items() if volume not in removed}
        arrived=[]
        for source in sources:
            volume=source.get("volume_key",source["key"])
            if volume in self.eligible and source["key"] not in self.opened:
                arrived.append(source)
                self.opened[source["key"]]=volume
                root=self.volume_roots.get(volume)
                if root is not None:
                    self.connection_marks.setdefault(root,time.monotonic())
                self.pending_roots.discard(self.volume_roots.get(volume))
        self.present = current
        return arrived


def volume_event(event,device_type,mask):
    if device_type!=2 or event not in (0x8000,0x8004):
        return None
    roots=[chr(65+i)+':\\' for i in range(26) if mask&(1<<i)]
    return ('arrival' if event==0x8000 else 'removal',roots) if roots else None


def native_device_event(message):
    """Decode this application's WM_DEVICECHANGE; no registration is needed for volumes."""
    if os.name!='nt' or message is None or int(message)==0:
        return None
    msg=ctypes.cast(int(message),ctypes.POINTER(wintypes.MSG)).contents
    if msg.message!=0x219:
        return None
    if msg.wParam==7:
        return ('refresh',[])
    if msg.wParam not in (0x8000,0x8004) or not msg.lParam:
        return None
    address=int(msg.lParam)
    size=ctypes.c_uint32.from_address(address).value
    kind=ctypes.c_uint32.from_address(address+4).value
    if size<20 or kind!=2:
        return None
    return volume_event(msg.wParam,kind,ctypes.c_uint32.from_address(address+12).value)


class DeviceEventFilter(QAbstractNativeEventFilter):
    def __init__(self,callback):
        super().__init__()
        self.callback=callback

    def nativeEventFilter(self,event_type,message):
        event=native_device_event(message)
        if event:
            self.callback(*event)
        return False,0


def request_foreground(window_id):
    """Own-window activation, with a temporary visible lift if Windows denies focus."""
    if os.name!='nt':
        return None
    from PySide6.QtCore import QTimer
    user=ctypes.WinDLL('user32',use_last_error=True)
    user.SetForegroundWindow.argtypes=[wintypes.HWND]
    user.SetWindowPos.argtypes=[wintypes.HWND,wintypes.HWND,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,wintypes.UINT]
    user.IsWindow.argtypes=[wintypes.HWND]
    if user.SetForegroundWindow(window_id):
        return True
    flags=0x0001|0x0002|0x0010|0x0040
    user.SetWindowPos(window_id,wintypes.HWND(-1),0,0,0,0,flags)
    def release():
        if user.IsWindow(window_id):
            user.SetWindowPos(window_id,wintypes.HWND(-2),0,0,0,0,flags & ~0x0040)
    QTimer.singleShot(1000,release)
    return False


def startup_shortcut_path():
    return Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Startup/FP Dock.lnk"


def set_startup(enabled, executable=None, shortcut=None):
    if os.name != "nt":
        return set_unix_startup(enabled,executable,shortcut)
    custom=shortcut is not None
    shortcut = Path(shortcut) if custom else startup_shortcut_path()
    legacy=[shortcut.parent/'Visual Field Notes Ingest.lnk',shortcut.parent/'Sigma FP Dock.lnk']
    if not enabled:
        shortcut.unlink(missing_ok=True)
        if not custom:
            for path in legacy:path.unlink(missing_ok=True)
        return
    if not getattr(sys, "frozen", False) and executable is None:
        raise OSError("Enable automatic watching in FPDock.exe.")
    exe = Path(executable or sys.executable).resolve()
    shortcut.parent.mkdir(parents=True, exist_ok=True)
    def quote(value):
        return "'" + str(value).replace("'", "''") + "'"
    script = ("$ErrorActionPreference='Stop'; $shell=New-Object -ComObject WScript.Shell; "
        f"$link=$shell.CreateShortcut({quote(shortcut)}); $link.TargetPath={quote(exe)}; "
        f"$link.Arguments='--watch'; $link.WorkingDirectory={quote(exe.parent)}; "
        f"$link.IconLocation={quote(str(exe)+',0')}; $link.WindowStyle=7; "
        "$link.Description='Detect connected cameras and SD cards with a CINEMA folder'; $link.Save()")
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand",
                             base64.b64encode(script.encode("utf-16le")).decode()],
                            capture_output=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise OSError(result.stderr.decode(errors="replace").strip() or "Could not create the Windows startup shortcut.")
    if not custom:
        for path in legacy:path.unlink(missing_ok=True)


def set_unix_startup(enabled,executable=None,target=None):
    mac=sys.platform=='darwin'
    if target is None:
        target=(Path.home()/'Library/LaunchAgents/space.eliasandre.fpdock.plist') if mac else (
            Path(os.environ.get('XDG_CONFIG_HOME',str(Path.home()/'.config')))/'autostart/FP-Dock.desktop')
    target=Path(target)
    if not enabled:
        target.unlink(missing_ok=True);return
    if not getattr(sys,'frozen',False) and executable is None:
        raise OSError('Enable automatic watching in a packaged FP Dock application.')
    exe=Path(executable or sys.executable).resolve()
    target.parent.mkdir(parents=True,exist_ok=True)
    if mac:
        target.write_bytes(plistlib.dumps({'Label':'space.eliasandre.fpdock','ProgramArguments':[str(exe),'--watch'],
            'RunAtLoad':True,'ProcessType':'Interactive','WorkingDirectory':str(exe.parent)}))
    else:
        # Desktop-entry Exec quoting is not shell quoting.
        quoted='"'+str(exe).replace('%','%%').replace('\\','\\\\').replace('"','\\"').replace('`','\\`').replace('$','\\$')+'"'
        target.write_text('[Desktop Entry]\nType=Application\nName=FP Dock\nExec='+quoted+' --watch\nTerminal=false\nX-GNOME-Autostart-enabled=true\n',encoding='utf-8')
