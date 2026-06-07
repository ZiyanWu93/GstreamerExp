"""Isolate the second PSNR bug: does vp8 enc/dec roundtrip preserve frames,
and is the Y plane being read with the right stride?

Compares the pre-encode I420 frame to the same frame after vp8enc(20Mbps)
-> vp8dec, reading the Y plane two ways: naive (first W*H bytes) and
stride-aware (via GstVideoMeta). If naive is ~10 dB but stride-aware is
~40 dB, the metric's naive [:W*H] read is the bug.

Run on aum: python3 psnr_roundtrip_diag.py
"""
import gi
gi.require_version("Gst", "1.0")
gi.require_version("GstVideo", "1.0")
from gi.repository import Gst, GstVideo
import numpy as np

Gst.init(None)
W, H, FPS = 1280, 1024, 10
PATH = "/home/ziyan/teleop_clips/realmotion.avi"
CAPS = f"video/x-raw,format=I420,width={W},height={H},framerate={FPS}/1"


def collect(desc, label):
    frames = []
    pipe = Gst.parse_launch(desc)
    sink = pipe.get_by_name("s")

    def on(s):
        smp = s.emit("pull-sample")
        if smp is None:
            return Gst.FlowReturn.OK
        buf = smp.get_buffer()
        vm = GstVideo.buffer_get_video_meta(buf)
        stride = vm.stride[0] if vm else W
        ok, info = buf.map(Gst.MapFlags.READ)
        if ok:
            raw = bytes(info.data)
            naive = np.frombuffer(raw[: W * H], dtype=np.uint8).reshape(H, W)
            if stride != W and len(raw) >= stride * H:
                sa = np.frombuffer(raw[: stride * H], dtype=np.uint8).reshape(H, stride)[:, :W]
            else:
                sa = naive
            frames.append((naive.copy(), sa.copy(), stride))
            buf.unmap(info)
        return Gst.FlowReturn.OK

    sink.set_property("emit-signals", True)
    sink.set_property("sync", False)
    sink.connect("new-sample", on)
    pipe.set_state(Gst.State.PLAYING)
    pipe.get_bus().timed_pop_filtered(
        60 * Gst.SECOND, Gst.MessageType.EOS | Gst.MessageType.ERROR)
    pipe.set_state(Gst.State.NULL)
    print(f"[{label}] {len(frames)} frames, stride[0]={frames[0][2] if frames else None}")
    return frames


def psnr(a, b):
    m = np.mean((a.astype(np.float64) - b.astype(np.float64)) ** 2)
    return float("inf") if m == 0 else 10 * np.log10(255 ** 2 / m)


src = collect(
    f'filesrc location="{PATH}" ! avidemux ! jpegparse ! jpegdec ! '
    f'videoconvert ! videoscale ! videorate ! capsfilter caps="{CAPS}" ! '
    f'appsink name=s', "src")
rt = collect(
    f'filesrc location="{PATH}" ! avidemux ! jpegparse ! jpegdec ! '
    f'videoconvert ! videoscale ! videorate ! capsfilter caps="{CAPS}" ! '
    f'vp8enc target-bitrate=20000000 ! vp8dec ! videoconvert ! '
    f'capsfilter caps="{CAPS}" ! appsink name=s', "roundtrip")

for i in (0, 3, 10):
    if i < min(len(src), len(rt)):
        print(f"frame{i} naive-read  PSNR: {psnr(src[i][0], rt[i][0]):.2f} dB")
        print(f"frame{i} stride-read PSNR: {psnr(src[i][1], rt[i][1]):.2f} dB")
