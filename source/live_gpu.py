"""Live Bayer preview: CPU unpack only, GPU half-resolution debayer / color.

No proxy files or clip cache. A camera-linear matrix is calibrated through
LibRaw once using synthetic patches in its in-memory buffer. The source file
is never opened for writing. Unsupported layouts use the normal CPU renderer.
"""
from __future__ import annotations
import time
import io
import os
from dataclasses import dataclass,field
from pathlib import Path
import numpy as np
import rawpy
import tifffile
from shiboken6 import VoidPtr
from PySide6.QtGui import QOpenGLContext,QOffscreenSurface,QSurfaceFormat,QVector3D,QVector4D
from PySide6.QtOpenGL import QOpenGLTexture,QOpenGLFramebufferObject,QOpenGLShaderProgram,QOpenGLShader,QOpenGLVertexArrayObject
from preview_color import DAVINCI,BLACKMAGIC,XYZ_TO_DWG,XYZ_TO_BMD,DWG_TO_REC709,auto_exposure_ev,meter_exposure_ev
from dng_geometry import visible_rectangle,crop_linear,pixel_aspect
from display_lut import is_resolve_view,view_cube

JPEG_TILE_WORKERS=max(1,min(4,(os.cpu_count() or 4)//3))


@dataclass
class BayerModel:
    black: np.ndarray
    span: np.ndarray
    red: np.ndarray
    green: np.ndarray
    blue: np.ndarray
    camera_xyz: np.ndarray
    flip: int
    ev: float
    crop: tuple
    direct_ifd: tuple | None = None
    direct_bits: int = 0
    direct_compression: int = 0
    verified_layouts: dict = field(default_factory=dict)
    display_crop: tuple | None = None
    meter_xyz: np.ndarray | None = None
    output_exposures: dict = field(default_factory=dict)
    pixel_aspect: float = 1.0


@dataclass
class PackedBayer:
    data: np.ndarray
    bits: int
    width: int
    crop: tuple

    @property
    def nbytes(self):
        return self.data.nbytes


def read_packed(page,source,model):
    """Read strips only; packed sensor samples are unpacked by the shader."""
    height,width=page.shape
    stride=(width*page.bitspersample+7)//8
    rows_per_strip=page.rowsperstrip
    data=np.empty((height,stride),np.uint8)
    for i,(offset,count) in enumerate(zip(page.dataoffsets,page.databytecounts)):
        row=i*rows_per_strip
        rows=min(rows_per_strip,height-row)
        needed=rows*stride
        if rows<=0 or count<needed:
            raise ValueError('Invalid packed sensor strip')
        source.seek(offset)
        chunk=source.read(needed)
        if len(chunk)!=needed:
            raise OSError('Incomplete RAW strip')
        data[row:row+rows]=np.frombuffer(chunk,np.uint8).reshape(rows,stride)
    if len(page.dataoffsets)*rows_per_strip<height:
        raise ValueError('Missing RAW strips')
    return PackedBayer(data,page.bitspersample,width,model.crop)


def unpack_bayer(path,model=None,packed_gpu=False):
    started=time.perf_counter()
    if model is not None and model.direct_ifd is not None:
        try:
            # One sequential device read avoids many small metadata reads over USB.
            with tifffile.TiffFile(io.BytesIO(Path(path).read_bytes())) as image:
                pages=image.pages
                page=None
                for item in model.direct_ifd:
                    page=pages[item]
                    pages=page.pages
                if page.bitspersample!=model.direct_bits or page.compression not in (1,7) or len(page.shape)!=2 or page.dtype.kind!='u' or page.photometric!=32803:
                    raise ValueError("RAW storage layout changed")
                layout=(page.shape,page.bitspersample,int(page.compression),page.fillorder)
                if model.verified_layouts.get(layout) is False:
                    raise ValueError('This DNG decoder layout needs LibRaw')
                verified=model.verified_layouts.get(layout,False)
                if verified and packed_gpu and page.compression==1 and page.bitspersample in (10,12,14) and not page.is_tiled and page.fillorder==1:
                    return read_packed(page,image.filehandle,model),time.perf_counter()-started
                top,left,height,width=model.crop
                workers=JPEG_TILE_WORKERS if page.compression==7 and page.is_tiled else 1
                pixels=page.asarray(maxworkers=workers)[top:top+height,left:left+width]
                if pixels.shape!=(height,width):
                    raise ValueError("RAW crop changed")
                if not verified:
                    with rawpy.imread(str(path)) as raw:
                        equal=np.array_equal(pixels,raw.raw_image_visible)
                    model.verified_layouts[layout]=bool(equal)
                    if not equal:
                        raise ValueError('Decoded sensor values differ from LibRaw')
                return np.ascontiguousarray(pixels,dtype=np.uint16),time.perf_counter()-started
        except (OSError,ValueError,IndexError,TypeError,AttributeError):
            pass
    with rawpy.imread(str(path)) as raw:
        data=raw.raw_image_visible
        if data.ndim!=2 or raw.raw_pattern is None or raw.raw_pattern.shape!=(2,2):
            raise ValueError("This RAW layout needs the CPU preview path.")
        frame=np.ascontiguousarray(data.copy())
    return frame,time.perf_counter()-started


def packed_rgb(frame,black,span,red,green,blue):
    h,w=frame.shape
    planes=np.stack([frame[dy:h-h%2:2,dx:w-w%2:2] for dy,dx in ((0,0),(0,1),(1,0),(1,1))],axis=-1)
    planes=np.maximum((planes.astype(np.float32)-black)/span,0)
    return np.stack([planes@red,planes@green,planes@blue],axis=-1)


def calibrate_bayer(path):
    with rawpy.imread(str(path)) as raw:
        image=raw.raw_image_visible
        if image.ndim!=2 or raw.raw_pattern is None or raw.raw_pattern.shape!=(2,2):
            raise ValueError("GPU preview supports 2×2 Bayer RAW layouts.")
        first=np.ascontiguousarray(image.copy())
        ids=[raw.raw_color(raw.sizes.top_margin+dy,raw.sizes.left_margin+dx) for dy,dx in ((0,0),(0,1),(1,0),(1,1))]
        desc=raw.color_desc.decode(errors="replace")
        letters=[desc[i] for i in ids]
        if sorted(letters)!=['B','G','G','R']:
            raise ValueError("Non-RGB Bayer colors require the CPU path.")
        red=np.array([float(c=='R') for c in letters],np.float32)
        green=np.array([.5 if c=='G' else 0 for c in letters],np.float32)
        blue=np.array([float(c=='B') for c in letters],np.float32)
        black=np.array([raw.black_level_per_channel[i] for i in ids],np.float32)
        span=np.maximum(float(raw.white_level)-black,1)
        flip=int(raw.sizes.flip)
        crop=(raw.sizes.top_margin,raw.sizes.left_margin,*first.shape)
        colors=np.array([[.02,.02,.02],[.10,.02,.02],[.02,.10,.02],[.02,.02,.10],
                         [.06,.06,.02],[.02,.06,.06],[.06,.02,.06],[.06,.06,.06]],np.float32)
        h,w=image.shape
        boundaries=np.linspace(0,w//2,9,dtype=int)*2
        features=[]
        for k,color in enumerate(colors):
            values=[]
            for pos,(dy,dx) in enumerate(((0,0),(0,1),(1,0),(1,1))):
                component=0 if letters[pos]=='R' else 2 if letters[pos]=='B' else 1
                value=np.uint16(round(float(black[pos]+color[component]*span[pos])))
                image[dy::2,boundaries[k]+dx:boundaries[k+1]:2]=value
                values.append((float(value)-black[pos])/span[pos])
            values=np.array(values,np.float32)
            features.append([values@red,values@green,values@blue])
        ref=raw.postprocess(half_size=True,demosaic_algorithm=rawpy.DemosaicAlgorithm.LINEAR,
            use_camera_wb=True,no_auto_bright=True,adjust_maximum_thr=0,bright=1,
            user_flip=0,gamma=(1,1),output_bps=16,output_color=rawpy.ColorSpace.XYZ).astype(np.float32)/65535
        if abs((ref.shape[1]/ref.shape[0])/(w/h)-pixel_aspect(path))>.01:
            raise ValueError("This camera's active crop needs the CPU renderer.")
        reference=[]
        for k in range(8):
            x=round((boundaries[k]+boundaries[k+1])/4*ref.shape[1]/(w/2))
            reference.append(np.median(ref[ref.shape[0]//4:3*ref.shape[0]//4,max(0,x-2):x+3],axis=(0,1)))
        features=np.array(features,np.float32)
        reference=np.array(reference,np.float32)
        valid=np.max(reference,axis=1)<.96
        matrix,_,rank,_=np.linalg.lstsq(features[valid],reference[valid],rcond=None)
        if rank<3 or np.max(np.abs(features[valid]@matrix-reference[valid]))>.003:
            raise ValueError("Camera linear calibration could not be verified; using CPU rendering.")
    camera_xyz=matrix.T.astype(np.float32)
    rgb=packed_rgb(first,black,span,red,green,blue)
    display_crop=visible_rectangle(path,crop)
    rgb=crop_linear(rgb,display_crop,first.shape)
    step=max(1,int(max(rgb.shape[:2])/300))
    xyz=rgb[::step,::step]@camera_xyz.T
    ev=auto_exposure_ev(xyz)
    model=BayerModel(black,span,red,green,blue,camera_xyz,flip,ev,crop)
    model.display_crop=display_crop
    model.pixel_aspect=pixel_aspect(path)
    model.meter_xyz=xyz.copy()
    # Avoid LibRaw setup per frame, verifying each new storage layout against it.
    # Sigma sequences can start uncompressed and switch to lossless JPEG.
    try:
        with tifffile.TiffFile(path) as image:
            def find(pages,prefix=()):
                for i,page in enumerate(pages):
                    route=prefix+(i,)
                    if page.photometric==32803 and page.bitspersample in (10,12,14,16) and page.compression in (1,7) and len(page.shape)==2 and page.dtype.kind=='u':
                        top,left,height,width=crop
                        pixels=page.asarray(maxworkers=1)[top:top+height,left:left+width]
                        if np.array_equal(pixels,first):
                            model.direct_bits=page.bitspersample
                            model.direct_compression=int(page.compression)
                            model.verified_layouts[(page.shape,page.bitspersample,int(page.compression),page.fillorder)]=True
                            return route
                    if page.pages:
                        result=find(page.pages,route)
                        if result is not None:
                            return result
                return None
            model.direct_ifd=find(image.pages)
    except (OSError,ValueError,IndexError,TypeError,AttributeError):
        pass
    return first,model


def surface_format():
    fmt=QSurfaceFormat()
    import sys
    fmt.setVersion(4,1) if sys.platform=='darwin' else fmt.setVersion(3,3)
    fmt.setProfile(QSurfaceFormat.CoreProfile)
    return fmt


def create_surface():
    surface=QOffscreenSurface()
    surface.setFormat(surface_format())
    surface.create()
    return surface


VERTEX='''#version 330 core
out vec2 uv;
void main(){vec2 p=vec2((gl_VertexID<<1)&2,gl_VertexID&2); uv=p; gl_Position=vec4(p*2.0-1.0,0,1);}
'''
FRAGMENT='''#version 330 core
in vec2 uv; out vec4 frag;
uniform sampler2D sensor;
uniform usampler2D packedSensor;
uniform int packedBits,cropLeft,cropTop;
uniform vec2 activeSize;
uniform vec4 imageCrop;
uniform vec4 blackLevel,span,redMask,greenMask,blueMask;
uniform vec3 row0,row1,row2;
uniform vec3 display0,display1,display2;
uniform int viewMode,flip;
uniform float gain;
uniform float postGain;
uniform sampler3D outputLut;
uniform int lutSize;
uniform vec3 lutMinimum,lutMaximum;
vec3 lutAt(ivec3 p){return texelFetch(outputLut,p,0).rgb;}
vec3 outputTransform(vec3 inputValue){
 vec3 p=clamp((inputValue-lutMinimum)/(lutMaximum-lutMinimum),0.0,1.0)*float(lutSize-1);
 ivec3 base=min(ivec3(floor(p)),ivec3(lutSize-2));vec3 f=p-vec3(base);
 ivec3 first,second;vec3 weights;
 if(f.r>=f.g){
  if(f.g>=f.b){first=ivec3(1,0,0);second=ivec3(1,1,0);weights=f.rgb;}
  else if(f.r>=f.b){first=ivec3(1,0,0);second=ivec3(1,0,1);weights=f.rbg;}
  else{first=ivec3(0,0,1);second=ivec3(1,0,1);weights=f.brg;}
 }else{
  if(f.r>=f.b){first=ivec3(0,1,0);second=ivec3(1,1,0);weights=f.grb;}
  else if(f.g>=f.b){first=ivec3(0,1,0);second=ivec3(0,1,1);weights=f.gbr;}
  else{first=ivec3(0,0,1);second=ivec3(0,1,1);weights=f.bgr;}
 }
 vec3 a=lutAt(base),b=lutAt(base+first),c=lutAt(base+second),d=lutAt(base+ivec3(1));
 return a+weights.x*(b-a)+weights.y*(c-b)+weights.z*(d-c);
}
float valueAt(ivec2 p){
 if(packedBits==0)return texelFetch(sensor,clamp(p,ivec2(0),textureSize(sensor,0)-1),0).r*65535.0;
 p+=ivec2(cropLeft,cropTop);
 int bit=p.x*packedBits;int b=bit/8;int last=textureSize(packedSensor,0).x-1;
 uint a=texelFetch(packedSensor,ivec2(b,p.y),0).r;
 uint c=texelFetch(packedSensor,ivec2(min(b+1,last),p.y),0).r;
 uint d=texelFetch(packedSensor,ivec2(min(b+2,last),p.y),0).r;
 uint value=((a<<16)|(c<<8)|d)>>uint(24-packedBits-(bit%8));
 return float(value&((1u<<uint(packedBits))-1u));
}
vec3 cameraAt(ivec2 base){
 vec4 v=vec4(valueAt(base),valueAt(base+ivec2(1,0)),valueAt(base+ivec2(0,1)),valueAt(base+ivec2(1,1)));
 v=max((v-blackLevel)/span,vec4(0));
 return vec3(dot(v,redMask),dot(v,greenMask),dot(v,blueMask));
}
void main(){
 vec2 q=vec2(uv.x,1.0-uv.y);
 if(flip==3)q=1.0-q;
 else if(flip==5)q=vec2(1.0-q.y,q.x);
 else if(flip==6)q=vec2(q.y,1.0-q.x);
 ivec2 size=ivec2(activeSize)/2;
 vec2 cell=clamp((imageCrop.xy+q*imageCrop.zw-vec2(1.0))*.5,vec2(0),vec2(size-1));
 ivec2 base=ivec2(floor(cell))*2;vec2 part=fract(cell);
 vec3 rgb=cameraAt(base);
 if(part.x>.0001)rgb=mix(rgb,cameraAt(min(base+ivec2(2,0),(size-1)*2)),part.x);
 if(part.y>.0001){
  vec3 other=cameraAt(min(base+ivec2(0,2),(size-1)*2));
  if(part.x>.0001)other=mix(other,cameraAt(min(base+ivec2(2,2),(size-1)*2)),part.x);
  rgb=mix(rgb,other,part.y);
 }
 vec3 x=vec3(dot(row0,rgb),dot(row1,rgb),dot(row2,rgb))*gain;
 if(viewMode==3){
  float sceneY=max(dot(x,vec3(0.2741184831,0.8736318350,-0.1477504075)),0.0);
  if(sceneY>.35){float d=sceneY-.35;float compressed=.35+.65*d/(d+.65);x*=compressed/sceneY;}
 }
 vec3 outv;
 if(viewMode==0||viewMode==3){bvec3 low=lessThanEqual(x,vec3(.00262409));outv=mix((log2(max(x+.0075,vec3(1e-12)))+7.0)*.07329248,x*10.44426855,low);if(viewMode==3)outv=outputTransform(outv);}
 else if(viewMode==1){bvec3 low=lessThan(x,vec3(.005));outv=mix(.08692876065491224*log(max(x+.005494072432257808,vec3(1e-12)))+.5300133392291939,x*8.283605932402494+.09246575342465753,low);}
 else{
  // Equivalent linear form of DWG/Intermediate -> Rec.709 display transform.
  vec3 c=vec3(dot(display0,x),dot(display1,x),dot(display2,x));
  float y=max(dot(c,vec3(.2126,.7152,.0722)),0.0);
  float d=max(y-.6,0.0);float mapped=y<=.6?y:.6+.4*d/(d+.4);
  vec3 chroma=c*(mapped/max(y,1e-10))-vec3(mapped);
  float pressure=max(max(-min(min(chroma.r,chroma.g),chroma.b)/max(mapped,1e-8),max(max(chroma.r,chroma.g),chroma.b)/max(1.0-mapped,1e-8)),0.0);
  vec3 display=clamp(vec3(mapped)+chroma/sqrt(1.0+pressure*pressure),0.0,1.0);
  outv=pow(display,vec3(1.0/2.4));
 }
 if(viewMode==3){
  vec3 c=pow(clamp(outv,0.0,1.0),vec3(2.4));
  float y=dot(c,vec3(.2126,.7152,.0722));
  float mapped=y*postGain/(1.0+y*(postGain-1.0));
  mapped+=3.0*mapped*pow(max(1.0-mapped,0.0),6.0);
  vec3 chroma=c*(mapped/max(y,1e-10))-vec3(mapped);
  float pressure=max(-min(min(chroma.r,chroma.g),chroma.b)/max(mapped,1e-8),max(max(chroma.r,chroma.g),chroma.b)/max(1.0-mapped,1e-8));
  float compressed=.8+.2*(1.0-exp(-5.0*max(pressure-.8,0.0)));
  float factor=pressure<=.8?1.0:compressed/max(pressure,1e-8);
  outv=pow(clamp(vec3(mapped)+chroma*factor,0.0,1.0),vec3(1.0/2.4));
 }
 frag=vec4(clamp(outv,0.0,1.0),1);
}
'''


class GpuRenderer:
    """Create and use on one render thread; the surface is owned by the UI."""
    def __init__(self,surface):
        self.surface=surface
        self.context=QOpenGLContext()
        self.context.setFormat(surface_format())
        if not self.context.create() or not self.context.makeCurrent(surface):
            raise RuntimeError("OpenGL 3.3 is unavailable")
        self.functions=self.context.functions()
        self.name=str(self.functions.glGetString(0x1F01))
        self.program=QOpenGLShaderProgram()
        if not self.program.addShaderFromSourceCode(QOpenGLShader.Vertex,VERTEX) or not self.program.addShaderFromSourceCode(QOpenGLShader.Fragment,FRAGMENT) or not self.program.link():
            raise RuntimeError(self.program.log())
        self.vao=QOpenGLVertexArrayObject()
        self.vao.create()
        self.texture=None
        self.fbo=None
        self.shape=None
        self.lut_texture=None
        self.lut_view=None

    def render(self,frame,model,view=DAVINCI,auto=True,max_size=(960,640),reduction=2):
        self.context.makeCurrent(self.surface)
        packed=isinstance(frame,PackedBayer)
        data=frame.data if packed else frame
        th,tw=data.shape
        h,w=frame.crop[2:] if packed else frame.shape
        image_crop=model.display_crop or (0,0,h,w)
        ct,cl,ch,cw=image_crop
        shape=(th,tw,packed)
        if self.shape!=shape:
            if self.texture:
                self.texture.destroy()
            self.texture=QOpenGLTexture(QOpenGLTexture.Target2D)
            self.texture.setSize(tw,th)
            self.texture.setFormat(QOpenGLTexture.R8U if packed else QOpenGLTexture.R16_UNorm)
            self.texture.setMinMagFilters(QOpenGLTexture.Nearest,QOpenGLTexture.Nearest)
            self.texture.setWrapMode(QOpenGLTexture.ClampToEdge)
            self.texture.allocateStorage(QOpenGLTexture.Red_Integer if packed else QOpenGLTexture.Red,QOpenGLTexture.UInt8 if packed else QOpenGLTexture.UInt16)
            self.shape=shape
        display_width=max(1,round(cw*model.pixel_aspect))
        outw,outh=(max(1,int(ch)//reduction),max(1,display_width//reduction)) if model.flip in (5,6) else (max(1,display_width//reduction),max(1,int(ch)//reduction))
        scale=min(1,max_size[0]/outw,max_size[1]/outh)
        outw,outh=max(1,round(outw*scale)),max(1,round(outh*scale))
        if self.fbo is None or (self.fbo.width(),self.fbo.height())!=(outw,outh):
            self.fbo=QOpenGLFramebufferObject(outw,outh)
        self.fbo.bind()
        self.functions.glViewport(0,0,outw,outh)
        self.program.bind()
        self.vao.bind()
        self.texture.bind(1 if packed else 0)
        self.functions.glPixelStorei(0x0CF5,1)
        self.texture.setData(QOpenGLTexture.Red_Integer if packed else QOpenGLTexture.Red,QOpenGLTexture.UInt8 if packed else QOpenGLTexture.UInt16,VoidPtr(data.ctypes.data,data.nbytes))
        use_lut=is_resolve_view(view)
        if use_lut:
            cube=view_cube(view)
            if self.lut_view!=view:
                if self.lut_texture:self.lut_texture.destroy()
                self.lut_texture=QOpenGLTexture(QOpenGLTexture.Target3D)
                self.lut_texture.setSize(cube.size,cube.size,cube.size)
                self.lut_texture.setFormat(QOpenGLTexture.RGB32F)
                self.lut_texture.setMinMagFilters(QOpenGLTexture.Nearest,QOpenGLTexture.Nearest)
                self.lut_texture.setWrapMode(QOpenGLTexture.ClampToEdge)
                self.lut_texture.allocateStorage(QOpenGLTexture.RGB,QOpenGLTexture.Float32)
                self.lut_texture.bind(2)
                self.lut_texture.setData(QOpenGLTexture.RGB,QOpenGLTexture.Float32,VoidPtr(cube.table.ctypes.data,cube.table.nbytes))
                self.lut_view=view
            self.lut_texture.bind(2)
            self.functions.glUniform1i(self.program.uniformLocation('outputLut'),2)
            self.functions.glUniform1i(self.program.uniformLocation('lutSize'),cube.size)
            for key,values in (('lutMinimum',cube.minimum),('lutMaximum',cube.maximum)):
                self.functions.glUniform3f(self.program.uniformLocation(key),*map(float,values))
        else:
            # Distinct sampler types may not share the same texture unit.
            self.functions.glUniform1i(self.program.uniformLocation('outputLut'),2)
        self.functions.glUniform1i(self.program.uniformLocation("sensor"),0)
        self.functions.glUniform1i(self.program.uniformLocation("packedSensor"),1)
        self.functions.glUniform1i(self.program.uniformLocation("packedBits"),frame.bits if packed else 0)
        self.functions.glUniform1i(self.program.uniformLocation("cropTop"),frame.crop[0] if packed else 0)
        self.functions.glUniform1i(self.program.uniformLocation("cropLeft"),frame.crop[1] if packed else 0)
        self.functions.glUniform2f(self.program.uniformLocation("activeSize"),w,h)
        self.functions.glUniform4f(self.program.uniformLocation('imageCrop'),float(cl),float(ct),float(cw),float(ch))
        for key,data in (("blackLevel",model.black),("span",model.span),("redMask",model.red),("greenMask",model.green),("blueMask",model.blue)):
            self.functions.glUniform4f(self.program.uniformLocation(key),*map(float,data))
        transform=XYZ_TO_BMD if view==BLACKMAGIC else XYZ_TO_DWG
        matrix=transform@model.camera_xyz
        for i,row in enumerate(matrix):
            self.functions.glUniform3f(self.program.uniformLocation("row"+str(i)),*map(float,row))
        for i,row in enumerate(DWG_TO_REC709):
            self.functions.glUniform3f(self.program.uniformLocation("display"+str(i)),*map(float,row))
        self.functions.glUniform1i(self.program.uniformLocation("viewMode"),3 if use_lut else 0 if view==DAVINCI else 1 if view==BLACKMAGIC else 2)
        self.functions.glUniform1i(self.program.uniformLocation("flip"),model.flip)
        self.functions.glUniform1f(self.program.uniformLocation("gain"),float(2.0**model.ev if auto and not use_lut else 1.0))
        if use_lut and auto and view not in model.output_exposures:
            model.output_exposures[view]=meter_exposure_ev(model.meter_xyz,view) if model.meter_xyz is not None else 0.0
        self.functions.glUniform1f(self.program.uniformLocation('postGain'),float(2**model.output_exposures[view] if use_lut and auto else 1.0))
        self.functions.glDrawArrays(0x0004,0,3)
        self.last_error=self.functions.glGetError()
        self.vao.release()
        self.program.release()
        image=self.fbo.toImage()
        self.fbo.release()
        return image

    def close(self):
        self.context.makeCurrent(self.surface)
        if self.texture:
            self.texture.destroy()
        if self.lut_texture:self.lut_texture.destroy()
        self.fbo=None
        self.vao.destroy()
        self.program.removeAllShaders()
        self.program=None
        self.context.doneCurrent()
