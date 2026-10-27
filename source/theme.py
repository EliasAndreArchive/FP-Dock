"""Shared light/dark UI colors; preview image pixels are never transformed."""
import re
from PySide6.QtCore import QVariantAnimation,QEasingCurve
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

_progress=0.
DARK={
 '#fbfaf6':'#1c1b17','#f8f7f1':'#1c1b17','#ffffff':'#1c1b17',
 '#1c1b17':'#fbfaf6','#e5e2d6':'#454237','#f0eee5':'#454237',
 '#c7c3b6':'#8c8879','#c0bcae':'#686457','#ece9df':'#454237',
 '#454237':'#e5e2d6','#9a9686':'#c0bcae','#8c8879':'#c7c3b6',
 '#625e52':'#c7c3b6','#686457':'#c7c3b6','#6d685b':'#c7c3b6',
 '#e9e4d3':'#454237','#d6d1c3':'#686457','#dedacd':'#454237',
 '#bdb8a9':'#9a9686','#447259':'#8ec7a5','#b84a2a':'#ec927c',
 '#4c74a8':'#8cb7ed'}


def role(light,dark):
 a,b=QColor(light),QColor(dark)
 return QColor.fromRgbF(*[a.getRgbF()[i]*(1-_progress)+b.getRgbF()[i]*_progress for i in range(4)])

def color(value,*args):
 if isinstance(value,str) and value.lower() in DARK:return role(value,DARK[value.lower()])
 return QColor(value,*args)

def stylesheet(css):
 return re.sub(r'#[0-9a-fA-F]{6}',lambda m:color(m.group()).name(),css)

def set_theme(dark,css,animate=True,callback=None):
 global _progress
 app=QApplication.instance()
 if hasattr(app,'_theme_animation'):app._theme_animation.stop()
 def apply(value):
  global _progress
  _progress=float(value);app.setStyleSheet(stylesheet(css))
  for widget in app.allWidgets():
   if hasattr(widget,'_animation') and hasattr(widget,'_refresh'):widget._refresh(True)
   widget.update()
  if callback:callback()
 target=1. if dark else 0.
 if target==_progress and app.styleSheet()==stylesheet(css):
  if callback:callback()
  return
 if not animate:apply(target);return
 animation=QVariantAnimation(app);app._theme_animation=animation
 animation.setStartValue(_progress);animation.setEndValue(target);animation.setDuration(180)
 animation.setEasingCurve(QEasingCurve.InOutCubic);animation.valueChanged.connect(apply);animation.start()
