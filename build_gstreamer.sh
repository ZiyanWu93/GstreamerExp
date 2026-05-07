#!/usr/bin/env bash
# Build GStreamer 1.24 + gst-plugins-rs (rtp) into ~/gst-1.24, leaving
# the system GStreamer 1.16 untouched. Idempotent: skips work that is
# already done (version check on the installed gst-inspect-1.0).
#
# Run on each host that participates in distributed runs:
#     scp build_gstreamer.sh aum:/tmp/  &&  ssh aum  bash /tmp/build_gstreamer.sh
#     scp build_gstreamer.sh veda:/tmp/ &&  ssh veda bash /tmp/build_gstreamer.sh
#
# Needs sudo for apt installs only. Compile takes ~30 min on a 24-core
# host. Source tree at $SRC can be deleted after success to reclaim
# disk; install at $PREFIX is what matters.

set -euo pipefail

GST_VERSION="1.24.10"
PREFIX="$HOME/gst-1.24"
SRC="$HOME/gst-src-1.24"
JOBS="$(nproc)"

if [[ -x "$PREFIX/bin/gst-inspect-1.0" ]]; then
    INSTALLED=$("$PREFIX/bin/gst-inspect-1.0" --version 2>/dev/null \
                | awk '/GStreamer/ {print $2}')
    if [[ "$INSTALLED" == "$GST_VERSION" ]] \
       && [[ -f "$PREFIX/lib/gstreamer-1.0/libgstrsrtp.so" ]]; then
        echo "[build] GStreamer $GST_VERSION + rsrtp already at $PREFIX"
        exit 0
    fi
    echo "[build] partial install at $PREFIX (gst=$INSTALLED, target=$GST_VERSION); continuing"
fi

# 1) System build deps (sudo). Conservative set chosen to cover
#    everything our pipeline elements need (vp8, rtp, udp, x11
#    autovideosink, gobject-introspection for python bindings).
echo "[build] installing apt deps"
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
    git build-essential pkg-config gettext bison flex \
    ninja-build cmake \
    libglib2.0-dev libffi-dev libssl-dev liborc-0.4-dev \
    libgirepository1.0-dev gobject-introspection \
    libxv-dev libx11-dev libxcb-shm0-dev libgudev-1.0-dev \
    libvpx-dev libopus-dev \
    libsrtp2-dev libnice-dev libsoup2.4-dev \
    libpulse-dev libasound2-dev \
    libgtk-3-dev libcairo2-dev \
    python3-pip python3-gi gir1.2-glib-2.0

# Ubuntu 20.04 ships meson 0.61, but GStreamer 1.24 needs >= 1.1.
# Install a recent meson via pip3 to ~/.local/bin (which is on PATH
# under most shells; we also explicitly prepend it for this script).
pip3 install --user --upgrade 'meson>=1.3'
export PATH="$HOME/.local/bin:$PATH"
meson --version

# 2) Source: pin GStreamer monorepo to the exact tag.
mkdir -p "$SRC"
if [[ ! -d "$SRC/gstreamer/.git" ]]; then
    echo "[build] cloning gstreamer $GST_VERSION"
    git clone --depth 1 --branch "$GST_VERSION" \
        https://gitlab.freedesktop.org/gstreamer/gstreamer.git \
        "$SRC/gstreamer"
fi
if [[ ! -d "$SRC/gst-plugins-rs/.git" ]]; then
    echo "[build] cloning gst-plugins-rs (gstreamer-1.24 branch)"
    git clone --depth 1 --branch "0.13" \
        https://gitlab.freedesktop.org/gstreamer/gst-plugins-rs.git \
        "$SRC/gst-plugins-rs" \
      || git clone --depth 1 \
        https://gitlab.freedesktop.org/gstreamer/gst-plugins-rs.git \
        "$SRC/gst-plugins-rs"
fi

# 3) Configure + build core/base/good/bad. Keep --libdir=lib so paths
#    are predictable (Debian multiarch otherwise puts things under
#    lib/x86_64-linux-gnu and complicates env.sh).
echo "[build] configuring meson (release, libdir=lib)"
cd "$SRC/gstreamer"
if [[ -d build ]]; then
    meson setup --reconfigure build \
        --prefix="$PREFIX" --libdir=lib \
        --buildtype=release \
        -Dgtk_doc=disabled -Dexamples=disabled -Dtests=disabled \
        -Dgst-examples=disabled -Ddevtools=disabled \
        -Dgpl=enabled -Dlibav=disabled -Dugly=disabled \
        -Drs=disabled -Dpython=disabled -Dsharp=disabled
else
    meson setup build \
        --prefix="$PREFIX" --libdir=lib \
        --buildtype=release \
        -Dgtk_doc=disabled -Dexamples=disabled -Dtests=disabled \
        -Dgst-examples=disabled -Ddevtools=disabled \
        -Dgpl=enabled -Dlibav=disabled -Dugly=disabled \
        -Drs=disabled -Dpython=disabled -Dsharp=disabled
fi
echo "[build] compiling (-j$JOBS); this is the slow step"
ninja -C build -j "$JOBS"
ninja -C build install

# 4) gst-plugins-rs / rtp plugin (contains rtpgccbwe). Build against
#    our freshly-installed GStreamer via PKG_CONFIG_PATH.
echo "[build] building gst-plugins-rs/rtp"
cd "$SRC/gst-plugins-rs"
PKG_CONFIG_PATH="$PREFIX/lib/pkgconfig" \
LD_LIBRARY_PATH="$PREFIX/lib" \
    cargo build --release -p gst-plugin-rtp

mkdir -p "$PREFIX/lib/gstreamer-1.0"
cp -f target/release/libgstrsrtp.so "$PREFIX/lib/gstreamer-1.0/"

# 5) Write env.sh — sourced by the worker before running.
cat > "$PREFIX/env.sh" <<EOF
# Locally-built GStreamer $GST_VERSION + rsrtp. Source from anywhere.
export GST_DIR="$PREFIX"
export PATH="\$GST_DIR/bin:\$PATH"
export LD_LIBRARY_PATH="\$GST_DIR/lib:\${LD_LIBRARY_PATH:-}"
export PKG_CONFIG_PATH="\$GST_DIR/lib/pkgconfig:\${PKG_CONFIG_PATH:-}"
export GI_TYPELIB_PATH="\$GST_DIR/lib/girepository-1.0:\${GI_TYPELIB_PATH:-}"
export GST_PLUGIN_PATH="\$GST_DIR/lib/gstreamer-1.0:\${GST_PLUGIN_PATH:-}"
EOF

# 6) Verify
echo "[build] verifying"
# shellcheck disable=SC1091
source "$PREFIX/env.sh"
gst-inspect-1.0 --version
gst-inspect-1.0 rtpgccbwe | head -5

echo "[build] done. To use: source $PREFIX/env.sh"
