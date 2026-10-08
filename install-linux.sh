#!/usr/bin/env bash
# screen-control Linux installer (Ubuntu/Mint family, X11 session).
# Safe to re-run: apt, venv creation and pip install are all idempotent.
set -euo pipefail

cd "$(dirname "$0")"

echo "============================================================"
echo " screen-control installer (Linux / X11)"
echo "============================================================"
echo

# ---------------------------------------------------------------------
# [1/5] Display server preflight
# ---------------------------------------------------------------------
echo "[1/5] Checking display server..."
if [ "${XDG_SESSION_TYPE:-}" = "wayland" ] || { [ -n "${WAYLAND_DISPLAY:-}" ] && [ "${XDG_SESSION_TYPE:-}" != "x11" ]; }; then
    echo "[ERROR] Wayland session detected - screen-control requires X11." >&2
    echo "        Log out, choose the 'Cinnamon (X11)' session at the login screen, then re-run." >&2
    exit 1
fi
if [ -z "${DISPLAY:-}" ]; then
    echo "[WARN] DISPLAY is not set - X11 tools (xdotool/wmctrl) will not work until it is."
fi

# ---------------------------------------------------------------------
# [2/5] System packages (sudo)
# ---------------------------------------------------------------------
echo "[2/5] Installing system packages (sudo may prompt for your password)..."
sudo apt-get update
sudo apt-get install -y xdotool wmctrl imagemagick python3-venv python3-tk python3-dev

# ---------------------------------------------------------------------
# [3/5] Python virtualenv
# ---------------------------------------------------------------------
echo "[3/5] Creating Python virtualenv in .venv ..."
python3 -m venv .venv

# ---------------------------------------------------------------------
# [4/5] Python dependencies
# ---------------------------------------------------------------------
echo "[4/5] Installing Python dependencies (rapidocr is a ~200MB download; this can take a while)..."
.venv/bin/pip install -r requirements.txt

# ---------------------------------------------------------------------
# [5/5] Game mode (/dev/uinput) permission report
# ---------------------------------------------------------------------
echo "[5/5] Checking /dev/uinput writability for game mode..."
if [ -w /dev/uinput ]; then
    echo "       game mode: OK"
else
    echo "       game mode: UNAVAILABLE - no write access to /dev/uinput."
    echo "       Fix it with one of these (spec 5.4), then re-run this script:"
    echo "         sudo usermod -aG input \$USER   # then log out and back in"
    cat <<'UDEV'
         echo 'KERNEL=="uinput", MODE=="0660", GROUP=="input"' | sudo tee /etc/udev/rules.d/99-uinput.rules
UDEV
fi

echo
echo "Done. Start the server with ./start-server.sh"
