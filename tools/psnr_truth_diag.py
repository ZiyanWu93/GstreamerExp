"""Diagnose the decoded_psnr truth-pipeline misalignment.

Decodes realmotion.avi two ways and compares the resulting I420 frames:

  TRUTH path  (what the metric's truth pipeline does):
      filesrc -> decodebin -> videoconvert -> videoscale -> videorate
      -> caps(I420, WxH, fps) -> appsink

  CAMERA path (what the camera encodes from, minus vp8):
      filesrc -> avidemux -> jpegparse -> jpegdec -> videoconvert
      -> videoscale -> videorate -> caps(I420, WxH, fps) -> appsink

If TRUTH[i] vs CAMERA[i] PSNR is very high (>50 dB) the two decode paths
agree, so the live 10 dB problem is in the network-side PTS->index
matching. If it is ~10-20 dB, the two decode paths themselves disagree
(colorimetry / scaler / decoder mismatch) and that is the baseline bug.

Also dumps frame 0/1/2 Y planes from each path as PNG for eyeballing,
and reports how many frames each path produced (cadence check).

Run on aum:  python3 psnr_truth_diag.py /home/ziyan/teleop_clips/realmotion.avi 1280 1024 10
"""
import sys
import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst
import numpy as np

Gst.init(None)

PATH = sys.argv[1]
W = int(sys.argv[2])
H = int(sys.argv[3])
FPS = int(sys.argv[4])
OUT = "/tmp/psnr_diag"
import os
os.makedirs(OUT, exist_ok=True)


def collect(pipeline_desc, label, use_decodebin):
    """Build pipeline, run to EOS, return list of Y-plane np arrays."""
    frames = []

    pipe = Gst.parse_launch(pipeline_desc)
    sink = pipe.get_by_name("s")

    def on_sample(s):
        sample = s.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.OK
        buf = sample.get_buffer()
        ok, info = buf.map(Gst.MapFlags.READ)
        if not ok:
            return Gst.FlowReturn.OK
        try:
            y = np.frombuffer(bytes(info.data[: W * H]), dtype=np.uint8).reshape(H, W)
            frames.append(y.copy())
        finally:
            buf.unmap(info)
        return Gst.FlowReturn.OK

    sink.set_property("emit-signals", True)
    sink.set_property("sync", False)
    sink.connect("new-sample", on_sample)

    pipe.set_state(Gst.State.PLAYING)
    bus = pipe.get_bus()
    msg = bus.timed_pop_filtered(60 * Gst.SECOND,
                                 Gst.MessageType.EOS | Gst.MessageType.ERROR)
    if msg and msg.type == Gst.MessageType.ERROR:
        err, dbg = msg.parse_error()
        print(f"[{label}] ERROR: {err.message} ({dbg})")
    pipe.set_state(Gst.State.NULL)
    print(f"[{label}] collected {len(frames)} frames")
    return frames


def psnr(a, b):
    mse = np.mean((a.astype(np.float64) - b.astype(np.float64)) ** 2)
    if mse == 0:
        return float("inf")
    return 10.0 * np.log10((255.0 ** 2) / mse)


def save_png(y, path):
    try:
        from PIL import Image
        Image.fromarray(y, mode="L").save(path)
    except Exception as e:
        # Fall back to raw if PIL missing.
        y.tofile(path + ".raw")
        print(f"  (PIL unavailable: {e}; wrote {path}.raw)")


caps = f"video/x-raw,format=I420,width={W},height={H},framerate={FPS}/1"

truth_desc = (
    f'filesrc location="{PATH}" ! decodebin ! videoconvert ! videoscale ! '
    f'videorate ! capsfilter caps="{caps}" ! appsink name=s'
)
camera_desc = (
    f'filesrc location="{PATH}" ! avidemux ! jpegparse ! jpegdec ! '
    f'videoconvert ! videoscale ! videorate ! capsfilter caps="{caps}" ! '
    f'appsink name=s'
)

print("=== TRUTH path (decodebin) ===")
truth = collect(truth_desc, "truth", True)
print("=== CAMERA path (avidemux->jpegparse->jpegdec) ===")
camera = collect(camera_desc, "camera", False)

n = min(len(truth), len(camera))
print(f"\n=== frame-by-frame PSNR (truth[i] vs camera[i]), first 6 of {n} ===")
for i in range(min(6, n)):
    print(f"  frame {i}: {psnr(truth[i], camera[i]):.2f} dB")

# Also check truth[0] vs camera[1] and camera[0] vs truth[1] to detect an
# off-by-one between the two decode paths.
if n >= 2:
    print("\n=== off-by-one probes ===")
    print(f"  truth[0] vs camera[1]: {psnr(truth[0], camera[1]):.2f} dB")
    print(f"  truth[1] vs camera[0]: {psnr(truth[1], camera[0]):.2f} dB")

for i in range(min(3, n)):
    save_png(truth[i], f"{OUT}/truth_{i}.png")
    save_png(camera[i], f"{OUT}/camera_{i}.png")
print(f"\nPNGs under {OUT}/")
