import os,sys,unittest,ctypes
from pathlib import Path
SOURCE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(SOURCE))
from devices import ArrivalTracker,volume_event,native_device_event


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.tracker=ArrivalTracker()
        self.source={'key':'I:camera:cinema','volume_key':'I:camera','root':'I:\\','path':'I:\\CINEMA'}
        self.volume={'key':'I:camera','root':'I:\\','external':True}
        self.snapshot={'sources':[self.source],'volumes':[self.volume]}

    def test_fast_reconnect_between_polls_same_letter_same_serial(self):
        self.assertEqual(self.tracker.update(self.snapshot),[])
        self.tracker.note_removal(['I:\\'])
        self.tracker.note_connection(['I:\\'])
        self.assertEqual(self.tracker.update(self.snapshot),[self.source])
        self.assertEqual(self.tracker.update(self.snapshot),[])

    def test_arrival_waits_through_unreadable_folder_and_volume(self):
        self.tracker.update({'sources':[],'volumes':[]})
        self.tracker.note_connection(['I:\\'])
        self.assertEqual(self.tracker.update({'sources':[],'volumes':[]}),[])
        for _ in range(5):
            self.assertEqual(self.tracker.update({'sources':[],'volumes':[self.volume]}),[])
        self.assertEqual(self.tracker.update(self.snapshot),[self.source])
        self.assertEqual(self.tracker.update(self.snapshot),[])

    def test_arrival_while_first_scan_runs_is_not_swallowed_by_baseline(self):
        self.tracker.note_connection(['I:\\'])
        self.assertEqual(self.tracker.update(self.snapshot),[self.source])

    def test_duplicate_native_messages_do_not_open_twice(self):
        self.tracker.update(self.snapshot)
        self.tracker.note_connection(['I:\\'])
        self.assertEqual(self.tracker.update(self.snapshot),[self.source])
        self.tracker.note_connection(['I:\\'])
        self.assertEqual(self.tracker.update(self.snapshot),[])

    def test_poll_detected_arrival_is_not_reopened_by_later_native_message(self):
        self.tracker.update({'sources':[],'volumes':[]})
        self.assertEqual(self.tracker.update(self.snapshot),[self.source])
        self.tracker.note_connection(['I:\\'])
        self.assertEqual(self.tracker.update(self.snapshot),[])

    def test_multiple_drive_letters_and_non_volume_events(self):
        self.assertEqual(volume_event(0x8000,2,(1<<8)|(1<<9)),('arrival',['I:\\','J:\\']))
        self.assertEqual(volume_event(0x8004,2,1<<8),('removal',['I:\\']))
        self.assertIsNone(volume_event(0x8000,5,1<<8))
        self.assertIsNone(volume_event(0x8000,2,0))

    @unittest.skipUnless(os.name=='nt','Windows native notification')
    def test_windows_native_message_layout(self):
        from ctypes import wintypes
        volume=(ctypes.c_uint32*5)(20,2,0,1<<8,0)
        msg=wintypes.MSG()
        msg.message=0x219
        msg.wParam=0x8000
        msg.lParam=ctypes.addressof(volume)
        self.assertEqual(native_device_event(ctypes.addressof(msg)),('arrival',['I:\\']))
        from shiboken6 import VoidPtr
        self.assertEqual(native_device_event(VoidPtr(ctypes.addressof(msg))),('arrival',['I:\\']))
