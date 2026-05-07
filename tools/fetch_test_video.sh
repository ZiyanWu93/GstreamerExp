#!/bin/bash
# One-time generation of a small test clip for the FileSource smoke.
# Run on the camera actor's host. Idempotent — skips if already present.
#
# Uses the locally-built GStreamer 1.24 (sourced via ~/gst-1.24/env.sh)
# to encode a videotestsrc `ball` pattern into a VP8/WebM file. We
# generate rather than download because the locally-built GStreamer on
# the camera host doesn't include H.264 decoding (no gst-libav / no
# H.264 in gst-plugins-bad), and VP8 is what every config in this
# project already depends on (vp8enc/vp8dec).
#
# This is a smoke fixture only — the content is still synthetic (no
# real motion variance / scene cuts). Real-research clips need to be
# placed at the path manually after re-encoding to VP8/WebM with the
# user's preferred ffmpeg/handbrake recipe.

set -e

DEST_DIR="$HOME/teleop_clips"
DEST_PATH="$DEST_DIR/realmotion.webm"

mkdir -p "$DEST_DIR"

if [ -s "$DEST_PATH" ]; then
    echo "[fetch] $DEST_PATH already present ($(stat -c%s "$DEST_PATH") bytes); skipping."
    exit 0
fi

# Source the locally-built GStreamer 1.24 (vp8enc + webmmux live there).
GST_ENV="$HOME/gst-1.24/env.sh"
if [ -f "$GST_ENV" ]; then
    # shellcheck disable=SC1090
    source "$GST_ENV"
fi

echo "[fetch] generating VP8/WebM clip → $DEST_PATH"
# 30 seconds (900 frames at 30 fps) of `pinwheel` — a rotating radial
# pattern. Has motion (rotation drives P-frame variance — what real
# content has and synthetic `ball` lacks) but spatial structure (vp8
# compresses it within the encoder's bitrate budget — `snow`-style
# noise blows past the budget and confounds the network experiment).
# Length is chosen so any single experiment run fits without looping
# (configurations target 20s runs); loop: false in
# specs/videos/teleop-realmotion.yaml avoids seek-on-EOS interactions
# with SCReAM's RTP queue accounting.
gst-launch-1.0 -e \
    videotestsrc pattern=pinwheel num-buffers=900 \
    ! video/x-raw,format=I420,width=1280,height=720,framerate=30/1 \
    ! vp8enc target-bitrate=1500000 cpu-used=8 deadline=1 keyframe-max-dist=60 \
    ! webmmux \
    ! filesink location="$DEST_PATH"

echo "[fetch] done — $DEST_PATH ($(stat -c%s "$DEST_PATH") bytes)"
