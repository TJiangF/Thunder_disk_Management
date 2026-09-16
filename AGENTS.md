# AGENTS.md — ThunderSweeper 协作约定

## 每次改完代码必须做（硬性要求）

1. **离线自检**：`./sweeper selftest`（应全绿）。
2. **重新打包**：`./build.sh`（PyInstaller onedir + 组装 `.app` + zip）。
3. **验证打包产物可用**（源码改动不重新 build 不会进 `.app`）：
   - `./dist/ThunderSweeper/ThunderSweeper --version`
   - `./dist/ThunderSweeper/ThunderSweeper selftest`
   - `./dist/迅雷云盘整理助手.app/Contents/Resources/ThunderSweeper/ThunderSweeper --version`
4. **提交并推送**：`git push origin main`（提交信息用中文、说明改了什么）。

> `dist/`、`build/` 已在 `.gitignore` 中，不要提交打包产物。

## 运行方式

- 始终用 `./sweeper <命令>`（内部 `exec .venv/bin/python -m thunder_sweeper`），不要用系统 `python`。
- 不带参数运行 `./sweeper` 进入交互式菜单（`.app` 也是这个菜单）。

## 常用命令

```bash
./sweeper selftest          # 离线自检
./sweeper selftest --live   # 含真实 API 沙箱测试
./build.sh                  # 打包
```

## 代码结构（简）

- `thunder_sweeper/__main__.py` — CLI 入口与各子命令
- `thunder_sweeper/wizard.py` — 交互式菜单（`.app` 走的路径）
- `thunder_sweeper/screenshots.py` — 截图（直链 + ffmpeg）；`process_many` / `process_video`
- `thunder_sweeper/util.py` — 路径/日志/JSON/`ProgressBar`/`LiveDisplay`
- `thunder_sweeper/review_server.py` — 本地管家网页
- `build.sh` / `packaging/` — 打包脚本与资源
