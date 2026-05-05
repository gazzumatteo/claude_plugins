#!/bin/bash
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo "=== mobile-testing MCP Doctor ==="
echo ""

ok()  { echo -e "  ${GREEN}✔${NC} $1"; }
warn() { echo -e "  ${YELLOW}⚠${NC} $1"; }
err()  { echo -e "  ${RED}✖${NC} $1"; }

ERRORS=0

echo "--- System ---"
if command -v python3 &>/dev/null; then
    ok "python3: $(python3 --version 2>&1)"
else
    err "python3 not found"
    ERRORS=$((ERRORS+1))
fi

echo ""
echo "--- iOS Simulator ---"
if command -v xcrun &>/dev/null; then
    ok "xcrun found"
    if xcrun simctl list devices booted 2>/dev/null | grep -q "Booted"; then
        booted=$(xcrun simctl list devices booted 2>/dev/null | grep "Booted" | wc -l | tr -d ' ')
        ok "$booted booted simulator(s)"
        xcrun simctl list devices booted 2>/dev/null | grep "Booted" | while read -r line; do
            echo "     $line"
        done
    else
        warn "No booted simulators"
    fi
    available=$(xcrun simctl list devices available 2>/dev/null | grep -c "([A-F0-9-]\+)" || echo 0)
    ok "$available available simulators"
    xcrun simctl list runtimes -j 2>/dev/null | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    for r in d.get('runtimes', []):
        print(f'     {r[\"name\"]} {r[\"version\"]}')
except: pass
" 2>/dev/null || true
else
    err "xcrun not found — install Xcode"
    ERRORS=$((ERRORS+1))
fi

echo ""
echo "--- Android ---"
if command -v adb &>/dev/null; then
    ok "adb: $(adb version 2>&1 | head -1)"
    adb_devices=$(adb devices 2>/dev/null | tail -n +2 | grep -v "^$" | wc -l | tr -d ' ')
    if [ "$adb_devices" -gt 0 ]; then
        ok "$adb_devices connected device(s)"
        adb devices -l 2>/dev/null
    else
        warn "No connected Android devices"
    fi
    avds=$(emulator -list-avds 2>/dev/null || echo "")
    if [ -n "$avds" ]; then
        avd_count=$(echo "$avds" | grep -c . || echo 0)
        ok "$avd_count AVD(s) available: $(echo "$avds" | tr '\n' ' ')"
    else
        warn "No AVDs configured — run /setup-mobile to create one"
    fi
else
    warn "adb not found — Android testing not available"
fi

echo ""
echo "--- Appium ---"
if command -v appium &>/dev/null; then
    ver=$(appium --version 2>/dev/null || echo "?")
    ok "Appium v$ver"
    ok "Drivers:"
    appium driver list --installed 2>/dev/null | grep "✔" | while read -r line; do
        echo "     $line"
    done
    if curl -s http://localhost:4723/status >/dev/null 2>&1; then
        ok "Appium server running on port 4723"
    else
        warn "Appium server not running"
    fi
else
    warn "Appium not installed — run /setup-mobile to install"
fi

echo ""
echo "--- Python ---"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_DIR="$(dirname "$SCRIPT_DIR")"
if [ -f "$PLUGIN_DIR/pyproject.toml" ]; then
    ok "pyproject.toml found"
    if command -v uv &>/dev/null; then
        ok "uv found: $(uv --version 2>&1)"
    else
        warn "uv not installed — run: curl -LsSf https://astral.sh/uv/install.sh | sh"
    fi
else
    err "pyproject.toml not found in $PLUGIN_DIR"
    ERRORS=$((ERRORS+1))
fi

echo ""
if [ "$ERRORS" -eq 0 ]; then
    echo -e "${GREEN}=== All checks passed ===${NC}"
else
    echo -e "${YELLOW}=== $ERRORS issue(s) found ===${NC}"
    echo "Run /setup-mobile to fix"
fi
