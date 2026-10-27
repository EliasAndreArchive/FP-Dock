# Preview color pipeline

All operations in this document are visual-only. The transfer code copies and verifies original bytes; it does not call the color pipeline to create altered DNG files.

## 1. RAW interpretation

`raw_library.py`, `live_gpu.py`, and `dng_geometry.py` read camera metadata rather than assuming every take has the same dimensions. Active area, default crop, pixel aspect, orientation, black level, white level, and frame rate are interpreted per clip.

The CPU reference uses LibRaw through rawpy with camera white balance, linear output, and automatic brightness disabled. The GPU path calibrates its camera-to-XYZ conversion against that reference. Unsupported or unverified layouts fall back to CPU decoding. Sigma fp sequences may start with a packed uncompressed frame and switch to lossless JPEG DNG; both layouts are verified before the fast path is used.

Lossless JPEG decompression is performed on CPU with imagecodecs. GPU use does not mean GPU decompression of the lossless DNG bitstream.

## 2. Pre-LUT highlight reduction

In scene-linear XYZ, let `Y` be nonnegative luminance. Values at or below `0.35` are left unchanged. Above that knee:

```text
d = Y - 0.35
mapped_Y = 0.35 + 0.65 * d / (d + 0.65)
```

XYZ is scaled by `mapped_Y / Y`, preserving its chromaticity. This smooth monotonic shoulder brings bright scene values down **before** the output LUT. It is not a clone of Resolve's or Lightroom's Highlights slider and cannot reconstruct detail lost at sensor saturation.

## 3. Working space and output LUT

The image is converted from XYZ into **DaVinci Wide Gamut**, then encoded as **DaVinci Intermediate**. The bundled `source/assets/resolve-dwg-to-rec709.cube` is a 65³ LUT expecting that DWG/Intermediate input. Both CPU and GPU use tetrahedral interpolation.

The supplied Resolve export contains the final CST to **Rec.709 Gamma 2.4**, including **DaVinci tone mapping**, **saturation compression**, and **Forward OOTF**. These settings describe the supplied LUT; FP Dock does not independently reimplement the proprietary DaVinci tone-mapping algorithm or apply that OOTF a second time.

This output LUT is not a Blackmagic Cinema Camera Film → Rec.709 LUT. Substituting a LUT with a different expected input would produce incorrect contrast and colors. The current RAW-to-working-space path also does not claim to recreate Resolve's Blackmagic Film camera-RAW controls.

## 4. Post-LUT ETTR and shadow lift

The LUT output is converted to display-linear light using a power of 2.4. With luminance `y` and exposure gain `g = 2^EV`, exposure uses a bounded curve:

```text
exposed_y = y * g / (1 + y * (g - 1))
lifted_y = exposed_y + 3 * exposed_y * (1 - exposed_y)^6
```

The second expression lifts deep shadows by up to about two stops, fades out toward highlights, and keeps black and white anchored. Chroma is scaled with luminance and smoothly compressed toward the neutral axis to keep the result in gamut. The final image is encoded back to Gamma 2.4.

To choose EV, the initial frame is sampled after the pre-LUT shoulder and LUT. A bounded search evaluates the same exposure/shadow operation and seeks a maximum-RGB **99.5th-percentile** value of about **0.98 in encoded display values**. This ignores isolated speculars while reserving headroom for the main bright image area. Pixels already clipped by the LUT are excluded from this metering; a post-LUT curve cannot recover them. EV remains fixed for that clip to prevent temporal pumping.

This is a display ETTR aid, not scene-referred camera exposure control. It does not guarantee that every hot pixel or reflection is below 0.98, that a later frame has the same brightness distribution, or that the grade matches Resolve exactly.

## Precision and display

Original DNG samples retain their recorded bit depth. Computation uses floating point; the current QImage display surface is 8-bit per color channel. This may show more banding than a dedicated 10/12-bit display pipeline. Display calibration, ICC handling, HDR, and OS compositing can also affect appearance. FP Dock does not currently provide a managed HDR or professional reference-monitor output path.

The UI theme never recolors video pixels. CPU/GPU tests compare final display images, so a difference of one “code value” refers to that final 8-bit image, not to quantization of the stored RAW file.

Implementation: [`preview_color.py`](../source/preview_color.py), [`display_lut.py`](../source/display_lut.py), [`live_gpu.py`](../source/live_gpu.py).
