#!/usr/bin/env bash
# Ablation rung for UMN's SCReAM: vary update interval + ramp-up gain
# (both on top of the fair S1 config: network_time_sync on, smart_buffer
# on). Isolates the contribution of reaction speed to the under-
# utilization seen in S1.
#
# args: <label> <interval_ms> <ramp_up_gain> [reps]
#   e.g. umn_ladder.sh s2 50 1.05    (faster loop, default gain)
#        umn_ladder.sh s3 50 1.15    (faster loop + faster climb)
set -u
LABEL="$1"; INTERVAL="$2"; UPGAIN="$3"; REPS="${4:-3}"
GE="$HOME/GstreamerExp"; UMN="$HOME/teleop-gopher-streamer-build"
OUT="$HOME/comparison-runs/umn-scream-$LABEL"
TRACES="ho cqi"; DUR=70
source "$HOME/gst-1.24/env.sh"; export PATH="$HOME/.local/bin:$PATH"

rm -rf "$OUT"; mkdir -p "$OUT"
echo "[$LABEL] interval=${INTERVAL}ms up_gain=${UPGAIN} reps=$REPS"; date
for t in $TRACES; do
  for r in $(seq 1 "$REPS"); do
    cd "$UMN"
    sed -e 's|receiver_host: "10.0.0.1"|receiver_host: "127.0.0.1"|' \
        -e 's|congestion_controller: .*|congestion_controller: "scream"|' \
        -e "s|update_interval_ms: .*|update_interval_ms: $INTERVAL|" \
        -e "s|ramp_up_gain: .*|ramp_up_gain: $UPGAIN|" \
        configs/comparison/realmotion-vp8-base.yaml > "/tmp/umn-$LABEL.yaml"
    rm -rf logs/comparison
    cd "$GE"
    sudo .venv/bin/python tools/tc_lo_driver.py \
        --spec "specs/networks/mahimahi-5g-$t-100ms-x0p33.yaml" \
        --duration "$DUR" >/tmp/tc.log 2>&1 &
    TC=$!; sleep 1
    cd "$UMN"
    timeout 90 poetry run experiment --config "/tmp/umn-$LABEL.yaml" --no-display \
        >/tmp/run-$LABEL.log 2>&1
    sudo kill "$TC" 2>/dev/null; sleep 1
    dst="$OUT/$t/rep$r"; mkdir -p "$dst"; cp -r logs/comparison/* "$dst"/ 2>/dev/null
    echo "[$LABEL] $t rep$r -> $dst"
  done
done
echo "[$LABEL] DONE"; date
