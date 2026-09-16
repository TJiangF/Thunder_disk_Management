#!/usr/bin/env bash
# Build the distributable macOS app: ./build.sh
#   dist/ThunderSweeper/                     -> 绿色目录（双击里面的命令即可运行）
#   dist/迅雷云盘整理助手.app                  -> 双击运行（打开终端菜单）
#   dist/ThunderSweeper-<version>-macos.zip  -> 对外分发的压缩包
#
# onedir 打包：启动秒开（onefile 每次启动要解包内置 ffmpeg，约 10 秒）。
set -euo pipefail
cd "$(dirname "$0")"

PY="${PY:-.venv/bin/python}"
if [ ! -x "$PY" ]; then PY=python3; fi
echo "==> 使用解释器: $PY"

echo "==> 安装依赖"
"$PY" -m pip install -q -r requirements.txt
"$PY" -m pip show pyinstaller >/dev/null 2>&1 || "$PY" -m pip install -q pyinstaller

echo "==> 生成图标"
"$PY" packaging/make_icon.py >/dev/null

echo "==> 自检（离线）"
"$PY" -m thunder_sweeper selftest | tail -2

echo "==> PyInstaller 打包（onedir）"
"$PY" -m PyInstaller packaging/ThunderSweeper.spec --noconfirm --clean \
    --distpath dist --workpath build >/tmp/sweeper-build.log 2>&1 || {
        tail -25 /tmp/sweeper-build.log; exit 1; }

VERSION="$("$PY" -c 'from thunder_sweeper import util; print(util.app_version())')"
BUNDLE="dist/ThunderSweeper"
APP="dist/迅雷云盘整理助手.app"
echo "==> 组装 .app (v$VERSION)"

# 让 .app 自包含：把整个 onedir 目录塞进 Resources
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp -R "$BUNDLE" "$APP/Contents/Resources/ThunderSweeper"
cp packaging/icon.icns "$APP/Contents/Resources/icon.icns"
sed "s/__VERSION__/$VERSION/g" packaging/Info.plist.template > "$APP/Contents/Info.plist"
cp packaging/launcher.sh "$APP/Contents/MacOS/ThunderSweeper"
chmod +x "$APP/Contents/MacOS/ThunderSweeper" \
         "$APP/Contents/Resources/ThunderSweeper/ThunderSweeper"
codesign --force --deep --sign - "$APP" 2>/dev/null || echo "（跳过签名）"

echo "==> 打包 zip"
(cd dist && zip -qry "ThunderSweeper-$VERSION-macos.zip" \
    "ThunderSweeper" "迅雷云盘整理助手.app")

echo
echo "完成："
du -sh dist/ThunderSweeper "$APP" "dist/ThunderSweeper-$VERSION-macos.zip"
echo "验证：dist/ThunderSweeper/ThunderSweeper --version && dist/ThunderSweeper/ThunderSweeper selftest"
