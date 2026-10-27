import json
import plistlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from devices import set_unix_startup, unix_storage_snapshot


class FakeVolume:
    def __init__(self, root, is_root=False, ready=True):
        self.root, self.root_volume, self.ready = root, is_root, ready
    def refresh(self): pass
    def isValid(self): return True
    def isReady(self): return self.ready
    def isRoot(self): return self.root_volume
    def rootPath(self): return self.root
    def device(self): return b'/dev/test-card'
    def displayName(self): return 'SD card'


class PlatformTests(unittest.TestCase):
    def test_unix_media_mounts_are_filtered_and_identified(self):
        for platform, mount in (('darwin', '/Volumes/Camera'), ('linux', '/run/media/user/Camera')):
            with self.subTest(platform=platform), patch('devices.sys.platform', platform), \
                 patch('devices.QStorageInfo') as storage, patch('devices.find_cinema') as find:
                storage.mountedVolumes.return_value = [FakeVolume('/', True), FakeVolume(mount), FakeVolume('/unready', ready=False)]
                find.return_value = [Path(mount)/'CINEMA']
                snapshot = unix_storage_snapshot()
                self.assertEqual(len(snapshot['sources']), 1)
                self.assertEqual(snapshot['sources'][0]['root'], mount)
                self.assertEqual(snapshot['sources'][0]['volume_key'], snapshot['volumes'][1]['key'])
                find.assert_called_once_with(Path(mount))

    def test_launch_agent_uses_argument_array_and_can_be_removed(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp, patch('devices.sys.platform', 'darwin'):
            exe = Path(temp)/'app with spaces/FPDock'
            target = Path(temp)/'startup.plist'
            set_unix_startup(True, exe, target)
            data = plistlib.loads(target.read_bytes())
            self.assertEqual(data['ProgramArguments'], [str(exe.resolve()), '--watch'])
            self.assertTrue(data['RunAtLoad'])
            set_unix_startup(False, target=target)
            self.assertFalse(target.exists())

    def test_xdg_startup_quotes_executable_and_can_be_removed(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp, patch('devices.sys.platform', 'linux'):
            exe = Path(temp)/'app with spaces/FPDock'
            target = Path(temp)/'autostart/FP-Dock.desktop'
            set_unix_startup(True, exe, target)
            text = target.read_text()
            self.assertIn('Exec="', text)
            self.assertIn('" --watch\n', text)
            self.assertIn('Terminal=false', text)
            set_unix_startup(False, target=target)
            self.assertFalse(target.exists())
