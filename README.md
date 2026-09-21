# 迅雷云盘管家 (thunder_sweeper)

在 Chrome 里登录一次迅雷网盘，工具会自动：

1. **递归扫描整个网盘**，收集视频文件并按大小排序；
2. **重复去重**：按“文件大小完全相同 + 名称高度相似（番号优先）”找出重复文件，供你勾选删除；
3. **分类**：按命名规则自动分类（日本 / 欧美 / 国产 / 成人-其他 / 非成人 / 未知），分类树可**自定义与多层子分类**，并支持单个/批量手动分类；
4. 用 **直链 + ffmpeg 精确定位（HTTP Range seek）** 为每个视频截取 8 张截图（只下载关键帧附近数据，省带宽，可彻夜跑）；
5. 打开**本地管家页面**：
   - **文件管理**：WizTree 式矩形框图，面积=文件大小，颜色=分类，可下钻、筛选、拖拽框选；
   - **重复去重**：重复分组列表，可“每组保留最大、其余标记”；
   - **整理**：预览“按分类归类 + 删除空/垃圾文件夹”的方案（不改云盘）；
   - **视频审核**：缩略图卡片、页内云播、下载到本地、进度标记、右下角序号浮窗；
   - **待删除**：汇总结算并提交/执行；
6. **一键移入回收站**（可恢复，不会永久删除）；`organize` 支持**真正执行移动/清理**。

> 路径：`/Users/tf/thunder_video_sweeper`
>
> **想直接分发给别人用？** 见 [5.5 打包发布](#55-打包发布--直接分发给别人用重点)：`./build.sh` 产出免安装的 `.app` + 可执行程序（内置 ffmpeg）。

---

## 0. 快速开始（TL;DR）

**最省事（打包版）**：双击 `迅雷云盘整理助手.app`，按菜单 1→2→3→4→5 走。

**Windows 用户**：见 [0.5 Windows 使用](#05-windows-使用)（数据目录为 `%APPDATA%\ThunderSweeper`，入口为 `sweeper.bat`）。

**源码版**：

```bash
cd /Users/tf/thunder_video_sweeper

./sweeper login --restart     # 首次：在弹出的 Chrome 里登录迅雷网盘
./sweeper scan --resume       # 全盘扫描（可中断续跑）
./sweeper classify            # 分类
./sweeper shots --top 20      # 生成截图（最大的 20 个）
./sweeper review              # 打开管家页面，浏览/筛选/勾选
./sweeper apply               # 回终端执行删除（移入回收站）
```

不带参数运行 `./sweeper` 会进入**交互式菜单**；`./sweeper selftest` 可自检。

---

## 0.5 Windows 使用

代码是**同一份、跨平台**（macOS / Windows 单分支，平台差异按 `sys.platform` 在内部判断），
Windows 上无需改码。下面是 Windows 的差异点：

**安装依赖（PowerShell，项目根目录）**

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt   # 含 imageio-ffmpeg，自带 ffmpeg
```

**统一入口 `sweeper.bat`**：等价于 macOS 的 `./sweeper`，自动优先使用 `.venv\Scripts\python.exe`：

```powershell
sweeper.bat login --restart
sweeper.bat scan --resume
sweeper.bat classify
sweeper.bat shots --top 20
sweeper.bat review
sweeper.bat apply
```

或直接用系统 Python：`python -m thunder_sweeper <命令>`（注意别用错解释器，会缺 websocket）。

**首选的浏览器**：Chrome / Edge / Brave / Chromium 会自动探测（含
`C:\Program Files\...\chrome.exe`、`...\msedge.exe` 等标准路径）；
也可在 `config.json` 设 `chrome_path` 或设置环境变量 `THUNDER_SWEEPER_CHROME`。

**数据目录**：默认 `%APPDATA%\ThunderSweeper\data`（`sweeper.bat home` 可查看/修改）。

**打包（Windows）**

```powershell
powershell -ExecutionPolicy Bypass -File build_win.ps1
```

产物 `dist\ThunderSweeper\ThunderSweeper.exe`（onedir，内置 ffmpeg，双击/命令行运行）+
`dist\ThunderSweeper-<ver>-win64.zip`。

**已知小差异**：终端固定面板/进度条使用 ANSI 控制码与 `█░` 字符，
在 Windows Terminal / 新版 PowerShell 下正常；旧版 cmd 下可能显示乱码（功能性不受影响）。

**Windows 上也支持本地磁盘管理**（见 3.8）：`sweeper.bat local drives` →
`sweeper.bat local path E:\` → `sweeper.bat local scan`（全程只读）等，与云盘数据完全分离。

---

## 1. 安装依赖

```bash
cd /Users/tf/thunder_video_sweeper

# Python 依赖（已用国内镜像装好，如需重装）
.venv/bin/python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt

# ffmpeg：项目用 imageio-ffmpeg 自带静态 arm64 ffmpeg，已装好
# 如未装：.venv/bin/python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple imageio-ffmpeg
# 或系统安装：brew install ffmpeg
```

> **统一入口 `./sweeper`**：这是包装脚本，自动使用项目虚拟环境，等价于 `.venv/bin/python -m thunder_sweeper`。
> **请始终用 `./sweeper ...`**，不要用系统 `python`（会缺 `websocket` 等依赖）。

依赖检查：

```bash
./sweeper status
```

---

## 2. 命令总览

| 命令 | 作用 |
|---|---|
| `./sweeper login [--restart]` | 启动调试 Chrome 抓取 token（`--restart` 先关掉残留的调试 Chrome） |
| `./sweeper status` | 查看登录状态 / 视频数 / 待删数 |
| `./sweeper home [路径] [--reset]` | 查看/设置数据目录（默认 `~/Library/Application Support/ThunderSweeper`；省略路径则显示当前） |
| `./sweeper sync [--upload] [--cloud\|--local] [--yes]` | 同步配置：`--upload` 上传本地覆盖云端；默认/`--cloud` 拉取云端覆盖本地；`--local` 保留本地 |
| `./sweeper scan [--resume] [--fresh] [--limit N] [--min-size MB]` | 递归扫描网盘，视频按大小降序写 `data/videos.json`（`--resume` 续扫；上次已完成会自动完整重扫；`--fresh` 强制完整重扫） |
| `./sweeper classify` | 按命名规则分类，写 `data/classified.json` |
| `./sweeper dedupe` | 扫描重复文件（同大小+名称相似/同番号），写 `data/duplicates.json` |
| `./sweeper organize [--apply] [--limit N] [--delete-folders] [--clean-junk] [--include-other] [--scope PATH] [--no-rescan] [--yes]` | 整理全套：预览 / 移动 / 删空的源文件夹 / 递归删垃圾文件夹；`--scope` 只整理某路径（含子目录）下的文件；执行后**自动重扫+分类**（`--no-rescan` 可关） |
| `./sweeper shots [--top N] [--more N] [--all] [--ids ID1,ID2] [--min-size MB] [--no-resume] [--workers N]` | 生成 8 张截图（每张单独取新直链）；`--more N` 继续 N 个（可写 `-N`）；`--workers` 并发线程数，运行时带**进度条** |
| `./sweeper inspect <序号或id>` | 打印某视频 `file_info` 与 Range 测试（排查直链） |
| `./sweeper review [--port PORT]` | 打开本地管家页面（默认 8765，占用自动顺延） |
| `./sweeper apply [--dry-run] [--yes]` | 执行删除（移入回收站）；`--dry-run` 只预览 |
| `./sweeper wizard` | 交互式菜单（不带参数运行时自动进入） |
| `./sweeper selftest [--live]` | 内置自检；`--live` 额外做真实接口沙箱测试（只动自建的临时文件夹） |
| `./sweeper --version` | 显示版本号 |

各命令都可加 `-h` 看详细参数，例如 `./sweeper shots -h`。

---

## 3. 完整流程

### 3.1 登录（只需一次）

```bash
./sweeper login
```

- 会启动一个**独立的 Chrome**（独立配置目录 `.chrome-profile`，不影响你日常的 Chrome）。
- 在窗口里登录迅雷网盘，脚本自动抓取 `access_token / refresh_token / captcha` 存到 `data/tokens.json`，之后可无头运行。
- token 过期会自动续期；续期失败就重跑一次 `login`。
- 上次失败残留导致找不到页面时：`./sweeper login --restart`。

### 3.2 扫描

```bash
./sweeper scan                   # 完整扫描（推荐，能发现新增/删除）
./sweeper scan --resume          # 上次被中断则接着扫；若上次已完成则自动改为完整扫描
./sweeper scan --fresh           # 强制忽略上次进度，完整重扫
./sweeper scan --min-size 500    # 只看大于 500MB 的
```

- 结果写入 `data/videos.json`（按大小降序），进度写入 `data/scan_state.json`。
- **新增的文件夹/文件必须完整扫描才会被发现**：`--resume` 会跳过“已扫过的目录”，因此只用于“上次被中断时接着扫”；若上次已扫完，它会**自动转为完整扫描**（否则会秒结束、看不到新增内容）。
- 全盘较慢属正常，随时 `Ctrl+C`，下次加 `--resume` 接着扫。

### 3.3 分类

```bash
./sweeper classify
```

- 结果写入 `data/classified.json`，并按“分类 + 大小”排序。
- 匹配顺序：**最高优先级覆盖 → 目录名提示 → 分类关键词（子分类在前、逐级向上）→ 通用兜底**。也就是说：给某个**子分类**配关键词后，会**先匹配最深子分类**，没命中才回退到父分类/顶级分类的规则。顶级分类固定顺序为**国产 → 日本 → 欧美 → 非成人 → …**
- 关键词匹配：**纯字母词按“字母边界”**（`jul` 不会误命中 `julesjordan`，`aavv` 仍能命中 `aavv121`）；含数字/点/中文的词按子串。
- 兜底：形如 `HEZ-445`／`CRPD-443` 的**番号**优先判为**日本**（不会再因“纯英文多词”误判欧美）。
- **编辑过某分类的关键词后，该分类以你的列表为准（覆盖内置）**，可直接删掉不想要的内置项（如站点域名）。
- 目录名提示**会忽略 `/整理/...` 目标目录**；且已在 `/整理` 下的文件**只用文件名参与匹配**（否则目标目录名会反过来影响分类，形成自反馈）。
- 内置分类依据：工作室番号（JUQ/SONE/IPX/FSDSS…）、系列（FC2/Caribbean/1Pondo…）、盗版站域名（`hhd800.com`/`489155.com`/`aavv*`/`zzpp*`…）、站点关键词（brazzers/vixen/propertysex…）、中文特征（麻豆/探花/糖心/推特/淫妻…）、扩展名（pdf/zip/图片/音乐→非成人）。
- 关键词可在网页「分类设置」按分类/子分类编辑；纯命名推断，`未知` 需人工复核。

### 3.4 生成截图

```bash
./sweeper shots --top 20     # 最大的 20 个（默认 10）
./sweeper shots --more 100   # 在已完成的基础上再截 100 个（简写 -100）
./sweeper shots --all        # 剩余全部（简写 -all）
./sweeper shots --workers 5  # 并发 5 个线程同时截图（默认取 config.workers，通常 3）
./sweeper shots --min-size 500
```

- `--top N`：处理**最大的 N 个**（已完成的会跳过）。
- `--more N`：跳过已处理过的，**接着往后截 N 个**（“继续截图”）；`-N` 等价。
- `--all`：把剩余未处理的**全部**截完；`-all` 等价。
- **已尝试过的文件（含失败/只截到一半的）默认会被跳过**，不会反复卡在同一文件上；要重试它们加 `--retry-failed`。
- `--workers N`：**并发截图**（默认 3）。多个视频同时取直链+抽帧，明显更快；**不设上限**，可超过 CPU 逻辑线程数（按需调大；线程太多会受网络/带宽与同时运行的 ffmpeg 进程数限制）。建议 3~8 起步。并发下 token 刷新已做去重（5 秒内只刷新一次），不会互相踩踏。
- 每个视频 8 张截图，存 `data/thumbs/<文件id>/1..8.jpg`；已处理记录在 `data/shots_state.json`。
- 时间点由 `config.json` 的 `fractions` 控制。
- **交互式配置**：向导菜单 `[4] 生成截图` 会先显示**当前已截图数量**，再问**本次新增数量**和**并发数量**；数量可输入数字（在已完成基础上**继续截**，等价 `--more N`），或输入 **`all`** 表示**全部截图**（等价 `--all`）；并发**不设上限**（可超过本机 CPU 线程数）。
- **固定面板**：运行时在**终端底部锁定一块固定面板**（绝对光标定位、原地重绘，**不滚屏、不跳动**），自上而下：
  1. **进度条**（固定位置，只统计本次目标数量）：`[████░░░░] 12/50 ✓10 ✗2  截图`；
  2. **每个线程一行**（共并发数行）：显示该线程**当前正在截图的文件**及阶段（`取直链 / 直链就绪 / 截图 3/8`…），截完立即刷新为下一个文件名；
  3. **日志区**（最底部、固定行数）：异常与关键过程都在这里，**新消息在最上面、旧消息往下移**（保留最近若干条，完整历史保存在内存）。
  运行期间所有终端日志都会被收进面板的日志区，不会冲乱布局。结束后在面板位置打印成功/失败汇总与失败文件名。输出不是终端（重定向到文件）时退化为单行进度条 + 逐行日志。

### 3.5 打开管家页面

```bash
./sweeper review
```

自动打开 `http://127.0.0.1:8765/`，顶部页签（默认“文件管理”，顺序：文件管理 / 重复去重 / 整理 / 视频审核 / 待删除 / 分类设置）：

- **文件管理**（默认页，WizTree 式）：整屏矩形框图，点文件夹下钻、点文件块选中；下方表格同步列出当前目录（可按名称/大小排序），每行有“播放（页内云播）/下载”。
  - **拖拽框选**：在表格里按住鼠标上下拖动可**连续多选**文件；按住 `⌘/Ctrl` 拖动=在已有选择上**加选**；单击某行=切换该行选中。框选后可用顶部「批量分类」或加入“待删除”。
- **重复去重**：全局扫描重复文件——**大小完全相同**且**名称高度相似**（含番号相同，如 `KBR-033`）。**每组默认只显示一行**（最大那个），点“展开其余 N 个”查看全部副本。每个分组有“**全选该组删除**”和“仅保留最大”按钮；也可用页首“每组保留最大，其余标记”。站点域名（`xxx.com`）不会被误当番号。
- **整理**：预览 organize 方案（移动清单 / 将删除的文件夹 / 保留的文件夹）。顶部**作用路径**下拉（一级/二级级联菜单，列出 scan 到的所有路径，默认「全部路径」）：**默认全选＝整理全部**；选中某个路径后变为**只整理该路径（含子目录）下的文件**，路径外的文件**不移动、不修改、也不参与删除文件夹**。旁边有**勾选框**（移动默认执行）：`删除空白文件夹` / `清理垃圾文件夹` / `纠正 /整理 内错误归类`，以及 **执行** 按钮。执行完会**自动重新扫描 + 分类**，**进度条**显示 `[移动]/[清理]/[扫描]/[分类]` 各阶段进度，完成后页面自动刷新。也可用命令行 `organize --apply`（等价 `--scope PATH`）。
- **视频审核**：只列出**已生成截图**的视频；缩略图卡片（8 张/行 4 张）。标题旁：**“▶ 播放”= 页内云播**（弹层播放器，服务器实时取直链）；**“⬇ 下载”= 下载到本地**（走 VIP 加速直链，带原文件名）。右下角有**序号浮窗**：显示“第 X / N”、`◀ ▶` 上一个/下一个、输入序号“跳转”，并显示**进度标记“第 X 个”**、点“↩ 跳标记”一键跳回。顶部有：**排序**（默认“大小从大到小”）、**评分筛选**（全部 / ≥1★ … / 5★，与分类筛选叠加生效）、**截图按钮**（“继续截图 N 个”默认 100 / “全部截图” / **“停止”**：停止正在进行的截图，正在处理的文件结束后不再开始新的）和**并发数**输入（默认 3）。
  - **五星评分**：每张卡片可点星打分（1~5 星，点同一颗星可清除），评分保存在 `data/ratings.json`；用顶部“评分筛选”只看 ≥N 星的视频（叠加分类筛选，二者同时满足才显示）。
  - 说明：本页直接扫描 `data/thumbs/` 目录，**凡是截过图的都会显示**（不再依赖 `queue.json`）。
- **待删除**：汇总所有选中项（名称/路径/大小/分类）。两个按钮：
  - **执行删除（移入回收站）**：先弹窗**列出全部待删文件**供你核对，确认后网页直接执行删除并显示进度/成功失败数；删除成功的项会从清单和列表中自动移除，并**同步清理本地 `videos.json`**（重启不再显示已删文件）。
  - **仅导出清单**：把当前选择写入 `data/selections.json`。
  - **选择会自动保存**：勾选/取消/移除都会即时写回 `selections.json`（替换式），所以**重启 `review` 会精确还原你当前的待删选择**，手动移除的不会再出现。
- **分类设置**：
  - **分类树编辑**：可**新增顶级分类 / 加子分类 / 改名 / 删除**（子分类可多层嵌套）。删除某分类会连带其子分类，属于它的文件回到自动分类。分类树写入 `data/categories.json`，并用于网页下拉/筛选；**organize 会按分类树创建对应目录**（如 `日本/无码` → `/整理/日本/无码`）。
  - **关键词（点击展开编辑）**：分类树每行点「**关键词**」才展开文本框，可为**任意分类/子分类**编辑关键词（每行一个，大小写不敏感、子串匹配），保存后立即重新分类。文本框会**预填内置关键词**（日本/欧美/国产/非成人，含番号、域名等），可直接增删；保存后即以你的列表为准。**匹配时子分类优先、逐级向上**。
  - **最高优先级覆盖**：折叠区里每行 `关键字=分类`（分类可填名称或 ID），先于所有关键词规则。均写入 `data/classify_rules.json`，`./sweeper classify` 也会读取。
  - **按当前规则重新分类**：点页面上的该按钮，可**在不改关键词的情况下**对全部文件重新跑一遍分类（结果即时刷新）。
- **单个手动分类**：视频审核卡片右上角、文件管理表格里的分类下拉，默认显示“自动分类-XXX”；选具体分类=手动锁定（显示“手动”标记），选回“自动分类-XXX”=恢复自动。
- **批量手动分类**：勾选文件、**勾选文件夹**（文件夹行左侧的复选框；其**内所有视频含子目录**会一并归类），或点「选中所有可见」按筛选全选；在顶部「批量分类」下拉选分类（或“恢复自动”），点 **应用到选中** 一次改一批（写入 `data/manual_categories.json`，会从“待删除”选择里移除）。
- **页签滚动独立**：切换页签会记住各自滚动位置，互不影响（在审核页滚到中间，切到文件管理是在顶部，切回来还在原处）。
- **关闭服务**：右上角「**关闭服务**」按钮可让本地服务主动退出（终端里也会提示“服务已停止”）；当然也可以在终端按 `Ctrl+C`。
- 顶部：**筛选（级联下拉）** + 全选本目录 / 清空选择 / 提交删除。
  - 下拉默认「全部分类」；**鼠标移到某分类的 `›` 会展开下一级**；点选某个分类即**单选该分类（含其子分类）**，按钮显示所选路径，**并在该项右侧显示 ✓ 与高亮**；点「全部分类」恢复全部。每项后的数字为**整盘所有文件按分类的统计**（父级 = 自身 + 全部子分类的合计），与云盘无关路径；下方列表/框图仍按**云盘真实路径**导航显示。
- 选中方式：点框图文件块、点卡片空白处、或表格勾选框。

### 3.6 整理云盘（归类 + 清理）

**全部整理功能都在 `./sweeper organize` 这一个命令里**（默认只预览，不改动云盘）。

```bash
./sweeper organize                                   # 预览方案（不改动）
./sweeper organize --apply --limit 3 --yes           # 测试：只移动前 3 个（不删文件夹）
./sweeper organize --apply --yes                     # 移动（按分类树 → /整理/<分类路径>）
./sweeper organize --apply --delete-folders --yes    # 移动 + 删除空的/只剩垃圾的源文件夹
./sweeper organize --clean-junk --yes                # 递归删除只含垃圾文件的文件夹
./sweeper organize --apply --delete-folders --clean-junk --yes   # 一次做完
./sweeper organize --include-other --apply --yes     # 连“成人-其他”也一起移动
```

**参数**

| 参数 | 作用 |
|---|---|
| （无） | 只生成/预览方案，写 `data/organize_plan.json` |
| `--apply` | 执行移动 |
| `--limit N` | 只移动前 N 个（测试用；此时**不删文件夹**） |
| `--delete-folders` | 移动后删除“变空/只剩垃圾”的**源文件夹** |
| `--clean-junk` | 递归删除**只含垃圾文件或为空**的文件夹（不依赖移动方案） |
| `--include-other` | 把「成人-其他」也纳入移动（默认只移 日本/欧美/国产） |
| `--no-rescan` | 执行后**不**自动重新扫描+分类（默认会自动做） |
| `--fix-inside` | 连同**纠正“已在 `/整理` 内但归类不对”**的文件（默认不动它们） |
| `--scope PATH` | **只整理该云盘路径（含子目录）下的文件**；路径外的文件不移动、不修改、不参与删文件夹（网页「整理」页的“作用路径”下拉，等价） |
| `--yes` | 跳过确认（否则需输入 yes） |

**规则**
- 成人视频按**你的分类树**移动：例如分类为 `日本/无码`（子分类）的文件 → `/整理/日本/无码/`；顶级 `欧美` → `/整理/欧美/`。
- **只整理 `/整理` 之外的文件**：已经在 `/整理/...` 里的文件**默认不再搬动**（避免反复抖动）；若要用（已修正的）分类**纠正 `/整理` 内的错误归类**，加 `--fix-inside`（网页整理页勾“纠正 /整理 内错误归类”）。
- **差异预览**：方案里始终包含 **“路径与分类不一致”** 清单（`/整理/X` 里却是分类 Y 的文件），网页「整理」页与命令预览都会显示数量与明细（`mismatch_count`）；**勾选“纠正 /整理 内错误归类”后点“重新生成方案”，这些纠正移动会出现在“移动明细”里**（否则默认不移动它们；命令行等价 `--fix-inside`）。
- 默认移动**除「未知 / 非成人 / 成人-其他」外**的所有分类；`--include-other` 时也移动「成人-其他」。（因此子分类默认跟随所属顶级分类。）
- 源文件夹只剩种子/小文件 → 移动后**删除该文件夹**；只剩视频 → 移动后**删除**；还有较大其他文件 → **保留**。
- **未知 / 非成人 始终不动**；`--limit` 时**不执行任何文件夹删除**（安全）。
- 垃圾判定：`--clean-junk` 与 `--delete-folders` 都把以下扩展名视为垃圾（不看大小）：
  `apk html htm txt url torrent nfo jpg jpeg png gif webp bmp ico srt ass sub zip db ini xml json log exe lnk bat cmd …`
- 阈值/目标可配：`organize_base`(默认 `/整理`)、`organize_small_mb`(20)、`organize_large_mb`(100)、`organize_chunk`(8)、`organize_delay`(1.0)。

**其它**
- 移动是异步任务：工具会**等待任务完成**、遇“任务数超限”自动退避重试、失败转逐个重试；重复执行会**自动跳过已在目标**的文件。
- 已在回收站的文件会跳过（`file_move_from_recycle_bin`）。
- 网页“整理”页可预览方案；执行记录：`data/organize_applied.json`、`data/organize_clean.json`。
- **刷新即最新**：页面每次刷新都会从磁盘重新加载 `videos.json/classified.json` 与手动分类，所以重扫/分类/整理后**普通刷新**就能看到真实路径与分类（无需重启服务）。
- 想重做整盘整理前，建议先重新扫描以刷新 `data/files.json`：`rm -f data/scan_state.json && ./sweeper scan && ./sweeper classify`。

### 3.8 本地磁盘管理模式（Windows / macOS / Linux）

同一套框架也支持**管理本地硬盘**（云盘与本地互不影响）：
- 入口：**向导菜单**启动时先选「本地磁盘管理」；或命令行 `local` 子命令
  （`./sweeper local scan` / `sweeper.bat local scan` 均可）。
- 数据完全分离：本地记录写 `data_local/`（与云盘 `data/` 分开），
  **本地文件/分类/选择不会进云盘 sync**。
- 扫描**是只读遍历**，绝不改动磁盘上的任何文件。

**流程**

```bash
./sweeper local drives                 # 列出可用磁盘/路径
./sweeper local path E:\               # 选择要管理的路径（Windows 如 E:\，mac 如 /Volumes/XXX）
./sweeper local scan                   # 全盘只读扫描（可断点续扫）
./sweeper local classify               # 按命名规则分类（与云盘同一套规则）
./sweeper local dedupe                 # 找重复视频
./sweeper local shots --top 20         # 给最大的 20 个视频截图（ffmpeg 直接读盘）
./sweeper local review                 # 打开管家页面（框图/审核/分类/去重，页面内云播即本地流）
./sweeper local apply                  # 把勾选文件移入回收站目录
./sweeper local organize [--apply]     # 本地整理方案 / 执行（移动到 <路径>/整理/<分类>/）
```

**区别 / 说明**
- 「▶ 播放」= **调用系统默认播放器**直接打开本地文件（网页服务后台执行 `open` / `os.startfile` / `xdg-open`），不再有浏览器内嵌播放的卡顿。
- 「⬇ 下载」= 本地服务流式喂给浏览器（`data_local` 数据；支持 HTTP Range）。
- 删除 = 移入磁盘根下的 `.ThunderSweeper_Trash/` **隐藏回收夹**（可手动恢复，不会永久删除）。
- `organize` 预览**不会移动任何文件**；`--apply` 才会把视频移到 `<路径>/整理/<分类路径>/`，
  并把空/只剩垃圾的源文件夹移入回收夹。
- 网页「整理」页的"作用路径"下拉、分类设置、手动分类、评分、进度标记等全部沿用云盘实现。
- 云盘（`login/sync`/整理到网盘 `/整理`）完全不受影响：两套数据、两套路径互不干扰。

**注意**：在真实重要磁盘上执行 `organize --apply` / `apply` /
`organize --clean-junk` **之前**，建议先只跑预览并人工核对清单。

### 3.7 删除（移入回收站）
两种方式，二选一：

```bash
./sweeper apply --dry-run   # 终端：预览会删哪些，不动手
./sweeper apply             # 终端：二次确认，输入 yes 执行
./sweeper apply --yes       # 终端：跳过确认
```

或在管家页面 **待删除** 页签点 **“执行删除（移入回收站）”**：先弹窗列出全部待删项 → 确认 → 网页直接删除（显示进度与成功/失败数）。

---

## 4. 配置 `config.json`（可选，放项目根目录）

```json
{
  "chrome_path": "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "debug_port": 9222,
  "fractions": [0.06, 0.18, 0.30, 0.42, 0.54, 0.66, 0.78, 0.90],
  "thumb_width": 480,
  "thumb_quality": 4,
  "ffmpeg_timeout": 60,
  "video_timeout": 300,
  "frame_retries": 2,
  "workers": 3,
  "link_preference": "media",
  "api_delay": 0.25,
  "organize_base": "/整理",
  "organize_small_mb": 20,
  "organize_large_mb": 100,
  "organize_chunk": 8,
  "organize_delay": 1.0
}
```

- `fractions`：8 张截图的时间百分比，改数量也可（数组长度即截图数）。
- `debug_port`：调试 Chrome 端口，占用时换值。
- `link_preference`：`media`（播放直链）/ `vip`（加速下载直链）/ `web`。
- `frame_retries`：每张截图失败重试次数（每次重取新链接）。
- `workers`：**截图并发线程数**（默认 3），也可用 `shots --workers N` 或网页上的“并发”覆盖。
- `video_timeout`：**单个视频的总时限（秒，默认 300）**；超时则跳过该视频剩余帧，避免个别视频卡很久。
- `api_delay`：API 调用间隔（秒），怕风控可调大。
- `ffmpeg_timeout`：**单张截图超时秒数（默认 60）**；某帧超时**不再重试**，且**连续 2 张超时即放弃该视频剩余帧**（源自文件后半段不可 seek 时能快速跳过而不是长时间“卡住”）。
- 并发运行时，进行中的文件会显示在上方的**动态刷新区域**，原地更新、不会刷屏，方便判断是否卡住；`Ctrl+C` 会取消未开始的任务。（输出不是终端时，改为每 15 秒打印一次「进行中：…」心跳。）

---

## 5. 目录结构

程序目录（源码）：

```
/Users/tf/thunder_video_sweeper/
  sweeper                 # 统一入口脚本（用自带虚拟环境）
  .venv/                  # Python 虚拟环境
  build.sh  packaging/    # 打包脚本与资源
```

数据目录（**源码运行与打包 `.app` 默认相同**）：`~/Library/Application Support/ThunderSweeper`
（可用 `./sweeper home <路径>` 或向导菜单 `[h]` 修改；见 [3.6/5.5](#55-数据放在哪里)）

```
<数据目录>/
  config.json             # 可选配置
  .chrome-profile/        # 调试 Chrome 的独立配置（登录态在这里）
  data/
    tokens.json           登录凭证（含 refresh_token，注意保密）
    videos.json           scan 结果：视频，按大小降序
    scan_state.json       扫描进度（支持 --resume）
    classified.json       classify 结果：每个文件的分类
    files.json            扫描到的全部文件（含非视频，用于整理时的垃圾/大文件判断）
    organize_plan.json    整理方案（预览）
    organize_applied.json 整理执行记录（移动结果）
    organize_clean.json   垃圾文件夹清理记录
    duplicates.json       dedupe 结果：重复文件分组（网页会实时重算）
    classify_rules.json   自定义分类规则（分类设置页保存）
    categories.json       分类树（可自定义/含子分类；organize 按此建目录）
    manual_categories.json 手动分类结果（每文件 id→分类，自动重分类时保留）
    queue.json            shots 处理过的视频 + 截图路径
    shots_state.json     截图“已尝试”记录（--more/--all 据此跳过失败/半截的）
    thumbs/<id>/          每个视频 8 张截图
    selections.json       review 提交的待删清单
    review_progress.json  review 的进度标记
    ratings.json          视频五星评分（id→星级）
    sweeper_config.json   配置包（手动分类+评分+分类树+关键词；云端同步用）
    applied.json          apply 的执行结果
```

> `location.txt` 也在数据根目录，记录你自定义的数据目录（没有则用默认）。
> **本地磁盘模式**的扫描/分类/截图全部写在 `<数据目录>/data_local/`（云盘是 `data/`），互不干扰。


---

## 5.5 打包发布 / 直接分发给别人用（重点）

不装 Python、不装 ffmpeg，别人拿到就能用。

### 构建

```bash
cd /Users/tf/thunder_video_sweeper
./build.sh
```

产物在 `dist/`（onedir 打包，启动约 0.15 秒）：

| 产物 | 说明 |
| --- | --- |
| `dist/ThunderSweeper/` | 绿色目录，里面的 `ThunderSweeper` 就是可执行程序 |
| `dist/迅雷云盘整理助手.app` | 双击运行：自动开终端并进入菜单向导 |
| `dist/ThunderSweeper-1.0.0-macos.zip` | **对外分发的压缩包**（含上面两者） |

> 内置了静态 `ffmpeg`（来自 `imageio-ffmpeg`），用户无需 `brew install ffmpeg`。
> 打包用 onedir 而非 onefile：onefile 每次启动要把内置 ffmpeg 解包到临时目录，**要多等约 10 秒**。

### 别人怎么用

1. 解压 `ThunderSweeper-1.0.0-macos.zip`。
2. 双击 **`迅雷云盘整理助手.app`** → 打开终端菜单（首次会被 macOS 拦一下：右键 → 打开，或「系统设置 → 隐私与安全性 → 仍要打开」）。
3. 菜单选 `[1] 登录迅雷云盘`，在弹出的 Chrome 里登录一次。
4. 依次 `[2] 扫描云盘` → `[3] 自动分类` → `[4] 生成截图` → `[5] 打开管理网页`。

也可以直接用命令行（和源码版参数完全一致）：

```bash
./dist/ThunderSweeper/ThunderSweeper --help
./dist/ThunderSweeper/ThunderSweeper login
./dist/ThunderSweeper/ThunderSweeper selftest        # 自检
./dist/ThunderSweeper/ThunderSweeper selftest --live # 含真实接口沙箱测试
```

### 数据放在哪里

- **默认（源码运行与打包 `.app` 相同）**：`~/Library/Application Support/ThunderSweeper/data/`
  （macOS；Windows 为 `%APPDATA%\ThunderSweeper\data`，Linux 为 `~/.local/share/ThunderSweeper/data`）。
- **改数据目录**：向导菜单 `[h] 设置数据目录`，或 `./sweeper home <路径>`；`./sweeper home --reset` 恢复默认。
  设置写入 `<默认目录>/location.txt`，**两种运行方式共用同一份数据**。
- **优先级最高**：环境变量 `THUNDER_SWEEPER_HOME=/path/to/dir`（临时覆盖，不改设置）。
- 想在别处放浏览器配置/缓存，也支持 `THUNDER_SWEEPER_CHROME` 指定浏览器可执行文件。

### 云端配置同步（手动分类 / 评分 / 分类设置）

- 本地把「**手动分类 + 评分 + 分类树 + 关键词规则**」打包成一个配置文件 `data/sweeper_config.json`。
- 向导菜单 **`[s] 同步配置 (Sync Config)`** 打开子菜单（三个功能键）：
  - **[1] Upload**：把本地配置**上传/更新到云盘** `/config/sweeper_config.json`。上传前先显示**云端/本地时间戳**（云端没有则显示“暂无”）并让你确认；确认后覆盖云端存档；云端没有 `config` 文件夹会**自动新建**。
  - **[2] Sync**：拉取云端 `/config/sweeper_config.json`，显示**云端/本地时间戳**，确认后用**云端覆盖本地**（立即生效）。
  - **[3] Exit**：返回上一级菜单。
- 命令行等价：`./sweeper sync --upload [--yes]`（上传）、`./sweeper sync [--cloud|--local]`（拉取）。

### 每台机器的前置条件

- macOS 11+，Apple Silicon（本机构建为 arm64）。
- 装有 Chromium 系浏览器之一：**Chrome / Edge / Brave / Chromium**（会自动探测；也可在 `config.json` 指定 `chrome_path`）。
  > 首次登录需要用户在弹出的浏览器里手动登录一次（迅雷没有公开的账号密码登录接口）。

### 自检

```bash
./dist/ThunderSweeper/ThunderSweeper selftest         # 136 项离线自检
./dist/ThunderSweeper/ThunderSweeper selftest --live  # 额外 7 项真实 API 沙箱（会建/删自己的临时文件夹）
```

`--live` 会真实调用迅雷接口：创建 `/_sweeper_selftest_*` 文件夹、改名、移动、删除，**只动它自己创建的测试文件夹，绝不碰你的文件**。

---

## 6. 重置 / 清空缓存并重新加载（重点）

> **数据目录已改为 `~/Library/Application Support/ThunderSweeper`**（源码与 `.app` 相同）。
> 本节的 `data/...` 请理解为 `<数据目录>/data/...`。先设一下变量更方便：
>
> ```bash
> DATA="$HOME/Library/Application Support/ThunderSweeper/data"   # 用 ./sweeper home 可确认实际路径
> ```

按需选择级别，从轻到重：

### A. 只刷新管家页面（浏览器缓存）

- 页面上按 **⌘ + Shift + R** 强制刷新；或换个端口重开：

```bash
./sweeper review --port 8800
```

### B. 结束残留的管家服务（端口被占用）

```bash
lsof -ti :8765 | xargs kill
# 或者直接看终端提示，脚本会自动顺延到 8766/8767…
```

### C. 清空业务缓存、**保留登录**（重新扫描/分类/截图）

```bash
cd /Users/tf/thunder_video_sweeper
# 只删生成物，保留 tokens.json
rm -f data/videos.json data/scan_state.json data/classified.json \
      data/queue.json data/shots_state.json data/selections.json data/review_progress.json data/applied.json
rm -rf data/thumbs

# 然后从上一步重新来，例如：
./sweeper scan
./sweeper classify
./sweeper shots --top 20
./sweeper review
```

### D. 彻底清空所有数据（含登录态），完全重新加载

```bash
cd /Users/tf/thunder_video_sweeper

# 1) 关掉残留的调试 Chrome（只关本工具用 .chrome-profile 启的）
pkill -f thunder_video_sweeper/.chrome-profile
# 2) 关掉残留的管家服务
lsof -ti :8765 | xargs kill 2>/dev/null

# 3) 删除全部数据 + 调试 Chrome 配置（连登录一起清掉）
rm -rf data .chrome-profile .venv/__pycache__ thunder_sweeper/__pycache__

# 4) 重新登录并从零开始
./sweeper login --restart
./sweeper scan
./sweeper classify
./sweeper shots --top 20
./sweeper review
./sweeper apply
```

> 只清缓存但想保留登录：**不要删** `data/tokens.json` 和 `.chrome-profile/`。
> 想连账号登录一起重置：删 `data/` 和 `.chrome-profile/` 后重新 `./sweeper login`。

### E. 只重置“分类结果”或“选择”

```bash
rm -f data/classified.json && ./sweeper classify   # 重跑分类（保留自定义规则与手动分类）
rm -f data/classify_rules.json                      # 只清掉自定义分类规则
rm -f data/categories.json                          # 只清掉自定义分类树（恢复默认 6 类）
rm -f data/manual_categories.json                   # 只清掉手动分类（恢复全自动）
rm -f data/selections.json                          # 丢弃未提交的待删选择
rm -f data/review_progress.json                     # 清除“进度标记”
```

### F. 清空截图、全部重新开始截图（重点）

`review` 是直接扫描 `data/thumbs/` 显示已截图的，所以想“从零重截”只要清空截图目录再跑 `shots`：

```bash
cd /Users/tf/thunder_video_sweeper

# 1) 清空所有截图与索引
rm -rf data/thumbs data/queue.json data/shots_state.json

# 2) 重新截（三选一）
./sweeper shots --all        # 全部重截
./sweeper shots --top 50     # 只重截最大的 50 个
./sweeper shots --more 100   # 再截 100 个（此时等同于从头取前 100）

# 3) 打开审核页，会按大小降序排列（可切换排序）
./sweeper review
```

> 若只想**重截某几个**（保留其它），可用 `rm -rf data/thumbs/<文件id>` 删掉对应目录，再用 `./sweeper shots --ids <id1,<id2> --no-resume` 重截。
> `data/thumbs` 里的目录名就是文件 id（可用 `./sweeper inspect <序号>` 查看）。
> 只想**重试失败/半截的**视频（不动已成功的）：`./sweeper shots --all --retry-failed`（默认 `--more/--all` 会跳过尝试过的）。

---

## 7. 注意事项

- **删除是“移入回收站”**，可在迅雷云盘回收站恢复；本工具不会永久删除。
- `data/tokens.json` 等同于账号登录态，**不要泄露或提交到任何仓库**。
- 加速直链通常需要迅雷会员；非会员会退回普通直链（较慢，但也能截图）。
- 分类是**纯命名规则**推断，`未知` 与个别日/欧边界需人工复核。
- 建议先 `./sweeper shots --top 5` 跑通，再放大批量。

---

## 8. 常见问题

**Q：`./sweeper` 报缺少 `websocket`？**
用了系统 `python`。改用 `./sweeper ...`，或先 `source .venv/bin/activate`。

**Q：`review` 打不开 / 端口占用？**
脚本会自动顺延端口；或 `lsof -ti :8765 | xargs kill`；或 `--port 8800`。

**Q：`login` 报“没有找到可用的浏览器页面”？**
残留调试 Chrome 占端口。用 `./sweeper login --restart`（只关本工具的调试 Chrome）。

**Q：截图黑屏 / 花屏？**
换时间点（改 `fractions`）或重试；少数视频关键帧间距大。

**Q：截图某张很慢或失败？**
直链是短时效的，工具每张都会重取；失败会按 `frame_retries` 重试。可调大 `ffmpeg_timeout`。

**Q：点“▶ 播放”没反应 / 变成下载？**
“▶ 播放”是**页内云播**（弹层 `<video>`）；“⬇ 下载”才会下载到本地。若播放无反应，确认 `./sweeper status` 已登录并重开 `./sweeper review`（未登录时播放只读不到直链）。

**Q：整理/移动/建目录报 `captcha_invalid`（验证码无效）？**
迅雷的 `captcha_token` 是**按接口动作绑定**的（形如 `post:/drive/v1/files:batchMove`）。工具已按每个请求自动申请对应 action 的 token，并在失效时自动重申请重试，直接重跑即可；若仍报错，重新 `./sweeper login` 刷新一次凭据。

**Q：想彻底重来一遍？**
见 **第 6 节 D**：删 `data/` 与 `.chrome-profile/` 后 `./sweeper login --restart` 重新开始。
