#!/usr/bin/env bash
# Build the distributable macOS app: ./build.sh
#   dist/ThunderSweeper                      -> 单文件命令行程序
#   dist/ThunderSweeper.app                  -> 双击运行（打开终端菜单）
#   dist/ThunderSweeper-<version>-macos.zip  -> 对外分发的压缩包
set -euo pipefail
cd "$(dirname "$0")"

PY="${PY:-.venv/bin/python}"
if [ ! -x "$PY" ]; then PY=python3; fi
echo "==> 使用解释器: $PY"

echo "==> 安装依赖"
"$PY" -m pip install -q -r requirements.txt
"$PY" -m pip show pyinstaller >/dev/null 2>&1 || "$PY" -m pip install -q pyinstaller

echo "==> 生成图标"
"$PY" packaging/make_icon.py

echo "==> 自检（离线）"
"$PY" -m thunder_sweeper selftest | tail -3

echo "==> PyInstaller 打包"
"$PY" -m PyInstaller packaging/ThunderSweeper.spec --noconfirm --clean \
    --distpath dist --workpath build

VERSION="$("$PY" -c 'from thunder_sweeper import util; print(util.app_version())')"
APP="dist/ThunderSweeper.app"
echo "==> 组装 .app (v$VERSION)"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp dist/ThunderSweeper "$APP/Contents/Resources/ThunderSweeper"
cp packaging/icon.icns "$APP/Contents/Resources/icon.icns"
sed "s/__VERSION__/$VERSION/g" packaging/Info.plist.template > "$APP/Contents/Info.plist"
cp packaging/launcher.sh "$APP/Contents/MacOS/ThunderSweeper"
chmod +x "$APP/Contents/MacOS/ThunderSweeper" "$APP/Contents/Resources/ThunderSweeper"
codesign --force --deep --sign - "$APP" 2>/dev/null || echo "（跳过签名）"

echo "==> 打包 zip"
(cd dist && zip -qry "ThunderSweeper-$VERSION-macos.zip" ThunderSweeper ThunderSweeper.app)

echo
echo "完成："
ls -lh dist/ThunderSweeper "dist/ThunderSweeper-$VERSION-macos.zip"
echo "验证：dist/ThunderSweeper --version && dist/ThunderSweeper selftest"
