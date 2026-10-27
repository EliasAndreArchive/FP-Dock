"""Silent video poster extraction; original files are read-only."""
from PySide6.QtCore import QObject,QTimer,QUrl,Signal,Qt
from PySide6.QtMultimedia import QMediaPlayer,QVideoSink


class VideoThumbnail(QObject):
    done=Signal(object)
    def __init__(self,parent=None):
        super().__init__(parent);self.finished=False
        self.player=QMediaPlayer(self);self.sink=QVideoSink(self)
        self.player.setVideoSink(self.sink);self.sink.videoFrameChanged.connect(self.frame)
        self.player.errorOccurred.connect(lambda *_:self.finish((False,self.player.errorString())))
        self.timer=QTimer(self);self.timer.setSingleShot(True)
        self.timer.timeout.connect(lambda:self.finish((False,'Video preview unavailable')))
    def start(self,path):
        self.timer.start(4000);self.player.setSource(QUrl.fromLocalFile(str(path)));self.player.play()
    def frame(self,frame):
        if self.finished or not frame.isValid():return
        image=frame.toImage()
        if not image.isNull():self.finish((True,image.scaled(176,99,Qt.KeepAspectRatio,Qt.SmoothTransformation)))
    def finish(self,result):
        if self.finished:return
        self.finished=True;self.timer.stop();self.player.stop();self.player.setSource(QUrl())
        self.done.emit(result);self.deleteLater()
    def cancel(self):self.finish((False,'Cancelled'))
