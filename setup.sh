#!/usr/bin/env bash
#
# setup.sh — one-command bootstrap for the AMPWorks WLED development environment.
#
# Clone this repo, run ./setup.sh, then build:
#     cd WLED && ../.venv/bin/pio run -e ampworks
#
# What it does:
#   1. Checks prerequisites (git, python3/pip, Node.js/npm).
#   2. Initializes submodules (WLED firmware + ArduinoLibs: MPR121, Debug).
#   3. Installs PlatformIO into an isolated .venv (does not touch your global Python).
#   4. Verifies with a smoke build (skip via --no-verify).
#
# Re-runnable / idempotent.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

VERIFY=1
BUILD_ENV="ampworks"

usage() {
  cat <<'USAGE'
Usage: ./setup.sh [options]
  --no-verify        Skip the smoke build at the end.
  --env=<pio-env>    Env to verify (default: ampworks; e.g. --env=apa102_mpr121).
  -h, --help         Show this help.
USAGE
}

for arg in "$@"; do
  case "$arg" in
    --no-verify) VERIFY=0 ;;
    --env=*)     BUILD_ENV="${arg#*=}" ;;
    -h|--help)   usage; exit 0 ;;
    *) printf 'ERROR: unknown argument: %s\n\n' "$arg" >&2; usage >&2; exit 2 ;;
  esac
done

info() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mWARN:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# 1. Prerequisites
# ---------------------------------------------------------------------------
info "Checking prerequisites"
command -v git     >/dev/null 2>&1 || die "git not found — install git and re-run."
command -v python3 >/dev/null 2>&1 || die "python3 not found — install Python 3 and re-run."
python3 -m venv --help >/dev/null 2>&1 || die "python3 venv module unavailable — install python3-venv and re-run."
python3 -m pip --version >/dev/null 2>&1 || die "pip unavailable — install python3-pip (or ensure 'python3 -m pip' works)."

# Node.js is required by WLED's web-UI build, which PlatformIO runs automatically
# (WLED/pio-scripts/build_ui.py -> npm install && npm run build). We do NOT auto-install
# it — a cross-platform Node install is too fragile.
if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
  die "Node.js/npm not found. WLED's web-UI build (run automatically by PlatformIO) needs it.
     Install Node first, then re-run ./setup.sh:
       https://nodejs.org   or via nvm:  https://github.com/nvm-sh/nvm"
fi
info "git $(git --version | awk '{print $3}') | python $(python3 -V 2>&1 | awk '{print $2}') | node $(node -v) | npm $(npm -v)"

# ---------------------------------------------------------------------------
# 2. Submodules (WLED + ArduinoLibs)
# ---------------------------------------------------------------------------
info "Initializing submodules (WLED, ArduinoLibs)"
git submodule sync --recursive
git submodule update --init --recursive

# ---------------------------------------------------------------------------
# 3. PlatformIO in an isolated virtualenv
# ---------------------------------------------------------------------------
VENV="$REPO_ROOT/.venv"
PIO="$VENV/bin/pio"
if [ ! -x "$PIO" ]; then
  info "Creating PlatformIO virtualenv (.venv) — this won't touch your global Python"
  python3 -m venv "$VENV"
  "$VENV/bin/python" -m pip install --quiet --upgrade pip
  info "Installing PlatformIO (first run downloads a fair bit — grab a coffee)"
  "$VENV/bin/python" -m pip install --quiet platformio
else
  info "PlatformIO virtualenv already present — skipping install"
fi
info "PlatformIO $("$PIO" --version | awk '{print $NF}') at $PIO"

# ---------------------------------------------------------------------------
# 4. Verify build
# ---------------------------------------------------------------------------
if [ "$VERIFY" -eq 1 ]; then
  info "Smoke build: pio run -e $BUILD_ENV (first build also fetches the ESP32 toolchain)"
  ( cd "$REPO_ROOT/WLED" && "$PIO" run -e "$BUILD_ENV" )
  info "Build OK."
else
  info "Skipping smoke build (--no-verify)."
fi

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
printf '\n\033[1;32mSetup complete.\033[0m\n'
cat <<'NEXT'
Next steps:
  source .venv/bin/activate                 # put pio on your PATH (or call ./.venv/bin/pio)
  cd WLED && pio run -e ampworks            # build for the AMPWorks board

  # Flash over HTTP OTA (override the device IP as needed):
  WLED_IP=<device-ip> pio run -e ampworks -t upload
  # The default upload_port lives in platformio.ini; override it per-machine in
  # WLED/platformio_override.ini (see platformio_override.sample.ini).

Custom envs: ampworks (VSPI) and apa102_mpr121 (HSPI + MPR121; available once its PR lands).
NEXT
