"""Visual-only transforms, from linear XYZ (D65) into explicit preview encodings.

DaVinci matrices / OETF: Blackmagic's Wide Gamut Intermediate white paper v1.1.
Blackmagic Gen 5 constants / matrix: Colour Science's BSD-3-Clause reference.
These are preview encodings, not a claim of Resolve's proprietary RAW rendering.
"""
import numpy as np
import sys
from pathlib import Path
from display_lut import is_resolve_view,view_cube

DAVINCI="DaVinci WG / Intermediate"
BLACKMAGIC="Blackmagic Wide Gamut / Film Gen 5"
REC709="Rec.709 / Gamma 2.4 — tone mapped"
SRGB=REC709  # Source compatibility; the old display view migrates to Rec.709.
VIEWS=(DAVINCI,BLACKMAGIC,REC709)
XYZ_TO_DWG=np.array([[1.51667204,-.28147805,-.14696363],[-.46491710,1.25142378,.17488461],
                     [.06484905,.10913934,.76141462]],dtype=np.float32)
XYZ_TO_BMD=np.array([[1.86638234,-.51839734,-.23460981],[-.60034249,1.37814896,.17673183],
                     [.00245199,.08639967,.83694271]],dtype=np.float32)
XYZ_TO_SRGB=np.array([[3.2404542,-1.5371385,-.4985314],[-.9692660,1.8760108,.0415560],
                      [.0556434,-.2040259,1.0572252]],dtype=np.float32)
DWG_TO_REC709=XYZ_TO_SRGB@np.linalg.inv(XYZ_TO_DWG)


def default_output_view():
    from display_lut import resolve_view
    asset=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent))/'assets/resolve-dwg-to-rec709.cube'
    return resolve_view(asset)


def preview_view(view):
    """Preserve the fixed working-space preview unless a valid output LUT is set."""
    if is_resolve_view(view):
        try:
            view_cube(view)
            return view
        except (OSError,ValueError):pass
    return DAVINCI


def rec709_display_linear(rgb):
    """Preview shoulder and smooth neutral-axis gamut compression, before gamma."""
    rgb=np.asarray(rgb,dtype=np.float32)
    y=np.maximum(rgb@np.array([.2126,.7152,.0722],np.float32),0)
    d=np.maximum(y-.6,0)
    mapped=np.where(y<=.6,y,.6+.4*d/(d+.4))
    toned=rgb*(mapped/np.maximum(y,1e-10))[...,None]
    chroma=toned-mapped[...,None]
    pressure=np.maximum(np.maximum(-np.min(chroma,axis=-1)/np.maximum(mapped,1e-8),
                                   np.max(chroma,axis=-1)/np.maximum(1-mapped,1e-8)),0)
    return np.clip(mapped[...,None]+chroma/np.sqrt(1+pressure*pressure)[...,None],0,1)


def dwg_intermediate_to_rec709(encoded):
    linear=intermediate_decode(encoded)@DWG_TO_REC709.T
    return np.power(rec709_display_linear(linear),1/2.4)


def intermediate_encode(linear):
    x=np.asarray(linear,dtype=np.float32)
    return np.where(x<=.00262409,x*10.44426855,
                    (np.log2(np.maximum(x+.0075,1e-12))+7)*.07329248)


def intermediate_decode(encoded):
    x=np.asarray(encoded,dtype=np.float32)
    return np.where(x<=.02740668,x/10.44426855,np.exp2(x/.07329248-7)-.0075)


def film5_encode(linear):
    x=np.asarray(linear,dtype=np.float32)
    return np.where(x<.005,8.283605932402494*x+.09246575342465753,
                    .08692876065491224*np.log(np.maximum(x+.005494072432257808,1e-12))+.5300133392291939)


def auto_exposure_ev(xyz):
    """Meter the first frame once. Never re-meter every playback frame."""
    step=max(1,int(max(xyz.shape[:2])/320))
    luminance=np.asarray(xyz)[::step,::step,1]
    valid=luminance[np.isfinite(luminance)&(luminance>1e-5)]
    if valid.size<16:
        return 0.0
    middle=float(np.percentile(valid,55))
    if middle<1e-4:
        return 0.0
    return float(np.clip(np.log2(.18/middle),-2,4))



def pre_lut_highlights(xyz):
    """Reduce scene-linear luminance before DWG encoding and the output LUT."""
    xyz=np.asarray(xyz,dtype=np.float32)
    y=np.maximum(xyz[...,1],0)
    d=np.maximum(y-.35,0)
    mapped=np.where(y<=.35,y,.35+.65*d/(d+.65))
    scale=np.where(y>.35,mapped/np.maximum(y,1e-10),1)
    return xyz*scale[...,None]


def meter_exposure_ev(xyz,view):
    if not is_resolve_view(view):return auto_exposure_ev(xyz)
    step=max(1,int(max(xyz.shape[:2])/300))
    encoded=view_cube(view).apply(intermediate_encode(pre_lut_highlights(np.asarray(xyz)[::step,::step])@XYZ_TO_DWG.T))
    return ettr_exposure_ev(encoded)


def ettr_exposure_ev(encoded):
    """ETTR for the brightest meaningful image area, with highlight headroom."""
    samples=np.asarray(encoded,dtype=np.float32).reshape(-1,3)
    samples=samples[np.all(np.isfinite(samples),axis=1)&(np.max(samples,axis=1)>1e-8)]
    if not len(samples):return 0.0
    # Already clipped LUT output cannot be restored by a later exposure curve.
    # Meter recoverable samples; the pre-LUT shoulder handles scene highlights.
    samples=samples[np.max(samples,axis=1)<.999999]
    if not len(samples):return 0.0
    low,high=-14.,24.
    for _ in range(24):
        ev=(low+high)*.5
        if np.percentile(np.max(post_lut_exposure(samples,ev),axis=1),99.5)<=.98:low=ev
        else:high=ev
    return low


def post_lut_exposure(encoded,ev):
    rgb=np.clip(np.asarray(encoded,dtype=np.float32),0,1)**2.4
    y=rgb@np.array([.2126,.7152,.0722],np.float32)
    gain=np.float32(2**ev)
    mapped=y*gain/(1+y*(gain-1))
    # Lift deep shadows by up to two stops, fading smoothly out of highlights.
    mapped=mapped+3*mapped*np.maximum(1-mapped,0)**6
    toned=rgb*(mapped/np.maximum(y,1e-10))[...,None]
    chroma=toned-mapped[...,None]
    pressure=np.maximum(-np.min(chroma,axis=-1)/np.maximum(mapped,1e-8),
                        np.max(chroma,axis=-1)/np.maximum(1-mapped,1e-8))
    compressed=.8+.2*(1-np.exp(-5*np.maximum(pressure-.8,0)))
    factor=np.where(pressure<=.8,1,compressed/np.maximum(pressure,1e-8))
    return np.power(np.clip(mapped[...,None]+chroma*factor[...,None],0,1),1/2.4)


def render_xyz(xyz,view=DAVINCI,ev=0.0):
    xyz=np.asarray(xyz,dtype=np.float32)*np.float32(1 if is_resolve_view(view) else 2.0**ev)
    if is_resolve_view(view):
        encoded=post_lut_exposure(view_cube(view).apply(intermediate_encode(pre_lut_highlights(xyz)@XYZ_TO_DWG.T)),ev)
    elif view==DAVINCI:
        rgb=xyz@XYZ_TO_DWG.T
        encoded=intermediate_encode(rgb)
    elif view==BLACKMAGIC:
        rgb=xyz@XYZ_TO_BMD.T
        encoded=film5_encode(rgb)
    elif view==REC709:
        encoded=dwg_intermediate_to_rec709(intermediate_encode(xyz@XYZ_TO_DWG.T))
    else:
        raise ValueError("Unknown preview encoding: "+str(view))
    return np.ascontiguousarray(np.round(np.clip(encoded,0,1)*255).astype(np.uint8))

