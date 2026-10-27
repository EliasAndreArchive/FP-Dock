"""Short-lived browser animations; idle browsing and playback have no animation loop."""
import math,time
from theme import color as ui_color

from PySide6.QtCore import Qt,QTimer,QPropertyAnimation,QEasingCurve,QEvent,QRectF,QPointF,QObject
from PySide6.QtGui import QPainter,QColor,QPen,QFont,QPixmap,QIcon
from PySide6.QtWidgets import QListWidget,QAbstractItemView,QWidget

APP_TITLE='FP Dock'


def refresh_interval(widget):
    screen=widget.screen()
    hz=screen.refreshRate() if screen else 60
    return max(1,int(1000/max(60,min(360,hz))))


class DisplayScroller(QObject):
    """A precise, display-rate scroll timer instead of Qt's 60 Hz animation clock."""
    def __init__(self,bar,parent):
        super().__init__(parent)
        self.bar=bar;self.duration=.19;self.first=0;self.last=0;self.started=0
        self.curve=QEasingCurve(QEasingCurve.OutCubic)
        self.timer=QTimer(self);self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.timeout.connect(self.tick)
    def setDuration(self,milliseconds):self.duration=milliseconds/1000
    def setEasingCurve(self,curve):self.curve=QEasingCurve(curve)
    def setStartValue(self,value):self.first=value
    def setEndValue(self,value):self.last=value
    def state(self):return QPropertyAnimation.Running if self.timer.isActive() else QPropertyAnimation.Stopped
    def stop(self):self.timer.stop()
    def start(self):
        self.started=time.perf_counter();self.timer.start(refresh_interval(self.bar))
    def tick(self):
        t=min(1,(time.perf_counter()-self.started)/self.duration)
        value=self.first+(self.last-self.first)*self.curve.valueForProgress(t)
        self.bar.setValue(round(value))
        if t>=1:self.timer.stop()


def dock_icon():
    pixmap=QPixmap(64,64)
    pixmap.fill(Qt.transparent)
    p=QPainter(pixmap)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen);p.setBrush(ui_color('#1c1b17'))
    p.drawRoundedRect(QRectF(3,3,58,58),14,14)
    p.setPen(ui_color('#fbfaf6'));p.setFont(QFont('Inter',22,QFont.DemiBold))
    p.drawText(pixmap.rect(),Qt.AlignCenter,'FP')
    p.end()
    return QIcon(pixmap)


class MotionGrid(QListWidget):
    def __init__(self,parent=None):
        super().__init__(parent)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setMouseTracking(True)
        self.viewport().installEventFilter(self)
        self.entered.connect(lambda index:self.set_hover(index.row()))
        self.viewportEntered.connect(lambda:self.set_hover(-1))
        self._hover_row=-1
        self._hover_values={}
        self._hover_animations={}
        self._intro_started=None
        self._intro_duration=.56
        self.motion_timer=QTimer(self)
        self.motion_timer.setTimerType(Qt.PreciseTimer)
        self.motion_timer.setInterval(refresh_interval(self))
        self.motion_timer.timeout.connect(self.animate_tick)
        self.scroll_animation=DisplayScroller(self.verticalScrollBar(),self)
        self.scroll_animation.setDuration(190)
        self.scroll_animation.setEasingCurve(QEasingCurve.OutCubic)
        self._scroll_target=0
        self.verticalScrollBar().sliderPressed.connect(self.scroll_animation.stop)

    def clear(self):
        self.motion_timer.stop();self.scroll_animation.stop()
        self._hover_row=-1;self._hover_values.clear();self._hover_animations.clear()
        self._intro_started=None
        delegate=self.itemDelegate()
        if hasattr(delegate,'clear_cache'):delegate.clear_cache()
        super().clear()

    def wheelEvent(self,event):
        bar=self.verticalScrollBar()
        pixels=event.pixelDelta().y()
        angle=event.angleDelta().y()
        if pixels:
            self.scroll_animation.stop()
            bar.setValue(bar.value()-pixels)
        elif angle and bar.maximum()>0:
            active=self.scroll_animation.state()==QPropertyAnimation.Running
            target=self._scroll_target if active else bar.value()
            self._scroll_target=max(bar.minimum(),min(bar.maximum(),target-angle/120*112))
            self.scroll_animation.stop()
            self.scroll_animation.setStartValue(bar.value())
            self.scroll_animation.setEndValue(round(self._scroll_target))
            self.scroll_animation.start()
        else:
            super().wheelEvent(event)
            return
        event.accept()

    def eventFilter(self,obj,event):
        if obj is self.viewport() and event.type()==QEvent.Leave:
            self.set_hover(-1)
        return super().eventFilter(obj,event)

    def set_hover(self,row):
        if row==self._hover_row:
            return
        now=time.monotonic()
        for index,target in ((self._hover_row,0),(row,1)):
            if index>=0:
                self._hover_animations[index]=(now,self.hover_value(index),target)
        self._hover_row=row
        self.viewport().setCursor(Qt.PointingHandCursor if row>=0 else Qt.ArrowCursor)
        self.motion_timer.start(refresh_interval(self))

    def hover_value(self,row):
        return self._hover_values.get(row,0)

    def animate_in(self):
        self._intro_started=time.monotonic()
        self.motion_timer.start(refresh_interval(self))

    def reveal_progress(self,row):
        if self._intro_started is None:
            return 1.0
        progress=max(0,min(1,(time.monotonic()-self._intro_started-min(row,23)*.014)/.24))
        return QEasingCurve(QEasingCurve.OutCubic).valueForProgress(progress)

    def animate_tick(self):
        now=time.monotonic()
        for row,(started,first,target) in list(self._hover_animations.items()):
            t=min(1,(now-started)/.18)
            ease=QEasingCurve(QEasingCurve.OutBack).valueForProgress(t)
            self._hover_values[row]=first+(target-first)*ease
            if t>=1:
                self._hover_animations.pop(row)
                if target==0:
                    self._hover_values.pop(row,None)
        if self._intro_started is not None and now-self._intro_started>=self._intro_duration:
            self._intro_started=None
        self.viewport().update()
        if not self._hover_animations and self._intro_started is None:
            self.motion_timer.stop()


class LoadingPanel(QWidget):
    def __init__(self,parent=None):
        super().__init__(parent)
        self._text='Connect your Sigma FP or insert its SD card.\nCINEMA will appear here automatically.'
        self.active=False
        self.started=time.monotonic()
        self.timer=QTimer(self);self.timer.setInterval(33);self.timer.timeout.connect(self.update)

    def setText(self,text):
        self._text=text;self.update()

    def text(self):
        return self._text

    def set_loading(self,active,text=None):
        self.active=active
        if text is not None:self._text=text
        if active and self.isVisible():self.timer.start()
        else:self.timer.stop()
        self.update()

    def showEvent(self,event):
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.setInterval(refresh_interval(self))
        if self.active:self.timer.start()
        super().showEvent(event)

    def hideEvent(self,event):
        self.timer.stop();super().hideEvent(event)

    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.Antialiasing)
        cx,cy=self.width()/2,self.height()/2
        if self.active:
            phase=time.monotonic()-self.started
            for i in range(3):
                p.save();p.translate(cx+(i-1)*39,cy-48+math.sin(phase*4-i*.8)*3)
                p.rotate((i-1)*9+math.sin(phase*3-i)*2)
                p.setPen(QPen(ui_color('#dedacd'),1));p.setBrush(ui_color('#ffffff'))
                p.drawRoundedRect(QRectF(-23,-29,46,58),5,5)
                shade=231+int(math.sin(phase*4-i)*7)
                p.fillRect(QRectF(-17,-23,34,31),ui_color(shade,shade-1,shade-6))
                p.setPen(QPen(ui_color('#bdb8a9'),2,Qt.SolidLine,Qt.RoundCap))
                p.drawLine(QPointF(-16,17),QPointF(8,17));p.restore()
        p.setPen(ui_color('#8c8879'));p.setFont(QFont('Inter',10))
        p.drawText(QRectF(25,cy+5,self.width()-50,115),Qt.AlignHCenter|Qt.TextWordWrap,self._text)
        p.end()
