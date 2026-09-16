#!/bin/sh
# Double-clicked .app launcher: opens Terminal and runs the wizard menu.
RES="$(cd "$(dirname "$0")/../Resources" && pwd)"
BIN="$RES/ThunderSweeper/ThunderSweeper"
if [ ! -x "$BIN" ]; then
    osascript -e 'display alert "ThunderSweeper" message "找不到内置程序，请重新解压安装包。"'
    exit 1
fi
osascript >/dev/null 2>&1 <<EOF
set shellCmd to (quoted form of "$BIN") & " wizard"
tell application "Terminal"
    activate
    do script shellCmd
end tell
EOF
