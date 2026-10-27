"""Smooth UI playhead; the stored value and seeking remain frame accurate."""
import time
from theme import color as ui_color

from PySide6.QtCore import Qt,QTimer,QRectF
from PySide6.QtGui import QPainter,QColor
from PySide6.QtWidgets import QSlider,QAbstractSlider


class PlaybackSlider(QSlider):
    def __init__(self,parent=None):
        super().__init__(Qt.Horizontal,parent)
        self.origin=self.target=0.0;self.started=0.0;self.duration=0.0
        self._syncing=False
        self.timer=QTimer(self);self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.timeout.connect(self.tick)

    def position_at(self,now=None):
        now=time.perf_counter() if now is None else now
        alpha=1 if self.duration<=0 else max(0,min(1,(now-self.started)/self.duration))
        return self.origin+(self.target-self.origin)*alpha

    def sync(self,value,animate=False,period=1/24):
        now=time.perf_counter();current=self.position_at(now);previous=self.value()
        self._syncing=True
        self.setValue(value)
        self._syncing=False
        value=float(self.value())
        self.origin=min(current,value) if animate and value>previous and not self.isSliderDown() else value
        self.target=value;self.started=now
        self.duration=max(.001,period) if self.origin!=value else 0
        if self.duration:
            hz=self.screen().refreshRate() if self.screen() else 60
            self.timer.start(max(4,round(1000/max(60,hz))))
        else:self.timer.stop()
        self.update()

    def sliderChange(self,change):
        super().sliderChange(change)
        if hasattr(self,'timer') and not self._syncing:
            self.timer.stop();self.origin=self.target=float(self.value());self.duration=0
            self.update()

    def tick(self):
        if time.perf_counter()-self.started>=self.duration:self.timer.stop()
        self.update()

    def mousePressEvent(self,event):
        self.timer.stop();self.origin=self.target=float(self.value());self.duration=0
        super().mousePressEvent(event)

    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.Antialiasing)
        left,right=5.0,max(5.0,self.width()-5.0);y=self.height()/2
        fraction=(self.position_at()-self.minimum())/max(1,self.maximum()-self.minimum())
        x=left+(right-left)*max(0,min(1,fraction))
        p.setPen(Qt.NoPen);p.setBrush(ui_color('#e5e2d6'))
        p.drawRoundedRect(QRectF(left,y-1.5,right-left,3),1.5,1.5)
        p.setBrush(ui_color('#1c1b17' if self.isEnabled() else '#c7c3b6'))
        p.drawRoundedRect(QRectF(left,y-1.5,max(0,x-left),3),1.5,1.5)
        p.drawEllipse(QRectF(x-5,y-5,10,10))
