"""Framehouse — a portable CinemaDNG sequence browser for Windows."""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import os
import shutil
import sys
import threading
import time
from collections import OrderedDict
from pathlib import Path

os.environ.setdefault("OMP_NUM_THREADS", "1")
from theme import color as ui_color, role as theme_role

from PySide6.QtCore import (Qt, QSize, QTimer, QObject, Signal, QRunnable, QThreadPool, QVariantAnimation, QEasingCurve, QEvent,
                            QUrl, QStandardPaths, QPointF, QRectF)
from PySide6.QtGui import (QImage, QPixmap, QPainter, QColor, QFont, QIcon, QShortcut,
                           QKeySequence, QDesktopServices, QAction, QPen, QPolygonF, QFontDatabase, QPainterPath)
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QLabel, QPushButton, QCheckBox,
    QVBoxLayout, QHBoxLayout, QSplitter, QListWidget, QListWidgetItem, QTreeWidget,
    QTreeWidgetItem, QAbstractItemView, QLineEdit, QComboBox, QSlider, QFileDialog,
    QMessageBox, QMenu, QStyle, QFrame, QDoubleSpinBox, QSizePolicy, QStyledItemDelegate, QGraphicsDropShadowEffect, QAbstractSpinBox)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtSvg import QSvgRenderer
from raw_library import Clip, scan_folder, load_metadata, decode_frame

APP = "Framehouse"
PANEL_PADDING=24
HEADER_HEIGHT=34
CONTENT_GAP=7
ASSET_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "assets"
STYLE = """
* { font-family: 'Inter', 'Segoe UI'; font-size: 12px; color: #1c1b17; }
QMainWindow, QWidget { background: #fbfaf6; }
QLabel { background: transparent; }
QWidget#header { background: #fbfaf6; border-bottom: 1px solid #e5e2d6; }
QLabel#brand { color: #1c1b17; font-size: 16px; font-weight: 600; letter-spacing: -0.3px; }
QLabel#eyebrow { color: #9a9686; font-size: 9px; font-weight: 600; letter-spacing: 1.4px; }
QLabel#title { font-size: 20px; font-weight: 600; color: #1c1b17; letter-spacing: -0.5px; }
QLabel#muted { color: #8c8879; font-size: 11px; }
QLabel#badge { color: #fbfaf6; background: #1c1b17; border-radius: 12px; padding: 5px 10px; font-size: 9px; font-weight: 600; }
QPushButton { background: #fbfaf6; border: 1px solid #e5e2d6; border-radius: 16px; padding: 8px 14px; font-size: 11px; font-weight: 600; }
QPushButton:hover { background: #f0eee5; border-color: #c7c3b6; }
QPushButton:pressed, QPushButton:checked { background: #1c1b17; border-color: #1c1b17; color: #fbfaf6; }
QPushButton:disabled { color: #c0bcae; border-color: #ece9df; background: #f8f7f1; }
QPushButton#primary { background: #1c1b17; color: #fbfaf6; border: 1px solid #1c1b17; border-radius: 18px; font-weight: 600; padding: 10px 18px; }
QPushButton#primary:hover { background: #454237; }
QPushButton#play { background: #1c1b17; border-radius: 17px; color: #fbfaf6; min-width: 62px; border: 0; }
QLineEdit, QComboBox, QDoubleSpinBox { background: #fbfaf6; border: 1px solid #e5e2d6; border-radius: 14px; padding: 7px 10px; font-size: 11px; selection-background-color: #e9e4d3; selection-color: #1c1b17; }
QDoubleSpinBox QLineEdit { border: none; border-radius: 0; padding: 0; background: transparent; }
QDoubleSpinBox::up-button { subcontrol-origin: border; subcontrol-position: top right; width: 15px; border: none; background: transparent; }
QDoubleSpinBox::down-button { subcontrol-origin: border; subcontrol-position: bottom right; width: 15px; border: none; background: transparent; }
QLineEdit:focus, QComboBox:focus, QDoubleSpinBox:focus { border-color: #4c74a8; }
QComboBox::drop-down { border: 0; width: 17px; }
QFrame#qualitySegments { background: #fbfaf6; border: 1px solid #e5e2d6; border-radius: 17px; }
QPushButton#segment { border: none; padding: 3px 9px; font-size: 11px; border-radius: 14px; }
QComboBox QAbstractItemView { background: #fbfaf6; selection-background-color: #e9e4d3; selection-color: #1c1b17; }
QTreeWidget, QListWidget { background: #fbfaf6; border: none; outline: none; }
QTreeWidget::item { padding: 9px 6px; border-radius: 17px; font-size: 11px; }
QTreeWidget::item:selected { background: #1c1b17; color: #fbfaf6; }
QTreeWidget::branch:selected { background: #fbfaf6; }
QTreeWidget::item:hover:!selected { background: #f0eee5; }
QListWidget::item { background: transparent; border: none; }
QSlider::groove:horizontal { height: 3px; background: #e5e2d6; border-radius: 1px; }
QSlider::sub-page:horizontal { background: #1c1b17; border-radius: 1px; }
QSlider::handle:horizontal { background: #1c1b17; width: 10px; height: 10px; margin: -4px 0; border-radius: 5px; }
QSplitter::handle { background: #e5e2d6; width: 1px; height: 1px; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 2px; }
QScrollBar:horizontal { background: transparent; height: 8px; margin: 2px; }
QScrollBar::handle { background: #d6d1c3; border-radius: 3px; min-height: 22px; min-width: 22px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: none; }
QToolTip { background: #1c1b17; color: #fbfaf6; border: 0; padding: 7px; }
QMenu { background: #fbfaf6; border: 1px solid #e5e2d6; padding: 5px; }
QMenu::item { padding: 7px 20px; }
QMenu::item:selected { background: #f0eee5; }
QStatusBar { background: #fbfaf6; border-top: 1px solid #e5e2d6; color: #9a9686; font-size: 10px; }
"""


def load_fonts():
    for path in ASSET_DIR.glob("Inter-*.ttf"):
        QFontDatabase.addApplicationFont(str(path))


def brand_icon(size=256):
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    p = QPainter(pixmap)
    renderer = QSvgRenderer(str(ASSET_DIR / "brand-mark.svg"))
    renderer.render(p, QRectF(0, size*.11, size, size*.78))
    p.end()
    return QIcon(pixmap)


class ClipCardDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        return QSize(208,165)

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(option.rect).adjusted(10, 9, -10, -12)
        painter.translate(rect.center())
        painter.rotate((-1.8, 1.3, -1.0, 1.8)[index.row() % 4])
        card = QRectF(-rect.width()/2, -rect.height()/2, rect.width(), rect.height())
        painter.setPen(Qt.NoPen)
        for offset in range(5, 0, -1):
            painter.setBrush(ui_color(28,27,23,5))
            painter.drawRoundedRect(card.adjusted(-offset, 2, offset, offset+3), 7, 7)
        selected = bool(option.state & QStyle.State_Selected)
        painter.setBrush(ui_color("#ffffff"))
        painter.setPen(QPen(ui_color("#1c1b17" if selected else "#e5e2d6"), 1.5 if selected else 1))
        painter.drawRoundedRect(card, 6, 6)
        photo = card.adjusted(7,7,-7,-41)
        icon = index.data(Qt.DecorationRole)
        if icon:
            pixmap = icon.pixmap(176,99)
            path = QPainterPath()
            path.addRoundedRect(photo, 3, 3)
            painter.save()
            painter.setClipPath(path)
            painter.setRenderHint(QPainter.SmoothPixmapTransform)
            painter.fillRect(photo,ui_color('#f0eee5'))
            fitted=pixmap.size().scaled(photo.size().toSize(),Qt.KeepAspectRatio)
            target=QRectF(photo.center().x()-fitted.width()/2,photo.center().y()-fitted.height()/2,fitted.width(),fitted.height())
            painter.drawPixmap(target,pixmap,QRectF(pixmap.rect()))
            painter.restore()
        text = str(index.data(Qt.DisplayRole)).split("\n")
        painter.setFont(QFont("Inter", 10, QFont.DemiBold))
        painter.setPen(ui_color("#1c1b17"))
        name = painter.fontMetrics().elidedText(text[0], Qt.ElideMiddle, int(card.width()-18))
        painter.drawText(card.adjusted(9,card.height()-36,-9,-18), Qt.AlignLeft | Qt.AlignVCenter, name)
        painter.setFont(QFont("Inter", 9))
        painter.setPen(ui_color("#686457"))
        painter.drawText(card.adjusted(9,card.height()-19,-9,-5), Qt.AlignLeft | Qt.AlignVCenter, text[1] if len(text)>1 else "")
        painter.restore()


class ElidedLabel(QLabel):
    """Keep the full accessible text while fitting long paths / clip names."""
    def paintEvent(self,event):
        p=QPainter(self)
        p.setFont(self.font())
        p.setPen(self.palette().color(self.foregroundRole()))
        text=self.fontMetrics().elidedText(self.text(),Qt.ElideMiddle,self.width())
        p.drawText(self.rect(),self.alignment(),text)


class WrappingPathLabel(QLabel):
    """Show complete paths, including unbroken folder names, at any panel width."""
    def hasHeightForWidth(self):return True
    def heightForWidth(self,width):
        return max(self.fontMetrics().height(),self.fontMetrics().boundingRect(QRectF(0,0,max(1,width),10000).toRect(),Qt.TextWrapAnywhere|Qt.AlignLeft,self.text()).height())
    def sizeHint(self):return QSize(240,self.fontMetrics().height())
    def minimumSizeHint(self):return QSize(0,self.fontMetrics().height())
    def paintEvent(self,event):
        p=QPainter(self);p.setFont(self.font());p.setPen(self.palette().color(self.foregroundRole()))
        p.drawText(self.rect(),Qt.TextWrapAnywhere|Qt.AlignLeft|Qt.AlignVCenter,self.text())


class Signals(QObject):
    done = Signal(object)
    progress = Signal(int, int)


class Job(QRunnable):
    def __init__(self, fn, callback):
        super().__init__()
        self.fn = fn
        self.signals = Signals()
        self.signals.done.connect(callback)

    def run(self):
        try:
            result = (True, self.fn())
        except Exception as exc:
            logging.exception("Background operation failed")
            result = (False, str(exc))
        self.signals.done.emit(result)


class Canvas(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumSize(300, 180)
        self.image = QImage()
        self.zoom = "Fit"
        self.message = "Your footage, frame by frame."
        self.detail = "Pin a folder to start browsing CinemaDNG sequences."
        self.setAutoFillBackground(False)

    def paintEvent(self, event):
        p = QPainter(self)
        clip_path = QPainterPath()
        clip_path.addRoundedRect(QRectF(self.rect()), 6, 6)
        p.setClipPath(clip_path)
        p.fillRect(self.rect(), ui_color("#f0eee5"))
        if not self.image.isNull():
            size = self.image.size()
            if self.zoom == "Fit":
                size.scale(self.size(), Qt.KeepAspectRatio)
            else:
                factor = float(self.zoom.rstrip("%")) / 100
                size = QSize(int(size.width()*factor), int(size.height()*factor))
            from PySide6.QtCore import QRect
            target = QRect((self.width()-size.width())//2, (self.height()-size.height())//2, size.width(), size.height())
            p.setRenderHint(QPainter.SmoothPixmapTransform)
            p.drawImage(target, self.image)
        else:
            from PySide6.QtCore import QRect
            if getattr(self,'show_brand',True):
                p.drawPixmap(self.width()//2-28, self.height()//2-85, brand_icon().pixmap(56,56))
            p.setPen(ui_color("#1c1b17"))
            p.setFont(QFont("Inter", 17, QFont.DemiBold))
            p.drawText(QRect(12, self.height()//2-13, self.width()-24, 40), Qt.AlignCenter, self.message)
            p.setPen(ui_color("#9a9686"))
            p.setFont(QFont("Inter", 10))
            p.drawText(QRect(18, self.height()//2+34, self.width()-36, 60), Qt.AlignHCenter | Qt.TextWordWrap, self.detail)
        p.end()

    def mouseDoubleClickEvent(self, event):
        self.window().toggle_fullscreen()


def label(text, kind=None):
    w = QLabel(text)
    if kind:
        w.setObjectName(kind)
    return w


def transport_icon(kind, color="#1c1b17"):
    """Vector controls avoid font-dependent transport glyphs."""
    pixmap = QPixmap(24, 24)
    pixmap.fill(Qt.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.Antialiasing)
    ink = QColor(color)
    p.setPen(QPen(ink, 1.7, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.setBrush(ink)
    def triangle(points):
        p.drawPolygon(QPolygonF([QPointF(*v) for v in points]))
    if kind == "play":
        triangle([(8,5),(19,12),(8,19)])
    elif kind == "pause":
        p.drawRoundedRect(QRectF(7,5,3,14),.5,.5)
        p.drawRoundedRect(QRectF(14,5,3,14),.5,.5)
    elif kind in ("previous", "first"):
        triangle([(17,6),(8,12),(17,18)])
        p.drawLine(5,6,5,18)
        if kind == "first":
            p.drawLine(2,6,2,18)
    elif kind in ("next", "last"):
        triangle([(7,6),(16,12),(7,18)])
        p.drawLine(19,6,19,18)
        if kind == "last":
            p.drawLine(22,6,22,18)
    elif kind == "loop":
        p.setBrush(Qt.NoBrush)
        p.drawArc(QRectF(4,4,16,16),35*16,285*16)
        p.setBrush(ink)
        triangle([(19,4),(20,11),(14,7)])
    elif kind == "fullscreen":
        for a,b,c in [((4,9),(4,4),(9,4)), ((15,4),(20,4),(20,9)),
                      ((20,15),(20,20),(15,20)), ((9,20),(4,20),(4,15))]:
            p.drawPolyline(QPolygonF([QPointF(*a),QPointF(*b),QPointF(*c)]))
    elif kind == "brand":
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(3,3,18,18),2,2)
        p.drawLine(8,7,8,17)
        p.drawLine(16,7,16,17)
        p.drawLine(8,7,16,7)
        p.drawLine(8,17,16,17)
    elif kind == "folder":
        p.setBrush(Qt.NoBrush)
        p.drawPolygon(QPolygonF([QPointF(3,6),QPointF(10,6),QPointF(12,9),
                                QPointF(21,9),QPointF(21,19),QPointF(3,19)]))
    p.end()
    return QIcon(pixmap)




class SmoothCheckBox(QCheckBox):
    def __init__(self,text):
        super().__init__(text)
        self.setCursor(Qt.PointingHandCursor)
        self._fill=ui_color('#fbfaf6')
        self._fade=QVariantAnimation(self);self._fade.setDuration(150)
        self._fade.setEasingCurve(QEasingCurve.InOutCubic)
        self._fade.valueChanged.connect(self._color)
        self.toggled.connect(self._toggle_color)
    def _color(self,color):
        self._fill=color
        self.setStyleSheet('QCheckBox::indicator, QCheckBox::indicator:checked { background: '+color.name()+'; }')
    def _toggle_color(self,checked):
        self._fade.stop();self._fade.setStartValue(self._fill)
        self._fade.setEndValue(ui_color('#1c1b17' if checked else '#fbfaf6'));self._fade.start()


def animate_label_color(widget,color):
    if getattr(widget,'_target_color',None)==color:return
    widget._target_color=color
    if not hasattr(widget,'_color_fade'):
        widget._current_color=ui_color('#8c8879')
        widget._color_fade=QVariantAnimation(widget);widget._color_fade.setDuration(150)
        def changed(value):
            widget._current_color=value;widget.setStyleSheet('color: '+value.name()+';')
        widget._color_fade.valueChanged.connect(changed)
    widget._color_fade.stop();widget._color_fade.setStartValue(widget._current_color)
    widget._color_fade.setEndValue(ui_color(color));widget._color_fade.start()


class SmoothButton(QPushButton):
    """Paint the shared button style with interruptible color transitions."""
    def __init__(self,text):
        super().__init__(text)
        self._hover=False;self._press=False
        self._colors=('#fbfaf6','#1c1b17','#e5e2d6')
        self._colors=tuple(ui_color(c) for c in self._colors)
        self._start=self._colors;self._end=self._colors
        self._animation=QVariantAnimation(self)
        self._animation.setStartValue(0.);self._animation.setEndValue(1.)
        self._animation.setDuration(150)
        self._animation.setEasingCurve(QEasingCurve.InOutCubic)
        self._animation.valueChanged.connect(self._blend)
        self.pressed.connect(self._pressed)
        self.released.connect(lambda:QTimer.singleShot(80,self._released))
        self.toggled.connect(lambda _:self._refresh())
        self.setCursor(Qt.PointingHandCursor)
    def setObjectName(self,name):
        super().setObjectName(name)
        if hasattr(self,'_animation'):self._refresh(True)
    def _pressed(self):self._press=True;self._refresh()
    def _released(self):self._press=self.isDown();self._refresh()
    def _blend(self,t):
        self._colors=tuple(QColor.fromRgbF(*[a.getRgbF()[i]*(1-t)+b.getRgbF()[i]*t for i in range(4)]) for a,b in zip(self._start,self._end))
        self.update()
    def _refresh(self,immediate=False):
        name=self.objectName();primary=name in ('primary','play')
        if not self.isEnabled():
            colors=('#e5e2d6','#9a9686','#e5e2d6') if name=='primary' else ('#f8f7f1','#c0bcae','#ece9df')
        elif self._press or self.isChecked():colors=('#1c1b17','#fbfaf6','#1c1b17')
        elif primary:colors=('#454237' if self._hover else '#1c1b17','#fbfaf6','#1c1b17')
        else:colors=('#f0eee5','#1c1b17','#c7c3b6') if self._hover else ('#fbfaf6','#1c1b17','#e5e2d6')
        if name=='segment' and not self._press and not self.isChecked():colors=(colors[0],colors[1],'transparent')
        if self.hasFocus() and self.isEnabled():colors=(colors[0],colors[1],'#4c74a8')
        self.setCursor(Qt.PointingHandCursor if self.isEnabled() else Qt.ArrowCursor)
        self._animation.stop();self._start=self._colors;self._end=tuple(ui_color(c) for c in colors)
        if self.isEnabled() and (primary or self._press or self.isChecked()):
            bg=theme_role(colors[0],'#e5e2d6' if self._hover and not self.isChecked() else '#fbfaf6')
            self._end=(bg,theme_role('#fbfaf6','#1c1b17'),self._end[2])
        if immediate:self._colors=self._end;self.update()
        else:self._animation.start()
    def enterEvent(self,event):self._hover=True;self._refresh();super().enterEvent(event)
    def leaveEvent(self,event):self._hover=False;self._refresh();super().leaveEvent(event)
    def changeEvent(self,event):
        super().changeEvent(event)
        if event.type()==QEvent.EnabledChange and hasattr(self,'_animation'):self._refresh()
    def focusInEvent(self,event):super().focusInEvent(event);self._refresh()
    def focusOutEvent(self,event):super().focusOutEvent(event);self._refresh()
    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.Antialiasing)
        background,foreground,border=self._colors
        radius=14 if self.objectName()=='segment' else 18 if self.objectName()=='primary' else 17 if self.objectName()=='play' else 16
        p.setBrush(background);p.setPen(QPen(border,1))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(.5,.5,-.5,-.5),radius,radius)
        p.setFont(self.font());p.setPen(foreground)
        text=self.text().replace('&','');icon=self.icon()
        iw=self.iconSize().width() if not icon.isNull() else 0
        gap=6 if iw and text else 0
        tw=self.fontMetrics().horizontalAdvance(text);left=(self.width()-tw-iw-gap)/2
        if iw:
            source=icon.pixmap(self.iconSize(),QIcon.Normal if self.isEnabled() else QIcon.Disabled)
            tinted=QPixmap(source.size());tinted.setDevicePixelRatio(source.devicePixelRatioF());tinted.fill(Qt.transparent)
            ink=QPainter(tinted);ink.drawPixmap(0,0,source)
            ink.setCompositionMode(QPainter.CompositionMode_SourceIn);ink.fillRect(tinted.rect(),foreground);ink.end()
            p.drawPixmap(QRectF(left,(self.height()-self.iconSize().height())/2,iw,self.iconSize().height()).toRect(),tinted)
        p.drawText(QRectF(left+iw+gap,0,tw,self.height()),Qt.AlignCenter,text)


def button(text, callback, tip="", name=None):
    w = SmoothButton(text)
    w.clicked.connect(callback)
    if tip:
        w.setToolTip(tip)
    if name:
        w.setObjectName(name)
    return w


def timecode(frame, fps):
    seconds, ff = divmod(max(0, frame), max(1, round(fps)))
    minutes, ss = divmod(seconds, 60)
    hh, mm = divmod(minutes, 60)
    return f"{hh:02}:{mm:02}:{ss:02}:{ff:02}"


