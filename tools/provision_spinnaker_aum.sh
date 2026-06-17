#!/usr/bin/env bash
# Provision the CAMERA host (aum) for the Teledyne/FLIR Spinnaker SDK +
# PySpin, so the `camera` source backend's mode:real path works once a
# physical machine-vision camera is attached. This is PREPARATION — it does
# NOT download the SDK (that needs a Teledyne account login, which is the
# operator's step) and does NOT require a camera to be present.
#
# What it does (idempotent): install the operator-provided Spinnaker SDK
# .deb bundle, add the USB3-Vision udev rules + flirimaging group, raise the
# usbfs memory limit (USB3-Vision needs it at high res/fps), and pip-install
# the matching PySpin wheel into the project venv.
#
# Usage:
#   tools/provision_spinnaker_aum.sh --dry-run            # print every command, change nothing
#   tools/provision_spinnaker_aum.sh --sdk-deb <path> [--wheel <pyspin.whl>] [--venv <dir>]
#
# Run ON aum (the camera/media host). Most steps need sudo.
set -euo pipefail

DRY_RUN=0
SDK_DEB=""
WHEEL=""
VENV="${VENV:-$HOME/gstexp/.venv}"
USBFS_MB=1000
RUN_USER="${SUDO_USER:-$USER}"

usage() { sed -n '2,20p' "$0"; exit "${1:-0}"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run)  DRY_RUN=1 ;;
    --sdk-deb)  SDK_DEB="${2:?--sdk-deb needs a path}"; shift ;;
    --wheel)    WHEEL="${2:?--wheel needs a path}"; shift ;;
    --venv)     VENV="${2:?--venv needs a path}"; shift ;;
    -h|--help)  usage 0 ;;
    *) echo "unknown arg: $1" >&2; usage 1 ;;
  esac
  shift
done

# run: echo (and, unless --dry-run, execute) a command.
run() {
  printf '  + %s\n' "$*"
  if [ "$DRY_RUN" -eq 0 ]; then "$@"; fi
}

echo "[provision] host=$(hostname) user=$RUN_USER venv=$VENV dry_run=$DRY_RUN"

# 1) Spinnaker SDK bundle (operator-downloaded from the Teledyne site).
echo "[1/5] Spinnaker SDK bundle"
if [ -z "$SDK_DEB" ]; then
  echo "  ! no --sdk-deb given. Download the Linux Spinnaker SDK .deb bundle"
  echo "    from teledynevisionsolutions.com (account login required — operator"
  echo "    step), then re-run with --sdk-deb <path-to-extracted-bundle-dir-or-deb>."
else
  if [ "$DRY_RUN" -eq 0 ] && [ ! -e "$SDK_DEB" ]; then
    echo "  ! --sdk-deb path not found: $SDK_DEB" >&2; exit 2
  fi
  # The bundle ships an install_spinnaker.sh that registers udev rules and
  # offers to create the flirimaging group; prefer it when present.
  if [ -d "$SDK_DEB" ] && [ -f "$SDK_DEB/install_spinnaker.sh" ]; then
    run sudo bash "$SDK_DEB/install_spinnaker.sh"
  else
    run sudo apt-get install -y "$SDK_DEB"
  fi
fi

# 2) flirimaging group + membership (the SDK installer usually creates it).
echo "[2/5] flirimaging group membership"
run sudo groupadd -f flirimaging
run sudo usermod -aG flirimaging "$RUN_USER"
echo "    (log out/in for the group to take effect)"

# 3) USB3-Vision udev rules (skip/ignore if the camera is GigE-Vision).
echo "[3/5] USB3-Vision udev rules"
UDEV=/etc/udev/rules.d/40-flir-spinnaker.rules
udev_body() {
  # FLIR/Point Grey USB vendor IDs -> flirimaging group, raised usbfs buffer.
  cat <<'RULES'
SUBSYSTEM=="usb", ATTRS{idVendor}=="1e10", GROUP="flirimaging"
SUBSYSTEM=="usb", ATTRS{idVendor}=="1724", GROUP="flirimaging"
RULES
}
if [ "$DRY_RUN" -eq 1 ]; then
  echo "  + write $UDEV:"; udev_body | sed 's/^/      /'
  echo "  + sudo udevadm control --reload-rules && sudo udevadm trigger"
else
  udev_body | sudo tee "$UDEV" >/dev/null
  sudo udevadm control --reload-rules
  sudo udevadm trigger
fi

# 4) usbfs memory (USB3-Vision needs a large buffer at high res/fps).
echo "[4/5] usbfs memory limit (${USBFS_MB} MB)"
run sudo sh -c "echo ${USBFS_MB} > /sys/module/usbcore/parameters/usbfs_memory_mb"
echo "    persist across reboots by adding usbcore.usbfs_memory_mb=${USBFS_MB} to"
echo "    GRUB_CMDLINE_LINUX_DEFAULT in /etc/default/grub, then: sudo update-grub"
echo "    (GigE-Vision instead? skip usbfs; set NIC MTU=9000 jumbo frames + PTP.)"

# 5) PySpin wheel into the project venv (version MUST match the SDK).
echo "[5/5] PySpin wheel"
if [ -z "$WHEEL" ]; then
  echo "  ! no --wheel given. The Spinnaker bundle ships a matching"
  echo "    spinnaker_python-*.whl; install it into the venv with the SAME"
  echo "    version as the SDK: $VENV/bin/pip install <spinnaker_python.whl>"
else
  run "$VENV/bin/pip" install "$WHEEL"
  run "$VENV/bin/python" -c "import PySpin; print('PySpin', PySpin.System.GetInstance().GetLibraryVersion())"
fi

echo "[provision] done. Verify with: $VENV/bin/python -c 'import PySpin' and"
echo "            (camera attached) the Spinnaker 'SpinView' enumerates the device."
