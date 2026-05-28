#!/usr/bin/env bash
# SCReAM-vs-SCReAM comparison sweep, run ON aum.
#
# Two arms — our reference SCReAM (config 90) vs UMN's SCReAM
# reimplementation (congestion_controller=scream) — across three 5G
# traces (HO, RB, CQI) x REPS reps, all single-host loopback with
# tc-on-lo shaping driven from the same translated trace spec.
#
# Each run's output is copied to a distinct preserved dir:
#   ~/comparison-runs/<arm>/<trace>/rep<N>/
#
# Usage:  bash comparison_sweep.sh [reps]   (default 3)
set -u

GE="$HOME/GstreamerExp"
UMN="$HOME/teleop-gopher-streamer-build"
OUT="$HOME/comparison-runs"
TRACES="ho rb cqi"
REPS_N="${1:-3}"
DUR=70

source "$HOME/gst-1.24/env.sh"
export PATH="$HOME/.local/bin:$PATH"

start_tc() {   # $1=trace -> prints driver PID
    local spec="$GE/specs/networks/mahimahi-5g-$1-100ms-x0p33.yaml"
    sudo "$GE/.venv/bin/python" "$GE/tools/tc_lo_driver.py" \
        --spec "$spec" --duration "$DUR" >/tmp/tc-sweep.log 2>&1 &
    echo $!
}

stop_tc() { sudo kill "$1" 2>/dev/null; sleep 1; }

run_ours() {   # $1=trace $2=rep
    cd "$GE"
    [ -f hosts.yaml ] && mv hosts.yaml /tmp/hosts.aside
    local tc; tc=$(start_tc "$1"); sleep 1
    timeout 100 .venv/bin/python cli.py 90 >/tmp/run-ours.log 2>&1
    stop_tc "$tc"
    [ -f /tmp/hosts.aside ] && mv /tmp/hosts.aside hosts.yaml
    local dst="$OUT/ours-scream/$1/rep$2"; mkdir -p "$dst"
    cp -r "$(ls -td runs/90/* | head -1)"/* "$dst"/ 2>/dev/null
    echo "    -> $dst"
}

run_umn() {    # $1=trace $2=rep
    cd "$UMN"
    sed -e 's|receiver_host: "10.0.0.1"|receiver_host: "127.0.0.1"|' \
        -e 's|congestion_controller: .*|congestion_controller: "scream"|' \
        configs/comparison/realmotion-vp8-base.yaml > /tmp/umn-scream.yaml
    rm -rf logs/comparison
    cd "$GE"; local tc; tc=$(start_tc "$1"); sleep 1
    cd "$UMN"
    timeout 90 poetry run experiment --config /tmp/umn-scream.yaml --no-display \
        >/tmp/run-umn.log 2>&1
    stop_tc "$tc"
    local dst="$OUT/umn-scream/$1/rep$2"; mkdir -p "$dst"
    cp -r logs/comparison/* "$dst"/ 2>/dev/null
    echo "    -> $dst"
}

rm -rf "$OUT"; mkdir -p "$OUT"
echo "[sweep] start: $REPS_N reps x {$TRACES} x {ours,umn} SCReAM"
date
for t in $TRACES; do
    for r in $(seq 1 "$REPS_N"); do
        echo "[sweep] ours-scream trace=$t rep=$r"; run_ours "$t" "$r"
        echo "[sweep] umn-scream  trace=$t rep=$r"; run_umn  "$t" "$r"
    done
done
echo "[sweep] DONE"
date
