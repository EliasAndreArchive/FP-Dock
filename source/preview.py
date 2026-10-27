"""Permanent right-hand player. Live RAW GPU processing; no disk previews."""
from __future__ import annotations
from PySide6.QtCore import Qt,QUrl,Signal,QTimer
from PySide6.QtGui import QPainter,QPen,QDesktopServices,QGuiApplication,QImage,QKeySequence,QShortcut
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QSlider,QStackedWidget,QComboBox,QSizePolicy,QFrame,QButtonGroup
from PySide6.QtMultimedia import QMediaPlayer,QAudioOutput,QMediaMetaData
from PySide6.QtMultimediaWidgets import QVideoWidget
from raw_library import load_metadata
from preview_color import DAVINCI,default_output_view
from live_gpu import create_surface
from live_worker import LiveRawWorker
from browser_motion import LoadingPanel
from visuals import Canvas,label,button,transport_icon,timecode,ElidedLabel,PANEL_PADDING,HEADER_HEIGHT,CONTENT_GAP
from export_naming import recorded_label
from theme import color as ui_color
from playback_slider import PlaybackSlider

VIDEO_EXTENSIONS={".mov",".mp4",".m4v",".mxf",".avi",".mkv",".mts",".m2ts"}

class Preview(QWidget):
    visualsChanged=Signal(str,bool)
    scaleChanged=Signal(int)
    playbackChanged=Signal(bool)
    bufferingChanged=Signal(bool)
    def __init__(self,clips=(),index=0,parent=None,*,view=DAVINCI,auto_exposure=True,reduction=2,**_unused):
        super().__init__(parent)
        self.clips=list(clips)
        self.position=0
        self.view=default_output_view()
        self.auto_exposure=True
        self.clip_aspect=1.5
        self.control_panels=[]
        self.transfer_busy=False
        self.playing=False
        self.buffering=False
        self.want=self.shown=0
        self.rendered_frames=0
        self.epoch=0
        self.backend="Starting"
        self.gpu_active=False
        self.measured_fps=0.0
        self.reduction=reduction if reduction in (2,4,8) else 2
        self.surface=create_surface() if QGuiApplication.platformName()!="offscreen" else None
        self.worker=LiveRawWorker(self.surface,self)
        self.worker.scale(self.reduction)
        self.worker.ready.connect(self.raw_ready)
        self.worker.frameReady.connect(self.raw_frame)
        self.worker.stats.connect(self.raw_stats)
        self.worker.failed.connect(self.raw_error)
        self.worker.buffering.connect(self.raw_buffering)
        self.worker.start()
        layout=QVBoxLayout(self)
        layout.setContentsMargins(PANEL_PADDING,PANEL_PADDING,PANEL_PADDING,16)
        layout.setSpacing(CONTENT_GAP)
        heading=QHBoxLayout()
        self.title=ElidedLabel("Choose a clip")
        self.title.setObjectName('title')
        self.title.setFixedHeight(HEADER_HEIGHT)
        self.title.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Preferred)
        heading.addWidget(self.title,1)
        self.folder_button=button("Folder ↗",self.open_folder,"Open this clip's original folder")
        self.folder_button.setEnabled(False)
        heading.addWidget(self.folder_button)
        layout.addLayout(heading)
        self.detail=label("Live preview · Originals stay unchanged","muted")
        self.detail.setWordWrap(False)
        self.detail.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Preferred)
        layout.addWidget(self.detail)
        self.stack=QStackedWidget()
        self.canvas=Canvas()
        self.canvas.show_brand=False
        self.canvas.setMinimumSize(0,0)
        self.canvas.message="Choose a clip"
        self.canvas.detail="Click an image in the browser. This panel stays here."
        self.video=QVideoWidget()
        self.video.setAspectRatioMode(Qt.KeepAspectRatio)
        self.stack.addWidget(self.canvas)
        self.stack.addWidget(self.video)
        self.loading=LoadingPanel()
        self.stack.addWidget(self.loading)
        self.stack.setSizePolicy(QSizePolicy.Expanding,QSizePolicy.Fixed)
        layout.addWidget(self.stack)
        self.build_status=label("Live RAW playback · RAM buffering only","muted")
        self.build_status.setWordWrap(True)
        layout.addWidget(self.build_status)
        self.build_status.hide()
        row=QHBoxLayout()
        self.play=button("",self.toggle,"Play / pause (Space)","play")
        self.play.setFixedHeight(34)
        self.play.setIcon(transport_icon("play","#fbfaf6"))
        self.play.setEnabled(False)
        self.folder_button.setEnabled(False)
        row.addWidget(self.play)
        self.time=label("00:00:00:00","muted")
        row.addWidget(self.time)
        self.slider=PlaybackSlider()
        self.slider.sliderPressed.connect(self.begin_scrub)
        self.slider.sliderMoved.connect(self.seek)
        self.slider.sliderReleased.connect(self.end_scrub)
        self.slider.valueChanged.connect(self.slider_changed)
        row.addWidget(self.slider,1)
        self.end_time=label("00:00:00:00","muted")
        for counter in (self.time,self.end_time):
            counter.setStyleSheet("QLabel { font-family: 'Consolas'; font-size: 12px; }")
            counter.ensurePolished()
            counter.setFixedWidth(max(90,counter.fontMetrics().horizontalAdvance('00:00:00:00')+8))
        row.addWidget(self.end_time)
        layout.addSpacing(PANEL_PADDING-CONTENT_GAP)
        layout.addLayout(row)
        self.scale_combo=QComboBox(self)
        self.scale_combo.hide()
        self.scale_combo.setCursor(Qt.PointingHandCursor)
        for name,value in (("½ · Quality",2),("¼ · Balanced",4),("⅛ · Fast",8)):
            self.scale_combo.addItem(name,value)
        self.scale_combo.setCurrentIndex((2,4,8).index(self.reduction))
        self.scale_combo.setToolTip("Playback resolution: up to 960, 480, or 240 pixels wide. Reduces rendering work; lossless RAW decompression is still required. Originals are unchanged.")
        self.scale_combo.currentIndexChanged.connect(self.scale_control_changed)
        self.scale_combo.setMaximumWidth(120)
        self.quality_bar=QFrame()
        self.quality_bar.setObjectName('qualitySegments')
        segments=QHBoxLayout(self.quality_bar)
        segments.setContentsMargins(2,2,2,2);segments.setSpacing(2)
        self.quality_group=QButtonGroup(self);self.quality_group.setExclusive(True)
        self.quality_buttons=[]
        for index,text in enumerate(('½ Quality','¼ Balanced','⅛ Fast')):
            segment=button(text,lambda checked=False,index=index:self.scale_combo.setCurrentIndex(index),self.scale_combo.itemText(index),'segment')
            segment.setCheckable(True);segment.setFixedHeight(28)
            self.quality_group.addButton(segment,index);self.quality_buttons.append(segment)
            segment.setChecked(index==self.scale_combo.currentIndex())
            segments.addWidget(segment)
        self.quality_bar.setFixedHeight(HEADER_HEIGHT)
        heading.insertWidget(1,self.quality_bar)
        self.audio_button=button("Sound",self.mute,"Mute / unmute")
        self.audio_button.setCheckable(True)
        self.audio_button.setEnabled(False)
        heading.insertWidget(2,self.audio_button)
        layout.addStretch(1)
        self.audio=QAudioOutput(self)
        self.audio.setVolume(.7)
        self.media=QMediaPlayer(self)
        self.media.setAudioOutput(self.audio)
        self.media.setVideoOutput(self.video)
        self.media.setLoops(QMediaPlayer.Infinite)
        self.media.positionChanged.connect(self.media_position)
        self.media.durationChanged.connect(self.media_duration)
        self.media.metaDataChanged.connect(self.video_metadata)
        self.media.errorOccurred.connect(self.media_error)
        self.video.videoSink().videoFrameChanged.connect(self.video_frame)
        self.sidecar_audio=QAudioOutput(self)
        self.sidecar_audio.setVolume(.7)
        self.sidecar=QMediaPlayer(self)
        self.sidecar.setAudioOutput(self.sidecar_audio)
        self.shortcuts=[]
        for key,callback in {"Space":self.toggle,"Left":lambda:self.step(-1),"Right":lambda:self.step(1)}.items():
            shortcut=QShortcut(QKeySequence(key),self)
            shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            shortcut.activated.connect(callback)
            self.shortcuts.append(shortcut)
        if self.clips:
            self.open_clip(index,autoplay=False)

    @property
    def clip(self):
        return self.clips[self.position] if self.clips else None

    @property
    def raw(self):
        return self.clip is not None and getattr(self.clip,"kind","raw")=="raw"

    def set_clips(self,clips,index=0,autoplay=False):
        self.clips=list(clips)
        if self.clips:
            self.open_clip(index,autoplay)
        else:
            self.clear_clip()

    def clear_clip(self):
        self.pause()
        self.media.stop()
        self.media.setSource(QUrl())
        self.sidecar.stop()
        self.sidecar.setSource(QUrl())
        self.clips=[]
        self.buffering=False
        self.loading.set_loading(False)
        self.epoch=self.worker.configure(None,self.view,self.auto_exposure,False)
        self.canvas.image=QImage()
        self.canvas.message="Choose a clip"
        self.canvas.detail="The selected clip will always appear here."
        self.canvas.update()
        self.stack.setCurrentIndex(0)
        self.title.setText("Choose a clip")
        self.detail.setText('Connect your Sigma FP or SD card, then select a clip.')
        self.build_status.setText('Live preview · Original RAW files stay unchanged')
        self.folder_button.setEnabled(False)
        self.audio_button.setEnabled(False)
        self.slider.setRange(0,0)
        self.time.setText('00:00:00:00');self.end_time.setText('00:00:00:00')
        self.play.setEnabled(False)

    def open_clip(self,index,autoplay=True):
        self.buffering=True
        self.pause()
        self.position=max(0,min(index,len(self.clips)-1))
        self.want=self.shown=0
        self.rendered_frames=0
        self.measured_fps=0
        self.video_shape_known=False
        self.build_status.hide()
        self.title.setText(self.clip.name)
        self.title.setToolTip(self.clip.name)
        self.detail.setText(recorded_label(self.clip))
        self.detail.setToolTip(str(self.clip.folder))
        self.folder_button.setToolTip(str(self.clip.folder))
        self.media.stop()
        self.media.setSource(QUrl())
        self.sidecar.stop()
        self.sidecar.setSource(QUrl())
        self.audio_button.setEnabled(bool(self.clip.audio) or not self.raw)
        self.scale_combo.setEnabled(self.raw)
        self.quality_bar.setEnabled(self.raw)
        self.play.setEnabled(True)
        self.folder_button.setEnabled(True)
        self.stack.setCurrentIndex(0 if self.raw else 1)
        if self.raw:
            self.loading.set_loading(True,'Loading clip…\nPreparing smooth playback in RAM.')
            self.stack.setCurrentWidget(self.loading)
            self.canvas.image=QImage()
            self.canvas.message="Opening RAW frame…"
            self.canvas.detail="Live rendering. No clip preparation."
            self.canvas.update()
            self.slider.setRange(0,len(self.clip.frames)-1)
            if self.clip.audio:
                self.sidecar.setSource(QUrl.fromLocalFile(str(self.clip.audio)))
            self.playing=bool(autoplay)
            self.epoch=self.worker.configure(self.clip,self.view,self.auto_exposure,autoplay)
            self.build_status.setText("Opening first frame · No disk cache")
            self.play.setIcon(transport_icon("pause" if autoplay else "play","#fbfaf6"))
            self.playbackChanged.emit(self.playing)
        else:
            self.buffering=True
            self.loading.set_loading(True,'Loading clip…\nOpening the video stream.')
            self.stack.setCurrentWidget(self.loading)
            self.epoch=self.worker.configure(None,self.view,self.auto_exposure,False)
            self.media.setSource(QUrl.fromLocalFile(str(self.clip.frames[0])))
            self.build_status.setText("Direct video playback · Hardware decoding where supported")
            if autoplay:
                self.start()
        self.update_time()

    def paintEvent(self,event):
        super().paintEvent(event)
        painter=QPainter(self);painter.setPen(QPen(ui_color('#e5e2d6'),1))
        for widget,edge in getattr(self,'divider_widgets',[]):
            y=widget.geometry().bottom()+1 if edge=='bottom' else widget.y()-1
            painter.drawLine(0,y,self.width()-1,y)

    def resizeEvent(self,event):
        super().resizeEvent(event)
        if hasattr(self,'stack'):QTimer.singleShot(0,self.fit_video)

    def add_control_panel(self,panel):
        self.control_panels.append(panel)
        self.layout().insertWidget(self.layout().count()-1,panel)
        QTimer.singleShot(0,self.fit_video)

    def fit_video(self):
        if not hasattr(self,'stack'):return
        width=max(1,self.stack.width())
        layout=self.layout();margins=layout.contentsMargins()
        reserved=margins.top()+margins.bottom();count=0
        for i in range(layout.count()):
            item=layout.itemAt(i)
            if item.spacerItem():
                reserved+=item.spacerItem().sizeHint().height()
                continue
            if item.widget() is self.stack or item.isEmpty():continue
            height=item.heightForWidth(max(1,item.geometry().width())) if item.hasHeightForWidth() else item.sizeHint().height()
            reserved+=max(0,height);count+=1
        available=max(80,self.height()-reserved-count*layout.spacing())
        self.stack.setFixedHeight(max(80,min(round(width/max(.1,self.clip_aspect)),available)))

    def set_image_aspect(self,width,height):
        if width>0 and height>0:
            aspect=width/height
            if abs(aspect-self.clip_aspect)>.0001:
                self.clip_aspect=aspect
            self.fit_video()

    def set_transfer_busy(self,busy):
        self.transfer_busy=bool(busy)
        available=not busy and self.clip is not None
        for control in (self.play,self.slider,self.folder_button):control.setEnabled(available)
        self.scale_combo.setEnabled(available and self.raw)
        self.quality_bar.setEnabled(available and self.raw)
        self.audio_button.setEnabled(available and (not self.raw or bool(self.clip.audio)))
        QTimer.singleShot(0,self.fit_video)

    def raw_ready(self,data,epoch):
        if epoch!=self.epoch or not self.raw:
            return
        self.backend=data["backend"]
        self.gpu_active=data["gpu"]
        seconds=round(self.clip.duration)
        self.detail.setText(recorded_label(self.clip)+f" · {self.clip.width} × {self.clip.height} · {self.clip.fps:g} fps · {seconds//60}:{seconds%60:02}")
        self.detail.setToolTip(self.detail.text())
        self.build_status.setText("Live GPU debayer + color · No disk cache" if data["gpu"] else "Live CPU fallback · No disk cache")
        self.build_status.setToolTip(data["backend"]+"\nRAW unpack / decompression runs on CPU. A bounded read-ahead queue is held in RAM.")
        self.update_time()

    def raw_buffering(self,data,epoch):
        if epoch!=self.epoch or not self.raw:return
        self.buffering=data['active']
        if self.buffering:
            self.build_status.setText('Loading clip in RAM…')
            self.loading.set_loading(True,f"Loading clip…\nPreparing frames {data['ready']} / {data['target']} in RAM.")
            self.stack.setCurrentWidget(self.loading)
            self.sidecar.pause()
        else:
            self.build_status.setText('Ready · Live '+('GPU' if self.gpu_active else 'CPU')+' · No disk cache')
            self.loading.set_loading(False)
            self.stack.setCurrentWidget(self.canvas)
            if self.playing and self.clip.audio:
                self.sidecar.setPosition(round(self.shown/self.clip.fps*1000));self.sidecar.play()
        self.bufferingChanged.emit(self.buffering)

    def raw_frame(self,image,index,epoch):
        if epoch!=self.epoch or not self.raw:
            return
        self.rendered_frames+=1
        self.preview_size=(image.width(),image.height())
        if self.rendered_frames==1 or abs(image.width()/image.height()-self.clip_aspect)>.001:
            self.set_image_aspect(image.width(),image.height())
        self.want=self.shown=index
        self.canvas.image=image
        self.canvas.update()
        self.update_time()
        if self.playing and not self.buffering and self.clip.audio:
            expected=round(index/self.clip.fps*1000)
            if abs(self.sidecar.position()-expected)>150:
                self.sidecar.setPosition(expected)
                self.sidecar.play()

    def raw_stats(self,data,epoch):
        if epoch!=self.epoch:
            return
        self.measured_fps=data["fps"]
        width,height=getattr(self,'preview_size',(0,0))
        self.build_status.setText(f"Live {'GPU' if data['gpu'] else 'CPU'} · {data['fps']:.1f} / {data['target']:g} fps · {width} × {height}")
        self.build_status.setToolTip(data["backend"]+f"\nMeasured unpack throughput: {data['read_mbps']:.0f} MB/s\nGPU debayer/color; CPU RAW unpack. Read speed can limit frame rate.")

    def raw_error(self,message,epoch):
        if epoch!=self.epoch:
            return
        self.pause()
        self.buffering=False;self.loading.set_loading(False);self.stack.setCurrentWidget(self.canvas)
        self.canvas.message='Preview unavailable';self.canvas.detail='Reconnect the camera or select another clip.';self.canvas.update()
        self.build_status.setText("Preview unavailable: "+message+". Original-file transfer is unaffected.")
        self.build_status.show()

    def start(self):
        if not self.clip or self.transfer_busy:
            return
        self.playing=True
        if self.raw:
            self.worker.play(True)
            if self.clip.audio and not self.buffering:
                self.sidecar.setPosition(round(self.shown/self.clip.fps*1000))
                self.sidecar.play()
        else:
            self.media.play()
        self.play.setIcon(transport_icon("pause","#fbfaf6"))
        self.playbackChanged.emit(True)

    def pause(self):
        self.playing=False
        if hasattr(self,"media"):
            self.worker.play(False)
            self.media.pause()
            self.sidecar.pause()
            self.play.setIcon(transport_icon("play","#fbfaf6"))
            self.playbackChanged.emit(False)
            if hasattr(self,'slider') and self.raw:self.update_time()

    def toggle(self):
        self.pause() if self.playing else self.start()

    def update_time(self):
        if self.raw:
            self.time.setText(timecode(self.shown,self.clip.fps))
            self.end_time.setText(timecode(len(self.clip.frames)-1,self.clip.fps))
            if not self.slider.isSliderDown():
                self.slider.blockSignals(True)
                self.slider.sync(self.shown,self.playing and not self.buffering,1/self.clip.fps)
                self.slider.blockSignals(False)

    def seek(self,value):
        if self.transfer_busy:return
        self.pause()
        if self.raw:
            self.want=max(0,min(int(value),len(self.clip.frames)-1))
            self.worker.seek(self.want)
        elif self.clip:
            self.media.setPosition(int(value))

    def step(self,offset):
        if self.clip:
            self.seek(self.want+offset if self.raw else self.media.position()+round(offset*1000/24))

    def slider_changed(self,value):
        if not self.slider.isSliderDown():
            self.seek(value)

    def begin_scrub(self):
        self.was_playing=self.playing
        self.pause()

    def end_scrub(self):
        self.seek(self.slider.value())
        if getattr(self,"was_playing",False):
            self.start()

    def media_position(self,value):
        if self.clip and not self.raw:
            self.slider.blockSignals(True)
            self.slider.sync(value,self.playing,.1)
            self.slider.blockSignals(False)
            self.time.setText(f"{value//60000:02}:{value//1000%60:02}")

    def media_duration(self,value):
        if self.clip and not self.raw:
            self.slider.setRange(0,value)
            self.end_time.setText(f"{value//60000:02}:{value//1000%60:02}")
            self.video_metadata()

    def video_metadata(self):
        if not self.clip or self.raw:return
        metadata=self.media.metaData()
        resolution=metadata.value(QMediaMetaData.Resolution)
        fps=metadata.value(QMediaMetaData.VideoFrameRate)
        info=[]
        if resolution and resolution.width()>0 and resolution.height()>0:
            info.append(f'{resolution.width()} × {resolution.height()}')
        if isinstance(fps,(int,float)) and fps>0:info.append(f'{fps:g} fps')
        seconds=self.media.duration()//1000
        if seconds:info.append(f'{seconds//60}:{seconds%60:02}')
        self.detail.setText(recorded_label(self.clip)+(' · '+' · '.join(info) if info else ''))
        self.detail.setToolTip(self.detail.text())

    def video_frame(self,frame):
        if frame.isValid():
            self.rendered_frames+=1
            if self.clip and not self.raw:
                if not self.video_shape_known:
                    image=frame.toImage()
                    if not image.isNull():self.set_image_aspect(image.width(),image.height())
                    self.video_shape_known=True
                self.buffering=False;self.loading.set_loading(False)
                self.stack.setCurrentWidget(self.video)
                self.bufferingChanged.emit(False)

    def media_error(self,*_):
        if self.clip and not self.raw:
            self.pause()
            self.buffering=False;self.loading.set_loading(False);self.stack.setCurrentWidget(self.canvas)
            self.build_status.setText("Video preview unavailable: "+self.media.errorString())
        self.build_status.show()

    def scale_control_changed(self,*_):
        for index,segment in enumerate(getattr(self,'quality_buttons',[])):
            segment.setChecked(index==self.scale_combo.currentIndex())
        self.reduction=self.scale_combo.currentData()
        self.worker.scale(self.reduction)
        self.scaleChanged.emit(self.reduction)

    def set_visual_settings(self,view,auto):
        self.view,self.auto_exposure=default_output_view(),True
        self.worker.visuals(self.view,True)

    def change(self,offset):
        index=self.position+offset
        if 0<=index<len(self.clips):
            self.open_clip(index)

    def open_folder(self):
        if self.clip:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.clip.folder)))

    def mute(self):
        muted=self.audio_button.isChecked()
        self.audio.setMuted(muted)
        self.sidecar_audio.setMuted(muted)
        self.audio_button.setText("Muted" if muted else "Sound")

    def shutdown(self):
        self.pause()
        self.media.stop()
        self.sidecar.stop()
        self.media.setSource(QUrl())
        self.sidecar.setSource(QUrl())
        self.worker.halt()
        self.worker.wait()
        if self.surface:
            self.surface.destroy()
            self.surface=None
