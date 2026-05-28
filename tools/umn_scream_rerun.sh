#!/usr/bin/env bash
# Fair re-run of UMN's SCReAM arm only, with network_time_sync + smart_buffer
# enabled (matching their field config) so the one-way-delay congestion
# signal is actually live. Output -> ~/comparison-runs/umn-scream-v2/.
# The original (handicapped) data stays in ~/comparison-runs/umn-scream/.
set -u
GE="$HOME/GstreamerExp"
UMN="$HOME/teleop-gopher-streamer-build"
OUT="$HOME/comparison-runs/umn-scream-v2"
TRACES="ho rb cqi"
REPS="${1:-3}"
DUR=70
source "$HOME/gst-1.24/env.sh"
export PATH="$HOME/.local/bin:$PATH"

rm -rf "$OUT"; mkdir -p "$OUT"
echo "[umn-v2] start $REPS reps x {$TRACES}"; date
for t in $TRACES; do
  for r in $(seq 1 "$REPS"); do
    echo "[umn-v2] trace=$t rep=$r"
    cd "$UMN"
    sed -e 's|receiver_host: "10.0.0.1"|receiver_host: "127.0.0.1"|' \
        -e 's|congestion_controller: .*|congestion_controller: "scream"|' \
        configs/comparison/realmotion-vp8-base.yaml > /tmp/umn-scream-v2.yaml
    rm -rf logs/comparison
    cd "$GE"
    sudo .venv/bin/python tools/tc_lo_driver.py \
        --spec "specs/networks/mahimahi-5g-$t-100ms-x0p33.yaml" \
        --duration "$DUR" >/tmp/tc.log 2>&1 &
    TC=$!; sleep 1
    cd "$UMN"
    timeout 90 poetry run experiment --config /tmp/umn-scream-v2.yaml --no-display \
        >/tmp/run-umn-v2.log 2>&1
    sudo kill "$TC" 2>/dev/null; sleep 1
    dst="$OUT/$t/rep$r"; mkdir -p "$dst"
    cp -r logs/comparison/* "$dst"/ 2>/dev/null
    echo "    -> $dst"
  done
done
echo "[umn-v2] DONE"; date
