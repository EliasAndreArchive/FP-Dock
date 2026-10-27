"""Render-ahead RAW stream with a bounded RAM buffer and deadline-based delivery."""
import math,threading,time
from concurrent.futures import ThreadPoolExecutor
from PySide6.QtCore import QThread,Signal
from PySide6.QtGui import QImage
from raw_library import load_metadata,develop_frame
from live_gpu import calibrate_bayer,unpack_bayer,GpuRenderer


class LiveRawWorker(QThread):
    frameReady=Signal(object,int,int)
    ready=Signal(object,int)
    stats=Signal(object,int)
    failed=Signal(str,int)
    buffering=Signal(object,int)

    def __init__(self,surface=None,parent=None):
        super().__init__(parent)
        self.surface=surface
        self.lock=threading.Lock();self.wake=threading.Event();self.stopping=threading.Event()
        self.state={'epoch':0,'clip':None,'view':None,'auto':True,'playing':False,'seek':0,'serial':0,'visual':0,'reduction':2}

    def configure(self,clip,view,auto,playing=False):
        with self.lock:
            self.state.update(epoch=self.state['epoch']+1,clip=clip,view=view,auto=auto,playing=playing,seek=0,
                              serial=self.state['serial']+1,visual=self.state['visual']+1)
            epoch=self.state['epoch']
        self.wake.set();return epoch

    def play(self,value):
        with self.lock:self.state['playing']=bool(value)
        self.wake.set()

    def seek(self,index):
        with self.lock:self.state.update(seek=int(index),serial=self.state['serial']+1,playing=False)
        self.wake.set()

    def visuals(self,view,auto):
        with self.lock:self.state.update(view=view,auto=bool(auto),visual=self.state['visual']+1)
        self.wake.set()

    def scale(self,reduction):
        if reduction not in (2,4,8):raise ValueError('Unsupported playback scale')
        with self.lock:self.state.update(reduction=reduction,visual=self.state['visual']+1)
        self.wake.set()

    def halt(self):
        self.stopping.set();self.wake.set()

    @staticmethod
    def max_size(reduction):return (1920//reduction,1280//reduction)

    @staticmethod
    def _image(rgb):
        return QImage(rgb.data,rgb.shape[1],rgb.shape[0],rgb.strides[0],QImage.Format_RGB888).copy()

    @staticmethod
    def _cpu_decode(path,view,auto,ev,reduction=2):
        t=time.perf_counter()
        rgb,_=develop_frame(path,'Fast',view,auto,ev,preview_size=LiveRawWorker.max_size(reduction),preview_reduction=reduction)
        return rgb,time.perf_counter()-t

    @staticmethod
    def _render(renderer,frame,model,state,ev):
        return renderer.render(frame,model,state['view'],state['auto'],LiveRawWorker.max_size(state['reduction']),state['reduction'])

    def run(self):
        renderer=None;tried_gpu=False;clip=None;model=None;gpu=False;ev=None
        epoch=serial=visual=-1;sequence=0;cursor=1;was_playing=False
        current=None;images={};futures={};priming=False;target=24;raw_limit=6
        next_due=None;render_guard=.006;stat_started=time.perf_counter();delivered=0;read_seconds=read_bytes=0
        last_progress=None;underruns=0;last_emit=None;max_gap=0
        def clear():
            for f in futures.values():f.cancel()
            futures.clear();images.clear()
        def notify(active):
            nonlocal last_progress
            data=(active,len(images),target)
            if data!=last_progress:
                self.buffering.emit({'active':active,'ready':len(images),'target':target},epoch)
                last_progress=data
        with ThreadPoolExecutor(max_workers=3,thread_name_prefix='RAW read ahead') as loaders:
            while not self.stopping.is_set():
                with self.lock:state=dict(self.state)
                if state['clip'] is None:
                    if clip is not None:
                        clear();clip=None;current=None;model=None
                        if renderer:renderer.close();renderer=None
                        tried_gpu=False
                    self.wake.wait(.1);self.wake.clear();continue
                try:
                    changed=state['epoch']!=epoch
                    relocated=changed or state['serial']!=serial
                    recolored=state['visual']!=visual
                    if changed or relocated or recolored:
                        clear();last_progress=None;priming=True;next_due=None
                        if changed:
                            epoch=state['epoch'];clip=state['clip'];sequence=0;model=None;ev=None;gpu=False
                            target=max(1,min(64,round(clip.fps),len(clip.frames)))
                            notify(True)
                            if not tried_gpu:
                                tried_gpu=True
                                if self.surface is not None:
                                    try:renderer=GpuRenderer(self.surface)
                                    except Exception:renderer=None
                            load_metadata(clip)
                            if renderer:
                                try:first,model=calibrate_bayer(clip.frames[0]);gpu=True
                                except Exception:gpu=False
                            if not gpu:
                                first,ev=develop_frame(clip.frames[0],'Fast',state['view'],True,preview_size=self.max_size(state['reduction']),preview_reduction=state['reduction'])
                                if not state['auto']:first,_=self._cpu_decode(clip.frames[0],state['view'],False,ev,state['reduction'])
                            self.ready.emit({'gpu':gpu,'backend':renderer.name if gpu else 'CPU fallback','fps':clip.fps,'frames':len(clip.frames),'ev':model.ev if gpu else ev},epoch)
                            underruns=0
                        else:
                            if relocated:sequence=max(0,min(state['seek'],len(clip.frames)-1))
                            notify(True)
                            if gpu:first,_=unpack_bayer(clip.frames[sequence%len(clip.frames)],model,True)
                            else:
                                if recolored:
                                    _,ev=develop_frame(clip.frames[0],'Fast',state['view'],True,preview_size=self.max_size(state['reduction']),preview_reduction=state['reduction'])
                                first,_=self._cpu_decode(clip.frames[sequence%len(clip.frames)],state['view'],state['auto'],ev,state['reduction'])
                        raw_limit=max(2,min(6,(128*1024**2)//max(1,first.nbytes)))
                        current=self._render(renderer,first,model,state,ev) if gpu else self._image(first)
                        target=max(1,min(round(clip.fps),64,len(clip.frames),(96*1024**2)//max(1,current.sizeInBytes())))
                        cursor=sequence+1;serial=state['serial'];visual=state['visual'];was_playing=state['playing']
                        self.frameReady.emit(current,sequence%len(clip.frames),epoch)
                        stat_started=time.perf_counter();delivered=0;last_emit=None;max_gap=0
                    if epoch!=self.state['epoch']:continue
                    now=time.perf_counter();period=1/max(1,clip.fps)
                    if state['playing']!=was_playing:
                        was_playing=state['playing'];next_due=None;last_emit=None
                        stat_started=now;delivered=0;max_gap=0
                    if state['playing'] and not priming:
                        if next_due is None:next_due=now+period
                        if now>=next_due:
                            following=images.pop(sequence+1,None)
                            if following is None:
                                priming=True;next_due=None;underruns+=1;last_progress=None;notify(True)
                            else:
                                sequence+=1;current=following
                                self.frameReady.emit(current,sequence%len(clip.frames),epoch)
                                delivered+=1
                                if last_emit is not None:max_gap=max(max_gap,now-last_emit)
                                last_emit=now
                                next_due+=period
                                if now-next_due>period/2:next_due=now+period
                    # Decode only a bounded number of sensor frames; prepared display
                    # images have a separate 96 MiB ceiling. No source frames are skipped.
                    while len(futures)<raw_limit and len(images)+len(futures)<target:
                        path=clip.frames[cursor%len(clip.frames)]
                        futures[cursor]=loaders.submit(unpack_bayer,path,model,True) if gpu else loaders.submit(self._cpu_decode,path,state['view'],state['auto'],ev,state['reduction'])
                        cursor+=1
                    first_needed=sequence+1
                    while first_needed in images:first_needed+=1
                    future=futures.get(first_needed)
                    headroom=float('inf') if next_due is None or priming or not state['playing'] else next_due-time.perf_counter()
                    if future is not None and future.done() and headroom>render_guard:
                        frame,elapsed=futures.pop(first_needed).result()
                        read_seconds+=elapsed;read_bytes+=frame.nbytes
                        began=time.perf_counter()
                        images[first_needed]=self._render(renderer,frame,model,state,ev) if gpu else self._image(frame)
                        cost=time.perf_counter()-began
                        render_guard=max(.004,min(.025,max(cost*1.5,render_guard*.97)))
                        if priming:notify(True)
                        continue
                    if priming and len(images)>=target:
                        priming=False;notify(False)
                        self.frameReady.emit(current,sequence%len(clip.frames),epoch)
                        next_due=time.perf_counter()+period if state['playing'] else None
                        stat_started=time.perf_counter();delivered=0;last_emit=None;max_gap=0
                    now=time.perf_counter()
                    if state['playing'] and not priming and now-stat_started>=1:
                        self.stats.emit({'fps':delivered/(now-stat_started),'target':clip.fps,'gpu':gpu,'backend':renderer.name if gpu else 'CPU fallback',
                            'read_mbps':read_bytes/max(.001,read_seconds)/1024**2,'buffer_frames':len(images),'underruns':underruns,'max_gap_ms':max_gap*1000},epoch)
                        stat_started=now;delivered=0;read_seconds=read_bytes=0;max_gap=0
                    if not state['playing'] and not priming and len(images)>=target:
                        self.wake.wait(.1);self.wake.clear()
                    else:
                        # Python's high-resolution waitable timer on Windows avoids
                        # the coarse timeouts of threading.Event.wait at frame deadlines.
                        delay=.001 if next_due is None else min(.003,max(.0002,next_due-time.perf_counter()-.0005))
                        time.sleep(delay)
                except Exception as exc:
                    self.failed.emit(str(exc),epoch);clear()
                    with self.lock:
                        if self.state['epoch']==epoch:self.state.update(playing=False,clip=None)
                    clip=None;current=None;self.wake.wait(.05)
            clear()
        if renderer:renderer.close()
