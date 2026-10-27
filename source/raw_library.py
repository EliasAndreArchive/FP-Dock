"""Sequence discovery and RAW decoding. Source files are always opened read-only."""
from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

# Each worker decodes one frame; avoid a second, oversized OpenMP thread pool.
os.environ.setdefault("OMP_NUM_THREADS", "1")
import numpy as np
import rawpy
import tifffile
from preview_color import DAVINCI, auto_exposure_ev, render_xyz,meter_exposure_ev
from dng_geometry import image_rectangle,visible_rectangle,crop_linear


def natural_key(value):
    return tuple((1, int(p)) if p.isdigit() else (0, p.casefold())
                 for p in re.split(r"(\d+)", str(value)))


@dataclass
class Clip:
    folder: Path
    name: str
    frames: list[Path]
    fps: float = 24.0
    fps_from_file: bool = False
    width: int = 0
    height: int = 0
    bits: int = 0
    camera: str = "CinemaDNG"
    gaps: int = 0
    audio: Path | None = None
    metadata_loaded: bool = False
    error: str = ""

    @property
    def key(self):
        return str(self.frames[0])

    @property
    def duration(self):
        return len(self.frames) / self.fps


def scan_folder(root: Path, cancelled: threading.Event | None = None):
    clips, errors = [], []
    def onerror(error):
        errors.append(str(error))
    for directory, dirs, names in os.walk(root, onerror=onerror, followlinks=False):
        if cancelled and cancelled.is_set():
            return [], errors
        dirs[:] = sorted(dirs, key=natural_key)
        groups = {}
        for name in names:
            if Path(name).suffix.lower() != ".dng":
                continue
            stem = Path(name).stem
            match = re.match(r"^(.*?)(\d+)$", stem)
            # Distinct numbered prefixes in the same directory remain distinct clips.
            group = ("sequence", match[1]) if match else ("still", stem)
            groups.setdefault(group, []).append(Path(directory) / name)
        for (kind, prefix), frames in sorted(groups.items(), key=lambda x: natural_key(x[0][1])):
            frames.sort(key=lambda p: natural_key(p.name))
            parent = Path(directory)
            title = parent.name if len(groups) == 1 else (prefix.rstrip("_- .") or parent.name)
            if kind == "still":
                title = prefix
            nums = [int(re.search(r"(\d+)$", p.stem)[1]) for p in frames] if kind == "sequence" else []
            gaps = sum(max(0, b-a-1) for a, b in zip(nums, nums[1:]))
            clip = Clip(parent, title, frames, gaps=gaps)
            wavs = sorted((parent / n for n in names if Path(n).suffix.lower() == ".wav"), key=natural_key)
            # Avoid assigning an unrelated soundtrack in directories with several clips.
            candidates = {title.casefold(), prefix.rstrip("_- .").casefold(), frames[0].stem.casefold()}
            clip.audio = next((p for p in wavs if p.stem.casefold() in candidates), None)
            if clip.audio is None and len(wavs) == 1 and len(groups) == 1:
                clip.audio = wavs[0]
            clips.append(clip)
    clips.sort(key=lambda c: natural_key(str(c.folder / c.name)))
    return clips, errors


def load_metadata(clip: Clip):
    if clip.metadata_loaded:
        return clip
    try:
        with tifffile.TiffFile(clip.frames[0]) as f:
            main = f.pages[0]
            def walk_pages(pages):
                for p in pages:
                    yield p
                    if p.pages is not None:
                        yield from walk_pages(p.pages)
            # Many cameras put a JPEG thumbnail in IFD0 and the sensor in a SubIFD.
            page = next((p for p in walk_pages(f.pages) if int(p.photometric or 0) in (32803, 34892)), main)
            tags = page.tags
            def value(code, default=None):
                tag = tags.get(code) or main.tags.get(code)
                return tag.value if tag else default
            clip.width = int(value(256, 0))
            clip.height = int(value(257, 0))
            try:
                _,_,image_height,image_width=image_rectangle(clip.frames[0])
                clip.width,clip.height=round(image_width),round(image_height)
            except (OSError,ValueError,TypeError):pass
            bits = value(258, 0)
            clip.bits = int(bits[0] if isinstance(bits, tuple) else bits)
            clip.camera = str(value(50708, value(272, "CinemaDNG"))).strip("\x00")
            rate = value(51044)
            if rate is not None:
                rate = float(rate[0]) / float(rate[1]) if isinstance(rate, tuple) else float(rate)
                if 0.1 <= rate <= 1000:
                    clip.fps, clip.fps_from_file = rate, True
    except Exception as exc:
        clip.error = str(exc)
    clip.metadata_loaded = True
    return clip


def decode_linear_xyz(path: Path, quality="Balanced", white_balance="Camera"):
    with rawpy.imread(str(path)) as raw:
        visible=(raw.sizes.top_margin,raw.sizes.left_margin,raw.sizes.height,raw.sizes.width)
        rectangle=visible_rectangle(path,visible)
        flip=int(raw.sizes.flip)
        rgb = raw.postprocess(
            half_size=quality != "Full resolution",
            demosaic_algorithm=rawpy.DemosaicAlgorithm.LINEAR,
            use_camera_wb=white_balance == "Camera",
            use_auto_wb=white_balance == "Auto",
            no_auto_bright=True,
            # Keep camera-WB/XYZ highlights above diffuse white available for
            # the output LUT rather than clipping them in LibRaw's 16-bit output.
            bright=1.0/16,
            adjust_maximum_thr=0,
            gamma=(1,1),
            output_bps=16,
            output_color=rawpy.ColorSpace.XYZ,
            user_flip=0,
        )
    xyz=rgb.astype(np.float32)*(16.0/65535.0)
    xyz=crop_linear(xyz,rectangle,visible[2:],(1280,720) if quality=='Fast' else None)
    # LibRaw already applies DefaultScale. crop_linear retains its output aspect;
    # applying the pixel ratio again would stretch non-square footage twice.
    if flip in (3,5,6):xyz=np.rot90(xyz,{3:2,5:1,6:3}[flip])
    return np.ascontiguousarray(xyz)


def develop_frame(path,quality="Balanced",view=DAVINCI,auto_exposure=True,auto_ev=None,exposure=0.0,white_balance="Camera",preview_size=None,preview_reduction=2):
    xyz=decode_linear_xyz(path,quality,white_balance)
    if preview_size:
        preview_size=(min(preview_size[0],max(1,xyz.shape[1]*2//preview_reduction)),
                      min(preview_size[1],max(1,xyz.shape[0]*2//preview_reduction)))
    if quality=="Fast" and (xyz.shape[1]>1280 or xyz.shape[0]>720):
        scale=min(1280/xyz.shape[1],720/xyz.shape[0])
        ys=np.linspace(0,xyz.shape[0]-1,max(2,round(xyz.shape[0]*scale))).astype(int)
        xs=np.linspace(0,xyz.shape[1]-1,max(2,round(xyz.shape[1]*scale))).astype(int)
        xyz=xyz[ys[:,None],xs[None,:]]
    ev=(meter_exposure_ev(xyz,view) if auto_ev is None else float(auto_ev)) if auto_exposure else 0.0
    if preview_size and (xyz.shape[1]>preview_size[0] or xyz.shape[0]>preview_size[1]):
        ratio=min(preview_size[0]/xyz.shape[1],preview_size[1]/xyz.shape[0])
        ys=np.linspace(0,xyz.shape[0]-1,max(1,round(xyz.shape[0]*ratio))).astype(int)
        xs=np.linspace(0,xyz.shape[1]-1,max(1,round(xyz.shape[1]*ratio))).astype(int)
        xyz=xyz[ys[:,None],xs[None,:]]
    return render_xyz(xyz,view,ev+exposure),ev


def decode_frame(path: Path, quality="Balanced", exposure=0.0, white_balance="Camera", *, view=DAVINCI,auto_exposure=True,auto_ev=None):
    return develop_frame(path,quality,view,auto_exposure,auto_ev,exposure,white_balance)[0]
