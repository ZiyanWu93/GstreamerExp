#!/usr/bin/env bash
# Point chrony at public NTP pools so the host's wall clock tracks
# real time. After this runs, frame_latency reports will be in
# absolute milliseconds rather than offset by ~50-150ms of clock
# skew measured over SSH.
#
# Run on each host that participates in distributed runs:
#     scp fix_clock.sh aum:/tmp/  &&  ssh aum  'sudo bash /tmp/fix_clock.sh'
#     scp fix_clock.sh veda:/tmp/ &&  ssh veda 'sudo bash /tmp/fix_clock.sh'
#
# Side effects: rewrites /etc/chrony/chrony.conf (backed up to
# .pre-gstexp), restarts chrony, blocks until first sync. No-op if
# chrony already has a synced reference.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "must run as root (sudo)" >&2
  exit 1
fi

if ! command -v chronyc >/dev/null; then
  apt-get update
  apt-get install -y chrony
fi

# chrony is "synced" when stratum is between 1 and 15; stratum 0 means
# "no usable reference" (the Reference ID also reads "00000000" in that
# state — the previous regex-based check misread that as success).
stratum=$(chronyc tracking 2>/dev/null | awk '/^Stratum/ {print $3}')
if [[ -n "$stratum" && "$stratum" -gt 0 && "$stratum" -lt 16 ]]; then
  echo "chrony already synced (stratum=$stratum) — nothing to do"
  exit 0
fi

conf=/etc/chrony/chrony.conf
if [[ -f $conf && ! -f ${conf}.pre-gstexp ]]; then
  cp "$conf" "${conf}.pre-gstexp"
fi

cat > "$conf" <<'EOF'
# Managed by GstreamerExp/fix_clock.sh — points at public NTP pools
# so absolute wall-clock measurements (frame latency) are trustworthy.
pool pool.ntp.org iburst maxsources 4
pool time.cloudflare.com iburst

driftfile /var/lib/chrony/chrony.drift
makestep 1.0 3
rtcsync
logdir /var/log/chrony
EOF

systemctl restart chrony
sleep 2
chronyc waitsync 30 0.05
chronyc tracking
