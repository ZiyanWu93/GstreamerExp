#!/bin/bash
# Idempotent remote-host setup for GstreamerExp.
# Run on each host that will execute a worker. Sources the locally-built
# GStreamer 1.24 (~/gst-1.24/env.sh — built once via build_gstreamer.sh),
# verifies plugins, and rebuilds gstscream against the new headers if
# the existing build is stale (linked against the system 1.16).
#
# Invoked by cli.py before each distributed run; cheap on subsequent runs.

set -e

# This script lives in scripts/ but operates at the project root
# (where scream/, .scream-plugin/, and the patch are).
cd "$(dirname "$(realpath "$0")")/.."
HERE="$(pwd)"

# Fast path: skip the work if a previous successful run still applies.
# The stamp records the env.sh and gstscream .so mtimes; if neither has
# changed, plugin scanning and rebuild checks would all be no-ops and we
# can exit immediately. This drops per-run setup time from ~10-30s to <0.1s
# on hosts that have already been set up.
GST_ENV="$HOME/gst-1.24/env.sh"
GSTSCREAM_SO="scream/gstscream/target/debug/libgstscream.so"
SCREAMTX_IMP="scream/gstscream/src/screamtx/imp.rs"
STAMP="$HOME/.gstexp-setup.stamp"
_stamp_signature() {
    local env_mt so_mt imp_mt
    env_mt="$(stat -c '%Y' "$GST_ENV" 2>/dev/null || echo missing)"
    so_mt="$(stat -c '%Y' "$GSTSCREAM_SO" 2>/dev/null || echo missing)"
    imp_mt="$(stat -c '%Y' "$SCREAMTX_IMP" 2>/dev/null || echo missing)"
    echo "$env_mt:$so_mt:$imp_mt:$HERE"
}
if [ -f "$STAMP" ] && [ "$(cat "$STAMP" 2>/dev/null)" = "$(_stamp_signature)" ]; then
    echo "[setup] $(hostname): cached (stamp matches)"
    exit 0
fi

echo "[setup] $(hostname): root=$HERE"

# 0) Clone the SCReAM source tree if it isn't here yet. The repo's
#    scream/ directory is gitignored (kept out of the GstreamerExp
#    repo because it's hundreds of megabytes and has its own
#    upstream); first-time setup grabs it from the canonical source.
SCREAM_UPSTREAM="${SCREAM_UPSTREAM:-https://github.com/EricssonResearch/scream.git}"
SCREAM_REF="${SCREAM_REF:-master}"
if [ ! -d scream ]; then
    echo "[setup] cloning SCReAM upstream from $SCREAM_UPSTREAM ($SCREAM_REF)"
    git clone --depth 1 --branch "$SCREAM_REF" "$SCREAM_UPSTREAM" scream
fi

# 1) Use locally-built GStreamer 1.24 (run build_gstreamer.sh first
#    if this fails). System GStreamer 1.16 is left untouched.
if [ ! -f "$GST_ENV" ]; then
    echo "[setup] ERROR: $GST_ENV missing. Run build_gstreamer.sh first."
    exit 1
fi
# shellcheck disable=SC1090
source "$GST_ENV"

# 2) Verify GStreamer + python3-gi resolve to our 1.24
python3 - <<'PY'
import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst
Gst.init(None)
v = Gst.version_string()
print("[setup] Gst", v)
assert v.startswith("GStreamer 1.24"), f"expected 1.24, got {v}"
PY

# 3) Verify required elements (now includes rtpgccbwe from gst-plugins-rs)
missing=()
for el in vp8enc vp8dec rtpvp8pay rtpvp8depay udpsink udpsrc videotestsrc \
         videoconvert capsfilter fakesink rtpbin clockoverlay \
         rtpgccbwe; do
    gst-inspect-1.0 "$el" >/dev/null 2>&1 || missing+=("$el")
done
if [ "${#missing[@]}" -gt 0 ]; then
    echo "[setup] ERROR: missing GStreamer elements: ${missing[*]}"
    exit 1
fi

# 4) Apply the local EOS-panic patch to gstscream if it's not already
#    applied. Detection: presence of the distinctive `FlowError::Eos`
#    pattern in screamtx/imp.rs (only the patched version has it). The
#    rationale and the patch itself live in scream-eos-fix.patch.
PATCH="scripts/scream-eos-fix.patch"
if [ -f "$SCREAMTX_IMP" ] && [ -f "$PATCH" ]; then
    if ! grep -q "FlowError::Eos" "$SCREAMTX_IMP"; then
        echo "[setup] applying $PATCH"
        patch -p1 -N < "$PATCH"
    fi
fi

# 5) Rebuild gstscream against the new GStreamer if needed. Detection:
#    .so missing, OR libgstreamer-1.0.so newer than the .so, OR imp.rs
#    newer than the .so (i.e. we just applied the patch).
GST_LIB="$GST_DIR/lib/libgstreamer-1.0.so"
needs_rebuild=0
if [ ! -d scream ]; then
    : # no scream source, skip
elif [ ! -f "$GSTSCREAM_SO" ]; then
    needs_rebuild=1
elif [ -f "$GST_LIB" ] && [ "$GST_LIB" -nt "$GSTSCREAM_SO" ]; then
    needs_rebuild=1
elif [ -f "$SCREAMTX_IMP" ] && [ "$SCREAMTX_IMP" -nt "$GSTSCREAM_SO" ]; then
    needs_rebuild=1
fi

if [ "$needs_rebuild" -eq 1 ] && [ -d scream/gstscream ]; then
    echo "[setup] (re)building gstscream against $GST_DIR (one-time, ~minutes)..."
    pushd scream/gstscream/scripts >/dev/null
    bash build.sh
    popd >/dev/null
fi

# 5) Stage clean plugin dir (workaround for the gstscream plugin scanner
#    hang caused by the deps/ subfolder's many .so files). Both gstscream
#    and the locally-built rsrtp coexist on GST_PLUGIN_PATH (env.sh adds
#    $GST_DIR/lib/gstreamer-1.0; cli.py prepends .scream-plugin).
mkdir -p .scream-plugin
if [ -f "$GSTSCREAM_SO" ]; then
    cp -f "$GSTSCREAM_SO" .scream-plugin/
fi

# 6) Verify gstscream loads against the new GStreamer
if [ -f .scream-plugin/libgstscream.so ]; then
    if ! GST_PLUGIN_PATH="$HERE/.scream-plugin:$GST_PLUGIN_PATH" \
         LD_LIBRARY_PATH="$HERE/scream/code/wrapper_lib:$LD_LIBRARY_PATH" \
         gst-inspect-1.0 screamtx >/dev/null 2>&1; then
        echo "[setup] ERROR: gstscream built but screamtx fails to load"
        exit 1
    fi
fi

echo "[setup] OK on $(hostname) (Gst $(gst-inspect-1.0 --version | awk '/GStreamer/ {print $2}'))"

# Record the signature that this success applies to. The fast path at the
# top reads it and short-circuits while it still matches.
_stamp_signature > "$STAMP"
