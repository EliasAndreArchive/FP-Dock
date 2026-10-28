"""Visual Field Notes Ingest — preview a card, copy everything, verify every byte."""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

os.environ.setdefault("OMP_NUM_THREADS","1")
from theme import color as ui_color, set_theme

from PySide6.QtCore import Qt, QTimer, QThreadPool, QSize, QStandardPaths, QUrl, QObject, Signal, QLockFile, QEvent, QRectF,QRect
from PySide6.QtGui import QImage, QPixmap, QIcon, QColor, QPainter, QFont, QDesktopServices, QShortcut, QKeySequence
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QListWidget, QListWidgetItem, QAbstractItemView, QLineEdit, QCheckBox, QFileDialog,
    QProgressBar, QFrame, QSystemTrayIcon, QMenu, QMessageBox, QSizePolicy, QComboBox, QSplitter,QStyle,QStyleOptionViewItem)
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from raw_library import Clip, scan_folder, decode_frame, natural_key
from transfer_history import find_transferred,ReadGate
from deletion import deletion_plan, delete_files
from transfer import inventory_folder, transfer_all, TransferCancelled, select_inventory
from preview_color import DAVINCI,default_output_view
from devices import storage_snapshot, ArrivalTracker, set_startup, find_cinema,DeviceEventFilter,request_foreground
from preview import Preview, VIDEO_EXTENSIONS
from visuals import STYLE, Job, Signals, label, button, brand_icon, load_fonts, ClipCardDelegate,ElidedLabel,WrappingPathLabel,SmoothCheckBox,animate_label_color,PANEL_PADDING,HEADER_HEIGHT,CONTENT_GAP
from browser_motion import APP_TITLE,MotionGrid,LoadingPanel,dock_icon
from video_thumb import VideoThumbnail
from export_naming import clip_recording_days,session_name,recorded_label,date_range

EXTRA_STYLE = """
QListWidget#sourceList::item { padding: 10px 12px; margin: 3px 0; border-radius: 17px; font-size: 11px; }
QListWidget#sourceList::item:selected { color: #fbfaf6; background: #1c1b17; }
QListWidget#sourceList::item:hover:!selected { background: #f0eee5; }
QWidget#side { border-right: 1px solid #e5e2d6; }
QFrame#transferBar { border-top: 1px solid #e5e2d6; }
QCheckBox { color: #8c8879; font-size: 10px; spacing: 7px; }
QCheckBox::indicator { width: 13px; height: 13px; border: 1px solid #c7c3b6; border-radius: 4px; background: #fbfaf6; }
QCheckBox::indicator:checked { background: #1c1b17; border-color: #1c1b17; }
QProgressBar { background: #e5e2d6; border: none; border-radius: 4px; height: 8px; max-height: 8px; }
QProgressBar::chunk { background: #1c1b17; border-radius: 2px; }
QPushButton#primary:disabled { background: #e5e2d6; color: #9a9686; border-color: #e5e2d6; }
QLabel#success { color: #447259; font-size: 12px; font-weight: 600; }
QLabel#error { color: #b84a2a; font-size: 12px; }
* { font-size: 13px; }
QLabel#muted { color: #625e52; font-size: 12px; }
QLabel#eyebrow { color: #6d685b; font-size: 10px; letter-spacing: .6px; }
QCheckBox { color: #625e52; font-size: 12px; }
QPushButton, QLineEdit, QComboBox { font-size: 12px; }
QListWidget#sourceList::item { font-size: 12px; padding: 9px 6px; }
"""


class IngestCards(ClipCardDelegate):
    checkClicked=Signal()

    def __init__(self,parent=None):
        super().__init__(parent)
        self._card_cache={}

    def clear_cache(self):self._card_cache.clear()

    def card_pixmap(self,painter,option,index):
        selected=bool(option.state & QStyle.State_Selected)
        icon=index.data(Qt.DecorationRole)
        dpr=painter.device().devicePixelRatioF()
        w,h=option.rect.width(),option.rect.height()
        key=(index.row(),selected)
        signature=(index.data(Qt.DisplayRole),icon.cacheKey() if icon else 0,w,h,dpr,ui_color("#ffffff").rgba())
        cached=self._card_cache.get(key)
        if cached is None or cached[0]!=signature:
            pixmap=QPixmap(round(w*dpr),round(h*dpr));pixmap.setDevicePixelRatio(dpr);pixmap.fill(Qt.transparent)
            local=QStyleOptionViewItem(option);local.rect=QRect(0,0,w,h)
            p=QPainter(pixmap);ClipCardDelegate.paint(self,p,local,index);p.end()
            if len(self._card_cache)>=128:self._card_cache.pop(next(iter(self._card_cache)))
            self._card_cache[key]=(signature,pixmap)
        return self._card_cache[key][1]

    def sizeHint(self,option,index):
        return QSize(180,148)

    @staticmethod
    def check_rect(rect):
        return QRectF(rect.right()-42,rect.top()+17,19,19)

    def paint(self,painter,option,index):
        owner=self.parent()
        progress=owner.reveal_progress(index.row())
        hover=owner.hover_value(index.row())
        painter.save()
        painter.setOpacity(painter.opacity()*progress)
        center=option.rect.center()
        painter.translate(center)
        painter.translate(0,8*(1-progress)-2*hover)
        painter.rotate(.65*hover)
        painter.scale(progress,progress)
        painter.translate(-center)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.drawPixmap(option.rect.topLeft(),self.card_pixmap(painter,option,index))
        painter.restore()
        painter.save()
        painter.setOpacity(painter.opacity()*progress)
        painter.setRenderHint(QPainter.Antialiasing)
        rect=self.check_rect(option.rect)
        checked=index.data(Qt.CheckStateRole)==Qt.Checked.value
        painter.setPen(ui_color("#fbfaf6"))
        painter.setBrush(ui_color("#1c1b17" if checked else "#fbfaf6"))
        painter.drawRoundedRect(rect,4,4)
        if checked:
            from PySide6.QtGui import QPen
            painter.setPen(QPen(ui_color("#fbfaf6"),1.7))
            painter.drawLine(int(rect.x()+4),int(rect.y()+10),int(rect.x()+8),int(rect.y()+14))
            painter.drawLine(int(rect.x()+8),int(rect.y()+14),int(rect.x()+15),int(rect.y()+5))
        if index.data(Qt.UserRole+3):
            badge=QRectF(option.rect.left()+13,option.rect.top()+17,91,20)
            painter.setPen(Qt.NoPen)
            painter.setBrush(ui_color("#fbfaf6"))
            painter.drawRoundedRect(badge,7,7)
            painter.setPen(ui_color("#1c1b17"))
            painter.setFont(QFont("Inter",8))
            painter.drawText(badge,Qt.AlignCenter,"Transferred ✓")
        painter.restore()

    def editorEvent(self,event,model,option,index):
        # The viewport owns checkbox input; do not rely on delegate edit triggers.
        return False


class IngestGrid(MotionGrid):
    checksChanged=Signal()
    playbackToggle=Signal()
    def __init__(self,parent=None):
        super().__init__(parent)
        self._pressed_check=None
        self._check_anchor=0

    def set_checks(self,items,checked):
        self.blockSignals(True)
        for item in items:
            item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
        self.blockSignals(False)
        self.checksChanged.emit()

    def mousePressEvent(self,event):
        item=self.itemAt(event.position().toPoint())
        if event.button()==Qt.LeftButton and item and IngestCards.check_rect(self.visualItemRect(item)).contains(event.position()):
            self._pressed_check=item
            event.accept()
            return
        self._pressed_check=None
        super().mousePressEvent(event)

    def mouseReleaseEvent(self,event):
        item=self.itemAt(event.position().toPoint())
        if self._pressed_check is not None:
            original=self._pressed_check
            self._pressed_check=None
            if event.button()==Qt.LeftButton and item is original and IngestCards.check_rect(self.visualItemRect(item)).contains(event.position()):
                row=self.row(item)
                checked=item.checkState()!=Qt.Checked
                if event.modifiers() & Qt.ShiftModifier:
                    first,last=sorted((self._check_anchor,row))
                    items=[self.item(i) for i in range(first,last+1)]
                else:
                    highlighted=self.selectedItems()
                    items=highlighted if item in highlighted and len(highlighted)>1 else [item]
                    self._check_anchor=row
                self.set_checks(items,checked)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self,event):
        if event.key()==Qt.Key_Space:
            if not event.modifiers() & Qt.ControlModifier:
                if not event.isAutoRepeat():
                    self.playbackToggle.emit()
                event.accept()
                return
            items=self.selectedItems()
            if not items and self.currentItem():
                items=[self.currentItem()]
            if items:
                self.set_checks(items,not all(i.checkState()==Qt.Checked for i in items))
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self,event):
        if event.key()==Qt.Key_Space:
            event.accept()
        else:
            super().keyReleaseEvent(event)


def size_text(n):
    for unit in ("B","KB","MB","GB","TB"):
        if n<1024 or unit=="TB":
            return f"{n:.1f} {unit}" if unit!="B" else f"{int(n)} B"
        n/=1024



def duration_text(seconds):
    seconds=max(0,int(round(seconds)))
    hours,seconds=divmod(seconds,3600);minutes,seconds=divmod(seconds,60)
    return f"{hours}:{minutes:02}:{seconds:02}" if hours else f"{minutes}:{seconds:02}"


def selected_duration_text(clips,keys):
    chosen=[clip for clip in clips if clip.key in keys]
    raw=[clip for clip in chosen if getattr(clip,'kind','raw')=='raw']
    text=duration_text(sum(clip.duration for clip in raw))
    return text+' + unknown' if len(raw)!=len(chosen) else text


def inspect_source(path,cancel):
    path=Path(path)
    if path.name.casefold()!="cinema":
        found=find_cinema(path)
        if len(found)!=1:
            raise OSError("Choose the specific CINEMA folder on the card.")
        path=found[0]
    inventory=inventory_folder(path,cancel)
    clips,errors=scan_folder(Path(path),cancel)
    if errors:
        raise OSError("Some clip folders could not be read: " + errors[0])
    videos=[entry for entry in inventory.files if Path(entry.relative).suffix.lower() in VIDEO_EXTENSIONS]
    for entry in videos:
        file=Path(path)/entry.relative
        c=Clip(file.parent,file.stem,[file])
        c.kind="video"
        clips.append(c)
    clips.sort(key=lambda c:natural_key(str(c.folder/c.name)))
    for clip in clips:
        if cancel and cancel.is_set():break
        clip.recording_days,clip.date_fallback=clip_recording_days(clip)
    return inventory,clips


class ProgressSignals(QObject):
    changed=Signal(object)


class Ingest(QMainWindow):
    def __init__(self,state_dir=None,watch_launch=False,detect=True):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.setWindowIcon(dock_icon())
        self.resize(1680,960)
        self.setMinimumSize(1180,720)
        self.setAcceptDrops(True)
        self.state_dir=Path(state_dir or QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation))
        self.state_dir.mkdir(parents=True,exist_ok=True)
        self.state_file=self.state_dir/"settings.json"
        try:
            self.settings=json.loads(self.state_file.read_text(encoding="utf-8"))
            if not isinstance(self.settings,dict):
                self.settings={}
        except (OSError,ValueError):
            self.settings={}
        set_theme(bool(self.settings.get("dark_mode",False)),STYLE+EXTRA_STYLE,animate=False)
        self.source=None
        self.inventory=None
        self.clips=[]
        self.detected=[]
        self.source_key=None
        self.manual_source=False
        self.destination=self.settings.get("destination","")
        self.jobs=set()
        self.generation=0
        self.scan_cancel=threading.Event()
        self.transfer_cancel=threading.Event()
        self.busy=False
        self.scanning=False
        self.disconnect_pending=False
        self.last_result=None
        self.preview=None
        self.thumb_pending=False
        self.thumbnails_loading=False
        self.thumbnail_cache={}
        self.thumbnail_stamps={}
        self.video_thumbnail=None
        self.detector_busy=False
        self.device_revision=0
        self.arrivals=ArrivalTracker()
        self.quitting=False
        self.watch_launch=watch_launch
        self.scan_pool=QThreadPool(self)
        self.scan_pool.setMaxThreadCount(1)
        self.thumb_pool=QThreadPool(self)
        self.thumb_pool.setMaxThreadCount(1)
        self.detect_pool=QThreadPool(self)
        self.detect_pool.setMaxThreadCount(1)
        self.history_cancel=threading.Event()
        self.history_paused=threading.Event()
        self.history_cache={}
        self.history_revision=0
        self.history_pool=QThreadPool(self)
        self.history_pool.setMaxThreadCount(1)
        self.transfer_pool=QThreadPool(self)
        self.transfer_pool.setMaxThreadCount(1)
        self.progress_signals=ProgressSignals(self)
        self.progress_signals.changed.connect(self.transfer_progress)
        self.last_progress=0
        self.selected_keys=set()
        self.selected_inventory=None
        self.last_check_click=0.0
        self.preview_view=default_output_view()
        self.settings.pop('display_lut_version',None)
        self.settings.pop('preview_view',None)
        self.auto_exposure=True
        self.settings['auto_exposure']=True
        self.build_ui()
        self.tray=QSystemTrayIcon(dock_icon(),self)
        self.tray.setToolTip(APP_TITLE)
        menu=QMenu(self)
        menu.addAction("Open ingest",self.reveal_window)
        menu.addAction("Quit",self.quit_app)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason:self.reveal_window() if reason in (QSystemTrayIcon.Trigger,QSystemTrayIcon.DoubleClick) else None)
        self.tray.messageClicked.connect(self.reveal_window)
        if self.settings.get("watch",False):
            self.tray.show()
        self.detect_timer=QTimer(self)
        self.detect_timer.setInterval(1000)
        self.detect_timer.timeout.connect(self.detect_devices)
        self.device_debounce=QTimer(self)
        self.device_debounce.setSingleShot(True)
        self.device_debounce.setInterval(150)
        self.device_debounce.timeout.connect(self.detect_devices)
        self.device_filter=None
        if detect:
            if os.name=='nt':
                self.device_filter=DeviceEventFilter(self.device_event)
                QApplication.instance().installNativeEventFilter(self.device_filter)
            self.winId()  # Hidden tray launches still need a native broadcast window.
            self.detect_timer.start()
            QTimer.singleShot(0,self.detect_devices)
        self.shortcuts=[]
        for key,callback in {"Ctrl+O":self.choose_source,"Ctrl+F":self.search.setFocus,"F5":self.refresh}.items():
            s=QShortcut(QKeySequence(key),self)
            s.activated.connect(callback)
            self.shortcuts.append(s)
        self.update_actions()

    def submit(self,pool,fn,callback):
        job=Job(fn,callback)
        self.jobs.add(job)
        job.signals.done.connect(lambda result,j=job:self.jobs.discard(j))
        pool.start(job)

    def save_settings(self):
        temp=self.state_file.with_suffix(".tmp")
        try:
            temp.write_text(json.dumps(self.settings,indent=2),encoding="utf-8")
            temp.replace(self.state_file)
        except OSError as exc:
            self.status.setText("Could not save preferences: "+str(exc))

    def build_ui(self):
        root=QWidget()
        outer=QVBoxLayout(root)
        outer.setContentsMargins(0,0,0,0)
        outer.setSpacing(0)
        self.header=QWidget()
        self.header.setObjectName("header")
        self.header.setStyleSheet('QWidget#header { border: none; }')
        footer=QVBoxLayout(self.header)
        footer.setContentsMargins(0,0,0,PANEL_PADDING)
        footer.setSpacing(CONTENT_GAP)
        h=QHBoxLayout();h.setSpacing(CONTENT_GAP)
        dock_title=label(APP_TITLE,"title")
        dock_title.setFixedHeight(HEADER_HEIGHT)
        h.addWidget(dock_title)
        h.addStretch()
        self.dark_mode=button("Dark mode",lambda:None,"Switch between light and dark appearance")
        self.dark_mode.setCheckable(True)
        self.dark_mode.setChecked(bool(self.settings.get("dark_mode",False)))
        self.dark_mode.toggled.connect(self.theme_changed)
        h.addWidget(self.dark_mode)
        self.watch=button("Auto-open",lambda:None)
        self.watch.setCheckable(True)
        self.watch.setCursor(Qt.PointingHandCursor)
        self.watch.setChecked(bool(self.settings.get("watch",False)))
        self.watch.setToolTip("Keep running in the tray and start at Windows sign-in. Opens for newly connected USB drives / SD cards with CINEMA.")
        self.watch.toggled.connect(self.watch_changed)
        h.addWidget(self.watch)
        footer.addLayout(h)
        self.connection_status=label('FP / SD card disconnected','muted')
        footer.addWidget(self.connection_status)
        self.header_notice=label('','muted');self.header_notice.setWordWrap(True);self.header_notice.hide()
        footer.addWidget(self.header_notice)
        self.notice_timer=QTimer(self);self.notice_timer.setSingleShot(True)
        self.notice_timer.timeout.connect(self.header_notice.hide)
        workspace=QHBoxLayout()
        workspace.setSpacing(0)
        side=QWidget()
        side.setObjectName("mediaPanel")
        side.setStyleSheet('QWidget#mediaPanel { border: none; }')
        s=QVBoxLayout(side)
        s.setContentsMargins(0,PANEL_PADDING,0,0)
        s.setSpacing(CONTENT_GAP)
        media_heading=QHBoxLayout()
        media_heading.setSpacing(CONTENT_GAP)
        export_heading=label("Save to","title")
        export_heading.setFixedHeight(HEADER_HEIGHT)
        media_heading.addWidget(export_heading,1)
        s.addLayout(media_heading)
        self.export_title=WrappingPathLabel(self.destination or 'Choose a destination')
        self.export_title.setObjectName('muted')
        self.export_title.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Preferred)
        self.export_title.setWordWrap(True)
        s.addWidget(self.export_title)
        self.device_label=label("Waiting for CINEMA","muted")
        self.device_label.setWordWrap(True)
        self.device_label.hide()
        s.addWidget(self.device_label)
        self.sources=ElidedLabel('No source selected')
        self.sources.setParent(side)
        self.sources.hide()
        self.sources.setObjectName('muted')
        self.sources.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Preferred)
        self.choose_source_button=button("Open…",self.choose_source,"Select a CINEMA folder manually (Ctrl+O)")
        self.content_splitter=QSplitter(Qt.Horizontal)
        main=QWidget()
        content=QVBoxLayout(main)
        content.setContentsMargins(PANEL_PADDING,PANEL_PADDING,PANEL_PADDING,16)
        content.setSpacing(CONTENT_GAP)
        top=QHBoxLayout()
        self.title=ElidedLabel("Your card, in full.")
        self.title.setFixedHeight(HEADER_HEIGHT)
        self.title.setObjectName('title')
        self.title.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Preferred)
        self.summary=label("Connect your FP or insert its SD card to get started.","muted")
        top.addWidget(self.title,1)
        top.addWidget(self.choose_source_button)
        self.summary.setWordWrap(False)
        self.hint=label("Choose clips to transfer","muted")
        self.hint.setParent(main)
        self.hint.hide()
        self.search=QLineEdit()
        self.search.setPlaceholderText("Find a clip…")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(160)
        self.search.textChanged.connect(self.filter_clips)
        top.addWidget(self.search)
        self.refresh_button=button("Refresh",self.refresh,"Refresh the source inventory (F5)")
        top.addWidget(self.refresh_button)
        content.addLayout(top)
        self.visual_row=QWidget()
        controls=QHBoxLayout(self.visual_row)
        controls.setContentsMargins(0,0,0,0)
        controls.addWidget(button("All",lambda:self.check_all(True)))
        controls.addWidget(button("None",lambda:self.check_all(False)))
        self.delete_button=button("Delete selected",self.delete_selected,"Permanently delete the checked clips from the source camera or card")
        controls.addWidget(self.delete_button)
        top.addWidget(self.visual_row)
        content.addWidget(self.summary)
        self.history_label=label("","muted")
        self.history_label.hide()
        content.addWidget(self.history_label)
        self.empty=LoadingPanel()
        content.addWidget(self.empty,1)
        self.grid=IngestGrid()
        self.grid.setViewMode(QListWidget.IconMode)
        self.grid.setFlow(QListWidget.LeftToRight)
        self.grid.setWrapping(True)
        self.grid.setResizeMode(QListWidget.Adjust)
        self.grid.setMovement(QListWidget.Static)
        self.grid.setUniformItemSizes(True)
        self.grid.setIconSize(QSize(176,99))
        self.grid.setGridSize(QSize(180,148))
        delegate=IngestCards(self.grid)
        self.grid.checksChanged.connect(self.update_selection)
        self.grid.playbackToggle.connect(self.toggle_current_preview)
        self.grid.setItemDelegate(delegate)
        self.grid.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.grid.setDragEnabled(False)
        self.grid.currentItemChanged.connect(self.preview_current)
        self.grid.itemClicked.connect(self.clip_clicked)
        self.grid.itemActivated.connect(self.open_preview)
        self.grid.itemChanged.connect(lambda _:self.update_selection())
        self.grid.verticalScrollBar().valueChanged.connect(lambda _:self.queue_thumbnail())
        self.grid.hide()
        content.addWidget(self.grid,1)
        self.content_splitter.addWidget(main)
        self.preview=Preview(parent=self,view=self.preview_view,auto_exposure=self.auto_exposure,reduction=self.settings.get('playback_scale',2))
        self.preview.add_control_panel(side)
        self.preview.scaleChanged.connect(self.playback_scale_changed)
        self.preview.setMinimumWidth(400)
        self.preview.visualsChanged.connect(self.apply_visuals)
        self.preview.playbackChanged.connect(lambda *_:self.update_history_pause())
        self.preview.bufferingChanged.connect(lambda *_:self.update_history_pause())
        self.preview.playbackChanged.connect(lambda playing:self.queue_thumbnail() if not playing else None)
        self.preview.bufferingChanged.connect(lambda loading:self.queue_thumbnail() if not loading else None)
        self.content_splitter.addWidget(self.preview)
        self.content_splitter.setStretchFactor(0,3)
        self.content_splitter.setStretchFactor(1,2)
        self.content_splitter.setSizes([900,570])
        workspace.addWidget(self.content_splitter,1)
        outer.addLayout(workspace,1)
        bar=QFrame()
        bar.setObjectName("exportPanel")
        b=QVBoxLayout(bar)
        b.setContentsMargins(0,0,0,0)
        b.setSpacing(CONTENT_GAP)
        self.destination_label=self.export_title
        self.destination_label.setToolTip(self.destination)
        self.destination_button=button("Browse…",self.choose_destination,'Choose a destination drive or folder')
        self.destination_button.setFixedHeight(HEADER_HEIGHT)
        media_heading.addWidget(self.destination_button)
        self.export_name=QLineEdit(self.settings.get('export_name',''))
        self.export_name.setPlaceholderText('Name your folder')
        self.export_name.setMaxLength(100)
        self.export_name.setMinimumWidth(100)
        self.export_name.setSizePolicy(QSizePolicy.Expanding,QSizePolicy.Fixed)
        self.export_name.setFixedHeight(HEADER_HEIGHT)
        self.export_name.textChanged.connect(self.export_name_changed)
        folder_row=QHBoxLayout()
        folder_row.setSpacing(CONTENT_GAP)
        folder_row.addWidget(label("Folder name","muted"))
        folder_row.addWidget(self.export_name,1)
        s.addLayout(folder_row)
        self.folder_preview=label('Select clips to see the folder name','muted')
        self.folder_preview.setWordWrap(True)
        self.transfer_button=button("Transfer all",self.start_transfer,"Copy every file, then compare SHA-256 hashes. Originals are never deleted.","primary")
        self.transfer_button.setFixedHeight(44)
        self.transfer_button.setSizePolicy(QSizePolicy.Expanding,QSizePolicy.Fixed)
        s.addWidget(self.folder_preview)
        self.cancel_button=button("Cancel",self.cancel_transfer)
        self.cancel_button.hide()
        self.progress=QProgressBar()
        self.progress.setRange(0,1000)
        self.progress.setTextVisible(False)
        self.progress.setValue(0)
        progress_row=QHBoxLayout()
        progress_row.addWidget(self.progress,1)
        self.progress_value=label('0%','muted')
        self.progress_value.setFixedWidth(36)
        progress_row.addWidget(self.progress_value)
        b.addLayout(progress_row)
        self.status=label("Waiting for a camera or card. Nothing is copied automatically.","muted")
        self.status.setWordWrap(True)
        b.addWidget(self.status)
        transfer_row=QHBoxLayout()
        transfer_row.addWidget(self.transfer_button)
        b.addLayout(transfer_row)
        cancel_row=QHBoxLayout()
        cancel_row.addStretch(1);cancel_row.addWidget(self.cancel_button);cancel_row.addStretch(1)
        b.addLayout(cancel_row)
        s.addStretch(1)
        s.addWidget(bar)
        preview_layout=self.preview.layout()
        if preview_layout.itemAt(preview_layout.count()-1).spacerItem():
            preview_layout.takeAt(preview_layout.count()-1)
        preview_layout.setStretch(preview_layout.indexOf(side),1)
        preview_layout.insertSpacing(preview_layout.indexOf(side),PANEL_PADDING-CONTENT_GAP)
        self.preview.divider_widgets=[(self.header,"bottom"),(side,"top")]
        preview_layout.insertWidget(0,self.header)
        preview_layout.insertSpacing(1,PANEL_PADDING-CONTENT_GAP)
        QTimer.singleShot(0,self.preview.fit_video)
        self.setCentralWidget(root)

    def detect_devices(self):
        if self.detector_busy or self.quitting:
            return
        self.detector_busy=True
        revision=self.device_revision
        self.submit(self.detect_pool,storage_snapshot,lambda result:self.devices_ready(result,revision))

    def device_event(self,action,roots):
        if self.quitting:
            return
        if action=='arrival':
            self.arrivals.note_connection(roots)
        elif action=='removal':
            self.arrivals.note_removal(roots)
            if self.source and any(ArrivalTracker.root_key(self.source.drive)==ArrivalTracker.root_key(root) for root in roots):
                if self.busy:
                    self.disconnect_pending=True
                    self.transfer_cancel.set()
                    self.preview.pause()
                    self.show_header_notice('FP / SD card disconnected — stopping safely')
                    self.update_header_connection()
                    self.status.setText('Stopping safely; verified files are kept for resume.')
                else:
                    self.disconnect_source()
        self.device_revision+=1
        logging.info('Device notification: %s %s revision=%s',action,roots,self.device_revision)
        self.device_debounce.start()

    def devices_ready(self,result,revision=None):
        self.detector_busy=False
        if self.quitting or not result[0]:
            return
        if revision is not None and revision!=self.device_revision:
            self.device_debounce.start(0)
            return
        snapshot=result[1]
        sources=snapshot["sources"] if isinstance(snapshot,dict) else snapshot
        arrivals=self.arrivals.update(snapshot)
        old=[s["key"] for s in self.detected]
        self.detected=sources
        self.update_header_connection()
        if old!=[s["key"] for s in sources]:
            self.rebuild_sources()
        self.device_label.setText(f"{len(sources)} CINEMA {'folder' if len(sources)==1 else 'folders'} detected" if sources else "Waiting for CINEMA")
        if arrivals:
            self.show_header_notice('Camera / SD connected')
            logging.info('CINEMA arrival: %s',[s['path'] for s in arrivals])
            if not self.busy:
                source=arrivals[0]
                self.load_source(source["path"],source["key"])
            if self.watch.isChecked():
                self.reveal_window(attention=True)
                self.tray.showMessage('Camera / card connected',arrivals[0]['root']+' · CINEMA is ready to preview or transfer.',QSystemTrayIcon.Information,6000)
        elif sources and self.source is None and self.isVisible() and not self.busy:
            source=sources[0]
            self.load_source(source["path"],source["key"])
        if self.source_key and self.source_key not in {s["key"] for s in sources}:
            if not self.busy:
                self.disconnect_source()
        else:
            self.update_actions()

    def disconnect_source(self):
        if self.busy:
            self.disconnect_pending=True
            return
        old_key=self.source_key
        self.history_cancel.set()
        self.scan_cancel.set()
        self.generation+=1
        self.scanning=False
        self.thumbnails_loading=False
        self.history_label.hide()
        self.source=None;self.source_key=None;self.manual_source=False
        self.inventory=None;self.clips=[];self.selected_keys=set();self.selected_inventory=None
        self.grid.clear();self.grid.hide()
        self.preview.clear_clip()
        self.empty.set_loading(False,'Connect your FP in USB storage mode, or insert its SD card.')
        self.show_header_notice('FP / SD card disconnected')
        self.empty.show()
        self.title.setText('No camera or SD card')
        self.summary.setText('Waiting for CINEMA')
        self.hint.setText('NO MEDIA CONNECTED')
        self.status.setText('Your verified copy is still available.' if self.last_result else '')
        self.detected=[s for s in self.detected if s['key']!=old_key]
        self.rebuild_sources()
        self.update_actions()

    def rebuild_sources(self):
        self.sources.setText(str(self.source) if self.source else 'No source selected')
        self.sources.setToolTip(self.sources.text())
        self.update_header_connection()

    def source_clicked(self,data):
        if not self.busy and data:
            self.load_source(data["path"],data["key"])

    def choose_source(self):
        path=QFileDialog.getExistingDirectory(self,"Choose the CINEMA folder")
        if path:
            self.load_source(path)

    def load_source(self,path,key=None):
        if self.busy:
            return
        if self.preview:
            self.preview.clear_clip()
        self.history_cancel.set()
        self.scan_cancel.set()
        self.scan_cancel=threading.Event()
        self.generation+=1
        generation=self.generation
        self.source=Path(path).resolve()
        self.source_key=key
        self.manual_source=key is None
        self.inventory=None
        self.last_result=None
        self.reset_transfer_progress()
        self.clips=[]
        self.selected_keys=set()
        self.selected_inventory=None
        self.history_label.hide()
        self.scanning=True
        self.thumbnails_loading=False
        self.grid.clear()
        self.grid.hide()
        self.empty.set_loading(True,'Reading CINEMA…\nFinding your clips and checking the card.')
        self.hint.setText('READING CINEMA…')
        self.empty.show()
        self.title.setText(str(self.source))
        self.title.setToolTip(str(self.source))
        self.summary.setText(str(self.source))
        self.summary.setToolTip(str(self.source))
        self.summary.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Preferred)
        self.status.setText("Reading the card. Original files are opened read-only.")
        self.rebuild_sources()
        self.update_actions()
        cancel=self.scan_cancel
        source=self.source
        self.submit(self.scan_pool,lambda:inspect_source(source,cancel),lambda result:self.source_ready(result,generation))

    def source_ready(self,result,generation):
        if self.quitting or generation!=self.generation:
            return
        self.scanning=False
        if not result[0]:
            if not self.source or not self.source.is_dir():
                self.disconnect_source()
                return
            self.empty.set_loading(False,'Couldn’t read this card.\nReconnect your Sigma FP or SD card, then try Refresh.')
            self.empty.setToolTip(result[1])
            self.status.setText("Could not read all files. No transfer started.")
            self.update_actions()
            return
        self.inventory,self.clips=result[1]
        self.thumbnail_stamps={str(self.inventory.source/e.relative).casefold():(e.size,e.mtime_ns) for e in self.inventory.files}
        self.source=self.inventory.source
        self.title.setText(str(self.source))
        self.title.setToolTip(str(self.source))
        self.rebuild_sources()
        if self.manual_source:
            self.device_label.setText("Manual folder selected")
        self.summary.setText(date_range(self.clips))
        self.summary.setToolTip(str(self.source))
        self.grid.blockSignals(True)
        for clip in self.clips:
            count=recorded_label(clip,compact=True)
            item=QListWidgetItem(self.placeholder(),clip.name+"\n"+count)
            item.setData(Qt.UserRole,clip)
            item.setFlags(item.flags()|Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            item.setToolTip(recorded_label(clip)+'\n'+str(clip.folder))
            self.grid.addItem(item)
        self.grid.blockSignals(False)
        self.thumbnails_loading=bool(self.clips)
        self.grid.hide()
        self.empty.set_loading(bool(self.clips),f'Loading clip thumbnails…\n0 / {len(self.clips)} ready')
        self.empty.show()
        if not self.clips:self.empty.setText('No previewable clips found.\nAll files can still be transferred and verified.')
        self.status.setText(f"Ready. All {len(self.inventory.files):,} files will be copied and verified; the card stays unchanged.")
        gaps=sum(c.gaps for c in self.clips)
        if gaps:
            self.status.setText(self.status.text()+f" Note: {gaps:,} frame numbers are already missing in the source sequences.")
        if self.clips:
            self.grid.blockSignals(True)
            self.grid.setCurrentRow(0)
            self.grid.blockSignals(False)
        self.filter_clips()
        self.start_history_check()
        self.update_selection()
        self.update_actions()
        QTimer.singleShot(0,self.queue_thumbnail)

    def placeholder(self,text='PREVIEW'):
        pixmap=QPixmap(176,99)
        pixmap.fill(ui_color("#f0eee5"))
        p=QPainter(pixmap)
        p.setPen(ui_color("#9a9686"))
        p.setFont(QFont("Inter",12))
        p.drawText(pixmap.rect(),Qt.AlignCenter,text)
        p.end()
        return QIcon(pixmap)

    def filter_clips(self):
        query=self.search.text().strip().casefold()
        for i in range(self.grid.count()):
            item=self.grid.item(i)
            clip=item.data(Qt.UserRole)
            item.setHidden(query not in (clip.name+" "+str(clip.folder)).casefold())
        QTimer.singleShot(0,self.queue_thumbnail)

    def queue_thumbnail(self):
        if self.thumb_pending or self.busy or self.scanning or self.quitting or (self.preview and (self.preview.playing or self.preview.buffering)):
            return
        generation=self.generation
        for i in range(self.grid.count()):
            item=self.grid.item(i)
            if item.data(Qt.UserRole+1):
                continue
            if not self.thumbnails_loading and (item.isHidden() or not self.grid.visualItemRect(item).intersects(self.grid.viewport().rect())):continue
            item.setData(Qt.UserRole+1,True)
            clip=item.data(Qt.UserRole)
            cached=self.thumbnail_cache.get(self.thumbnail_key(clip))
            if cached is not None:
                item.setIcon(QIcon(QPixmap.fromImage(cached)))
                item.setData(Qt.UserRole+2,True)
                continue
            self.thumb_pending=True
            if getattr(clip,'kind','raw')!='raw':
                job=VideoThumbnail(self)
                self.video_thumbnail=job
                job.done.connect(lambda result,row=i:self.thumbnail_ready(result,generation,row))
                job.start(clip.frames[0])
                return
            view,auto=self.preview_view,self.auto_exposure
            def work(c=clip):
                rgb=decode_frame(c.frames[0],"Fast",view=view,auto_exposure=auto)
                return QImage(rgb.data,rgb.shape[1],rgb.shape[0],rgb.strides[0],QImage.Format_RGB888).copy().scaled(176,99,Qt.KeepAspectRatio,Qt.SmoothTransformation)
            self.submit(self.thumb_pool,work,lambda result,row=i:self.thumbnail_ready(result,generation,row))
            return
        if self.thumbnails_loading and all(self.grid.item(i).data(Qt.UserRole+2) for i in range(self.grid.count())):
            self.thumbnails_loading=False
            self.empty.set_loading(False);self.empty.hide()
            self.grid.show();self.grid.animate_in()
            if self.clips:self.preview.set_clips(self.clips,0,autoplay=False)
            self.update_actions()

    def thumbnail_key(self,clip):
        return (self.inventory.device if self.inventory else None,str(clip.frames[0]).casefold(),self.thumbnail_stamps.get(str(clip.frames[0]).casefold()),self.auto_exposure,self.preview_view)

    def thumbnail_ready(self,result,generation,row):
        self.thumb_pending=False
        if generation==self.generation and row<self.grid.count():
            item=self.grid.item(row)
            if result[0]:
                item.setIcon(QIcon(QPixmap.fromImage(result[1])))
                if len(self.thumbnail_cache)>=512:self.thumbnail_cache.pop(next(iter(self.thumbnail_cache)))
                self.thumbnail_cache[self.thumbnail_key(item.data(Qt.UserRole))]=result[1]
            else:
                item.setIcon(self.placeholder('Preview unavailable'))
                item.setToolTip("Preview unavailable; transfer still includes this clip.\n"+result[1])
            item.setData(Qt.UserRole+2,True)
            if self.thumbnails_loading:
                done=sum(bool(self.grid.item(i).data(Qt.UserRole+2)) for i in range(self.grid.count()))
                self.empty.setText(f'Loading clip thumbnails…\n{done} / {self.grid.count()} ready')
        self.queue_thumbnail()

    def resizeEvent(self,event):
        super().resizeEvent(event)
        if hasattr(self,"grid"):
            QTimer.singleShot(0,self.queue_thumbnail)

    def preview_current(self,item,previous=None):
        if self.busy or not item:
            return
        clip=item.data(Qt.UserRole)
        if self.preview.clip is not clip:
            self.preview.set_clips(self.clips,self.grid.row(item),autoplay=False)

    def open_preview(self,item):
        if self.busy or not item:
            return
        self.preview_current(item)
        self.preview.start()

    def clip_clicked(self,item):
        if not QApplication.keyboardModifiers() & (Qt.ControlModifier|Qt.ShiftModifier):
            self.open_preview(item)

    def toggle_current_preview(self):
        if not self.busy and self.preview.clip:
            self.preview.toggle()

    def delete_selected(self):
        if self.busy or self.scanning or not self.inventory or not self.selected_keys:
            return
        if getattr(self,'deletion_dialog',None) is not None:
            self.deletion_dialog.raise_()
            self.deletion_dialog.activateWindow()
            return
        inventory=self.inventory
        clips=list(self.clips)
        keys=set(self.selected_keys)
        generation=self.generation
        count=len(self.selected_keys)
        warning=QMessageBox(self)
        warning.setIcon(QMessageBox.Warning)
        warning.setWindowTitle("Permanently delete selected clips?")
        warning.setText(f"Do you really want to permanently delete {count} selected clip(s)?")
        warning.setInformativeText(f"Source: {self.source}\n\nThis deletes their video frames/files and associated audio directly from the camera, SD card or source folder. It is irreversible and does not use the Recycle Bin or Trash. Make sure you have a verified backup. Other support files are kept.")
        warning.setDetailedText("\n".join(clip.name for clip in clips if clip.key in keys))
        confirm=warning.addButton("Permanently delete",QMessageBox.DestructiveRole)
        cancel=warning.addButton(QMessageBox.Cancel)
        warning.setDefaultButton(cancel)
        warning.setEscapeButton(cancel)
        self.deletion_dialog=warning
        try:
            warning.exec()
            confirmed=warning.clickedButton() is confirm
        finally:
            self.deletion_dialog=None
            warning.deleteLater()
        if not confirmed:
            return
        if self.busy or self.inventory is not inventory or self.generation!=generation:
            QMessageBox.warning(self,"Source changed","The source changed while the warning was open. Select your clips again; nothing was deleted.")
            return
        self.preview.clear_clip()
        self.history_cancel.set()
        self.scan_cancel.set()
        self.transfer_cancel=threading.Event()
        self.history_cancel.set()
        self.busy=True
        self.deleting=True
        self.last_result=None
        self.reset_transfer_progress()
        self.progress.show()
        self.cancel_button.show()
        self.update_actions()
        self.status.setText("Preparing deletion — checking selected files…")
        cancelled=self.transfer_cancel
        def work():
            progress=self.progress_signals.changed.emit
            plan=deletion_plan(inventory,clips,keys,cancelled,progress)
            return delete_files(plan,inventory.device,cancelled,progress)
        self.submit(self.transfer_pool,work,self.deletion_finished)

    def deletion_finished(self,result):
        self.busy=False
        self.deleting=False
        self.cancel_button.hide()
        if result[0]:
            deleted,error=result[1]
        else:
            deleted,error=0,str(result[1])
        if error:
            QMessageBox.warning(self,"Deletion stopped",f"Deleted {deleted} file(s). Deletion stopped: {error}\nRefresh the source to review what remains. Deleted files cannot be restored by FP Dock.")
        if self.disconnect_pending:
            self.disconnect_pending=False
            self.disconnect_source()
        elif self.source:
            self.refresh()
        self.show_header_notice(f"Deleted {deleted} file(s)" if not error else "Deletion stopped — check remaining clips")
        self.update_actions()
        if self.quitting:
            self.close()
            QApplication.instance().quit()

    def update_history_pause(self):
        if self.preview.playing or self.preview.buffering:self.history_paused.set()
        else:self.history_paused.clear()

    def start_history_check(self):
        self.history_cancel.set()
        self.history_cancel=threading.Event()
        self.history_revision+=1
        revision=self.history_revision
        generation=self.generation
        for i in range(self.grid.count()):
            self.grid.item(i).setData(Qt.UserRole+3,None)
        if self.busy or not self.inventory or not self.clips or not self.destination:
            self.history_label.hide()
            return
        self.history_label.setText("Checking existing Extras receipts…")
        self.history_label.show()
        inventory,clips,destination,cancel=self.inventory,list(self.clips),self.destination,self.history_cancel
        cache=self.history_cache
        def work():
            return find_transferred(inventory,clips,destination,ReadGate(cancel,self.history_paused),cache)
        def finished(result):
            if self.quitting or generation!=self.generation or revision!=self.history_revision or cancel.is_set():return
            if not result[0]:
                self.history_label.setText("Transfer history unavailable — no clips confirmed")
                return
            found=result[1]
            for i in range(self.grid.count()):
                item=self.grid.item(i);clip=item.data(Qt.UserRole)
                session=found.get(clip.key)
                item.setData(Qt.UserRole+3,session)
                item.setToolTip(recorded_label(clip)+'\n'+str(clip.folder)+(('\nAlready transferred · SHA-256 confirmed\n'+session) if session else ''))
            self.history_label.setText(f"{len(found)} already transferred · {len(clips)-len(found)} not confirmed at this destination")
            self.grid.viewport().update()
        self.submit(self.history_pool,work,finished)

    def check_highlighted(self,checked):
        self.grid.set_checks(self.grid.selectedItems(),checked)

    def check_all(self,checked):
        self.grid.blockSignals(True)
        for i in range(self.grid.count()):
            self.grid.item(i).setCheckState(Qt.Checked if checked else Qt.Unchecked)
        self.grid.blockSignals(False)
        self.update_selection()

    def update_selection(self):
        if self.busy or not self.inventory:
            return
        keys={self.grid.item(i).data(Qt.UserRole).key for i in range(self.grid.count())
              if self.grid.item(i).checkState()==Qt.Checked}
        changed=keys!=self.selected_keys
        if not changed and self.selected_inventory is not None:
            return
        self.selected_keys=keys
        if changed:
            self.last_result=None
            self.reset_transfer_progress()
        self.selected_inventory=select_inventory(self.inventory,self.clips,keys) if keys else None
        if self.selected_inventory:
            inv=self.selected_inventory
            self.hint.setText(f"{len(keys)} / {len(self.clips)} clips selected · {selected_duration_text(self.clips,keys)} total · {size_text(inv.total_bytes)}")
            if changed:
                self.status.setText('')
                gaps=sum(c.gaps for c in self.clips if c.key in keys)
                if gaps:
                    self.status.setToolTip(f"{gaps:,} frame numbers are already missing on the source.")
        else:
            self.hint.setText("0 / "+str(len(self.clips))+" clips selected · 0:00 total")
            if changed:
                self.status.setText("Choose the clip checkboxes to select what gets transferred.")
        self.summary.setText(date_range(self.clips)+' · '+self.hint.text())
        self.summary.setToolTip(self.summary.text())
        self.update_folder_preview()
        self.update_actions()

    def export_name_changed(self):
        self.settings['export_name']=self.export_name.text()
        self.save_settings()
        self.last_result=None
        self.reset_transfer_progress()
        self.update_folder_preview()
        self.update_actions()

    def update_folder_preview(self):
        chosen=[c for c in self.clips if c.key in self.selected_keys]
        name=session_name(chosen,self.export_name.text())
        fallback=any(getattr(c,'date_fallback',False) for c in chosen)
        self.folder_preview.setText(name or 'Select clips to see the folder name')
        self.folder_preview.setToolTip('Recording date from camera metadata.' if not fallback else 'Some clips lack recording dates in metadata; those clips use their file modification dates in Europe/Berlin.')
        if fallback and name:self.folder_preview.setText(name+'\nSome dates use file timestamps')

    def apply_visuals(self,view,auto):
        self.visuals_changed(view)

    def playback_scale_changed(self,reduction):
        self.settings['playback_scale']=reduction
        self.save_settings()

    def visuals_changed(self,view=None):
        view,auto=default_output_view(),True
        if (view,auto)==(self.preview_view,self.auto_exposure):
            return
        self.preview_view,self.auto_exposure=view,auto
        self.settings.update(auto_exposure=auto)
        self.save_settings()
        self.generation+=1
        self.grid.blockSignals(True)
        for i in range(self.grid.count()):
            item=self.grid.item(i)
            item.setData(Qt.UserRole+1,False)
            item.setIcon(self.placeholder())
        self.grid.blockSignals(False)
        if self.preview:
            self.preview.set_visual_settings(view,auto)
        self.queue_thumbnail()

    def choose_destination(self):
        path=QFileDialog.getExistingDirectory(self,"Choose a destination on another drive",self.destination)
        if path:
            self.destination=path
            self.settings["destination"]=path
            self.destination_label.setText(path)
            self.destination_label.setToolTip(path)
            self.save_settings()
            self.start_history_check()
            self.last_result=None
            self.reset_transfer_progress()
            self.update_actions()

    def reset_transfer_progress(self):
        self.progress.setValue(0)
        self.progress_value.setText('0%')

    def start_transfer(self):
        if self.last_result:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.last_result["session"]))
            return
        if self.busy or not self.inventory or not self.destination:
            return
        if self.preview:
            self.preview.pause()
        self.history_cancel.set()
        self.busy=True
        self.transfer_cancel=threading.Event()
        self.progress.setValue(0)
        self.progress_value.setText('0%')
        self.progress.show()
        self.cancel_button.show()
        self.status.setObjectName("muted")
        self.status.setText("Checking source and destination…")
        self.update_actions()
        source=self.source
        expected_device=self.inventory.device
        selected_keys=set(self.selected_keys)
        clips=list(self.clips)
        folder_name=session_name([c for c in clips if c.key in selected_keys],self.export_name.text())
        destination=self.destination
        cancel=self.transfer_cancel
        self.last_progress=0
        def progress(data):
            now=time.monotonic()
            if now-self.last_progress>.08 or data["phase"] in ("Verified","Checking that every source file is included"):
                self.last_progress=now
                self.progress_signals.changed.emit(data)
        def work():
            # A fresh inventory includes files recorded since the grid was populated.
            inventory=inventory_folder(source,cancel)
            if inventory.device!=expected_device:
                raise OSError("The source card was replaced. Refresh the source before transferring.")
            inventory=select_inventory(inventory,clips,selected_keys)
            return transfer_all(inventory,destination,cancel,progress,folder_name=folder_name)
        self.submit(self.transfer_pool,work,self.transfer_finished)

    def transfer_progress(self,data):
        self.progress.setValue(min(999,int(data["processed"]*1000/data["total"])))
        self.progress_value.setText(f'{self.progress.value()/10:.0f}%')
        self.status.setText(f"{data['phase']} · {self.progress.value()/10:.0f}%")
        self.status.setToolTip(data['file'])
        QTimer.singleShot(0,self.preview.fit_video)

    def transfer_finished(self,result):
        self.busy=False
        self.cancel_button.hide()
        if result[0]:
            self.last_result=result[1]
            self.progress.setValue(1000)
            self.progress_value.setText('100%')
            self.status.setObjectName("success")
            self.status.setText('Transfer verified · '+size_text(self.last_result['bytes']))
            self.status.setToolTip(f"{self.last_result['verified_files']:,} files verified with SHA-256. Originals unchanged.")
            if not self.isVisible():
                self.tray.showMessage("Transfer verified",f"All {self.last_result['verified_files']:,} files copied and verified. Originals remain on the card.")
        else:
            self.status.setObjectName("error")
            self.status.setText(result[1]+"  Reconnect if needed, then choose Transfer all to resume. Original files were not deleted.")
            if not self.isVisible():
                self.tray.showMessage("Transfer needs attention",result[1],QSystemTrayIcon.Warning)
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        if self.disconnect_pending:
            self.disconnect_pending=False
            self.disconnect_source()
        self.update_actions()
        self.queue_thumbnail()
        self.start_history_check()
        if self.quitting:
            self.close()
            QApplication.instance().quit()

    def cancel_transfer(self):
        self.transfer_cancel.set()
        self.cancel_button.setEnabled(False)
        self.status.setText("Stopping deletion safely. Already deleted files cannot be restored." if getattr(self,"deleting",False) else "Stopping at the next file-read boundary. Verified copies are kept for resume.")

    def update_actions(self):
        self.update_header_connection()
        self.status.setVisible(bool(self.status.text()))
        self.update_folder_preview()
        source_connected=not self.source_key or self.source_key in {s["key"] for s in self.detected}
        ready=bool(self.selected_inventory and self.selected_inventory.files and self.destination and source_connected)
        self.transfer_button.setEnabled(bool(self.last_result) or (ready and not self.busy))
        amount=size_text(self.selected_inventory.total_bytes) if self.selected_inventory else '0 B'
        self.transfer_button.setText("Open verified copy ↗" if self.last_result else ("Deleting…" if self.busy and getattr(self,"deleting",False) else "Transferring…" if self.busy else f"Transfer {len(self.selected_keys)} clips · {selected_duration_text(self.clips,self.selected_keys)} · {amount}"))
        self.delete_button.setEnabled(bool(self.selected_keys) and source_connected and not self.busy and not self.scanning and not self.thumbnails_loading)
        self.destination_button.setEnabled(not self.busy)
        self.export_name.setEnabled(not self.busy)
        self.choose_source_button.setEnabled(not self.busy)
        self.choose_source_button.setVisible(self.source is None)
        self.refresh_button.setEnabled(bool(self.source) and not self.busy and not self.scanning)
        self.sources.setEnabled(not self.busy)
        self.grid.setEnabled(not self.busy)
        self.visual_row.setEnabled(not self.busy and not self.scanning)
        self.preview.set_transfer_busy(self.busy)
        self.cancel_button.setEnabled(self.busy)

    def refresh(self):
        if self.source and not self.busy:
            self.load_source(self.source,self.source_key)

    def watch_changed(self,enabled):
        try:
            set_startup(enabled)
        except (OSError,subprocess.SubprocessError) as exc:
            self.watch.blockSignals(True)
            self.watch.setChecked(not enabled)
            self.watch.blockSignals(False)
            self.status.setText("Could not change automatic watching: "+str(exc))
            self.show_header_notice('Watcher could not be changed')
            return
        self.settings["watch"]=enabled
        self.save_settings()
        self.tray.setVisible(enabled)
        self.show_header_notice('Watcher enabled' if enabled else 'Watcher disabled')

    def theme_changed(self,dark):
        self.settings['dark_mode']=bool(dark)
        self.save_settings()
        def refresh_status():
            connected=bool(self.detected)
            self.connection_status.setStyleSheet('color: '+ui_color('#447259' if connected else '#8c8879').name()+';')
        set_theme(dark,STYLE+EXTRA_STYLE,callback=refresh_status)

    def show_header_notice(self,message):
        self.header_notice.setText(message)
        self.header_notice.show();self.notice_timer.start(4500)

    def update_header_connection(self):
        if not hasattr(self,'connection_status'):return
        connected=bool(self.detected)
        self.connection_status.setText('● Camera / SD connected' if connected else ('Local folder open' if self.source and self.manual_source else 'FP / SD card disconnected'))
        animate_label_color(self.connection_status,'#447259' if connected else '#8c8879')

    def reveal_window(self,attention=False):
        self.showNormal()
        self.raise_()
        self.activateWindow()
        if attention and os.name=='nt' and QApplication.platformName()!='offscreen':
            activated=request_foreground(int(self.winId()))
            QApplication.alert(self,8000)
            logging.info('Arrival window shown; foreground=%s visible=%s',activated,self.isVisible())

    def toggle_fullscreen(self):
        self.showNormal() if self.isMaximized() else self.showMaximized()

    def quit_app(self):
        if self.busy:
            answer=QMessageBox.question(self,"Transfer running","Cancel this transfer and quit? Verified copies will be kept for resume.",QMessageBox.Yes|QMessageBox.No,QMessageBox.No)
            if answer!=QMessageBox.Yes:
                return
            self.quitting=True
            self.cancel_transfer()
        else:
            self.quitting=True
            self.close()
            QApplication.instance().quit()

    def dragEnterEvent(self,event):
        if not self.busy and event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self,event):
        if not self.busy:
            for url in event.mimeData().urls():
                if url.isLocalFile() and Path(url.toLocalFile()).is_dir():
                    self.load_source(url.toLocalFile())
                    break

    def closeEvent(self,event):
        if not self.quitting and (self.watch.isChecked() or self.busy) and QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()
            self.preview.pause()
            self.hide()
            event.ignore()
            return
        if self.busy:
            event.ignore()
            self.status.setText("A transfer is running. Use Cancel before closing this window.")
            return
        self.quitting=True
        self.grid.motion_timer.stop()
        self.grid.scroll_animation.stop()
        self.empty.timer.stop()
        self.detect_timer.stop()
        self.device_debounce.stop()
        if self.device_filter is not None:
            QApplication.instance().removeNativeEventFilter(self.device_filter)
        self.history_cancel.set()
        self.scan_cancel.set()
        self.generation+=1
        if self.preview:
            self.preview.shutdown()
        for pool in (self.scan_pool,self.thumb_pool,self.detect_pool,self.transfer_pool,self.history_pool):
            pool.clear()
            pool.waitForDone()
        self.tray.hide()
        super().closeEvent(event)
        QTimer.singleShot(0,QApplication.instance().quit)


def main():
    parser=argparse.ArgumentParser(description=APP_TITLE)
    parser.add_argument("source",nargs="?",help="Optional CINEMA folder to open")
    parser.add_argument("--watch",action="store_true",help="Wait in the tray for a camera / SD card")
    parser.add_argument("--state-dir",help=argparse.SUPPRESS)
    parser.add_argument("--smoke-test",help=argparse.SUPPRESS)
    parser.add_argument("--smoke-player",help=argparse.SUPPRESS)
    parser.add_argument("--headless-check",action="store_true",help=argparse.SUPPRESS)
    parser.add_argument("--self-test-transfer",help=argparse.SUPPRESS)
    parser.add_argument("--self-test-watcher",help=argparse.SUPPRESS)
    args=parser.parse_args()
    if args.self_test_transfer:
        from selfcheck import run
        return run(args.self_test_transfer)
    if args.self_test_watcher:
        from watchcheck import run
        return run(args.self_test_watcher,Ingest)
    app=QApplication(sys.argv)
    app.setOrganizationName("VisualFieldNotes")
    app.setApplicationName("Ingest")
    app.setApplicationDisplayName(APP_TITLE)
    app.setStyle("Fusion")
    load_fonts()
    app.setStyleSheet(STYLE+EXTRA_STYLE)
    app.setWindowIcon(dock_icon())
    state=Path(args.state_dir or QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation))
    state.mkdir(parents=True,exist_ok=True)
    lock=QLockFile(str(state/"instance.lock"))
    lock.setStaleLockTime(0)
    server_name="FieldNotesIngest-"+hashlib.sha256(str(state.resolve()).casefold().encode()).hexdigest()[:24]
    if not lock.tryLock(100):
        if not args.watch:
            socket=QLocalSocket()
            socket.connectToServer(server_name)
            if socket.waitForConnected(1000):
                socket.write(json.dumps({"source":args.source}).encode())
                socket.waitForBytesWritten(1000)
        return 0
    logging.basicConfig(filename=state/"ingest.log",level=logging.INFO,format="%(asctime)s %(message)s")
    logging.info('Watcher starting; tray_launch=%s',args.watch)
    window=Ingest(state,args.watch,detect=not bool(args.smoke_test))
    if args.headless_check:
        window.setAttribute(Qt.WA_DontShowOnScreen,True)
    if not args.state_dir and not args.smoke_test and not window.settings.get("watch_v3_initialized"):
        try:
            set_startup(True)
            window.settings.update(watch=True,watch_v3_initialized=True)
            window.watch.blockSignals(True)
            window.watch.setChecked(True)
            window.watch.blockSignals(False)
            window.tray.show()
            window.save_settings()
        except (OSError,subprocess.SubprocessError) as exc:
            window.status.setText("Could not enable background watching: "+str(exc))
    server=QLocalServer(window)
    server.setSocketOptions(QLocalServer.UserAccessOption)
    QLocalServer.removeServer(server_name)
    server.listen(server_name)
    connections=[]
    def incoming():
        socket=server.nextPendingConnection()
        connections.append(socket)
        def read():
            try:
                payload=json.loads(bytes(socket.readAll()).decode())
                if payload.get("source") and not window.busy:
                    window.load_source(payload["source"])
            except (ValueError,TypeError,OSError):
                pass
            window.reveal_window()
            socket.disconnectFromServer()
        socket.readyRead.connect(read)
        if socket.bytesAvailable():
            read()
    server.newConnection.connect(incoming)
    app.setQuitOnLastWindowClosed(False)
    window.destroyed.connect(app.quit)
    if not args.watch or args.smoke_test:
        window.show()
    elif not window.watch.isChecked():
        return 0
    if args.source:
        QTimer.singleShot(0,lambda:window.load_source(args.source))
    if args.smoke_test:
        started=time.monotonic()
        probe=QTimer(window)
        stage="grid"
        def check():
            nonlocal stage
            if time.monotonic()-started>45:
                probe.stop()
                window.quitting=True
                window.close()
                app.exit(2)
                return
            if window.inventory and window.grid.count() and not window.thumb_pending and all(window.grid.item(i).data(Qt.UserRole+1) for i in range(min(10,window.grid.count()))):
                if args.smoke_player:
                    if stage=="grid":
                        window.open_preview(window.grid.item(0))
                        stage="playing"
                        return
                    player=window.preview
                    if stage=="playing":
                        if not player or player.rendered_frames<80 or player.measured_fps<=0:
                            return
                        player.pause()
                        player.seek(12)
                        stage="inspect"
                        return
                    if not player or player.shown!=12:
                        return
                    player.grab().save(args.smoke_player)
                probe.stop()
                path=Path(args.smoke_test)
                window.grab().save(str(path))
                path.with_suffix(".json").write_text(json.dumps({"frozen":bool(getattr(sys,"frozen",False)),
                    "clips":len(window.clips),"files":len(window.inventory.files),"grid_wraps":window.grid.isWrapping(),
                    "watch_enabled":window.watch.isChecked(),
                    "live_frames_seen":window.preview.rendered_frames,
                    "gpu_active":window.preview.gpu_active,"backend":window.preview.backend,
                    "measured_fps":window.preview.measured_fps,"preview_embedded":not window.preview.isWindow()}),encoding="utf-8")
                window.quitting=True
                window.close()
                app.exit(0)
            elif time.monotonic()-started>35:
                probe.stop()
                window.quitting=True
                window.close()
                app.exit(2)
        probe.timeout.connect(check)
        probe.start(100)
    result=app.exec()
    lock.unlock()
    return result


if __name__=="__main__":
    sys.exit(main())
