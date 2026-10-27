"""DNG image aperture. Crop metadata affects display only, never source bytes."""
import math
import numpy as np
import tifffile


def _values(tag,count):
    if tag is None:return None
    values=tag.value
    if not isinstance(values,(tuple,list,np.ndarray)):values=(values,)
    if int(tag.dtype) in (5,10) and len(values)==count*2:
        values=[float(values[i])/float(values[i+1]) for i in range(0,len(values),2)]
    else:
        values=[float(v[0])/float(v[1]) if isinstance(v,(tuple,list)) and len(v)==2 else float(v) for v in values]
    return values if len(values)==count and all(math.isfinite(v) for v in values) else None


def image_rectangle(path):
    """Return absolute sensor coordinates (top, left, height, width)."""
    with tifffile.TiffFile(path) as image:
        main=image.pages[0]
        def walk(pages):
            for page in pages:
                yield page
                if page.pages:yield from walk(page.pages)
        page=next((p for p in walk(image.pages) if int(p.photometric or 0) in (32803,34892)),main)
        height,width=page.imagelength,page.imagewidth
        def values(code,n):
            try:return _values(page.tags.get(code) or main.tags.get(code),n)
            except (ValueError,TypeError,ZeroDivisionError):return None
        area=values(50829,4) or [0,0,height,width]
        top,left,bottom,right=area
        if not (0<=top<bottom<=height and 0<=left<right<=width):top,left,bottom,right=0,0,height,width
        origin=values(50719,2) or [0,0]
        size=values(50720,2) or [right-left,bottom-top]
        x,y=origin;w,h=size
        if x>=0 and y>=0 and w>0 and h>0 and x+w<=right-left+.001 and y+h<=bottom-top+.001:
            return top+y,left+x,h,w
        return top,left,bottom-top,right-left


def visible_rectangle(path,visible):
    """Translate the metadata crop into LibRaw's visible-array coordinates."""
    vt,vl,vh,vw=visible
    try:top,left,height,width=image_rectangle(path)
    except (OSError,ValueError,TypeError):return (0,0,vh,vw)
    y0=max(vt,top);x0=max(vl,left);y1=min(vt+vh,top+height);x1=min(vl+vw,left+width)
    if y1<=y0 or x1<=x0:return (0,0,vh,vw)
    return y0-vt,x0-vl,y1-y0,x1-x0


def pixel_aspect(path):
    """DNG DefaultScale defines the display size of non-square sensor pixels."""
    try:
        with tifffile.TiffFile(path) as image:
            main=image.pages[0]
            def walk(pages):
                for p in pages:
                    yield p
                    if p.pages:yield from walk(p.pages)
            page=next((p for p in walk(image.pages) if int(p.photometric or 0) in (32803,34892)),main)
            scale=_values(page.tags.get(50718) or main.tags.get(50718),2)
            if scale and scale[0]>0 and scale[1]>0 and .1<=scale[0]/scale[1]<=10:
                return scale[0]/scale[1]
    except (OSError,ValueError,TypeError,ZeroDivisionError):pass
    return 1.0


def crop_linear(image,rectangle,visible_shape,max_size=None):
    """Crop after demosaic, retaining odd/subpixel offsets without shifting Bayer phase."""
    top,left,height,width=rectangle
    vh,vw=visible_shape
    sy,sx=image.shape[0]/vh,image.shape[1]/vw
    outw=max(1,round(width*sx));outh=max(1,round(height*sy))
    if max_size:
        scale=min(1,max_size[0]/outw,max_size[1]/outh)
        outw=max(1,round(outw*scale));outh=max(1,round(outh*scale))
    startx,starty=left*sx,top*sy
    if abs(startx-round(startx))<1e-6 and abs(starty-round(starty))<1e-6 and abs(outw-width*sx)<1e-6 and abs(outh-height*sy)<1e-6:
        x,y=round(startx),round(starty)
        return np.ascontiguousarray(image[y:y+outh,x:x+outw])
    xs=startx+(np.arange(outw)+.5)*(width*sx/outw)-.5
    ys=starty+(np.arange(outh)+.5)*(height*sy/outh)-.5
    xs=np.clip(xs,0,image.shape[1]-1);ys=np.clip(ys,0,image.shape[0]-1)
    x0=xs.astype(int);y0=ys.astype(int);x1=np.minimum(x0+1,image.shape[1]-1);y1=np.minimum(y0+1,image.shape[0]-1)
    dx=(xs-x0).astype(np.float32)[None,:,None];dy=(ys-y0).astype(np.float32)[:,None,None]
    a=image[y0[:,None],x0[None,:]]*(1-dx)+image[y0[:,None],x1[None,:]]*dx
    b=image[y1[:,None],x0[None,:]]*(1-dx)+image[y1[:,None],x1[None,:]]*dx
    return np.ascontiguousarray(a*(1-dy)+b*dy)
