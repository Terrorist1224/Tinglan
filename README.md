# 听澜字幕 Tinglan

Windows 本地字幕工作台：**导入音视频 → 本地转录与说话人识别 → 校对与 AI 润色 → 翻译 → 导出字幕**。

当前版本：**1.0.1**。通过 CMD 启动，在本机浏览器使用，不需要安装包。音视频转录和说话人识别在本地运行；AI 润色与翻译使用你配置的兼容 OpenAI Chat Completions API。

## 功能

- **多文件导入**：支持原生文件多选、网页上传、拖拽和单个绝对路径导入。直接导入引用原文件，不复制大视频；支持选择音轨和提取内嵌字幕。
- **本地转录**：使用 Whisper large-v3，支持 CUDA 或手动选择 CPU，保存词级时间信息及检查点。
- **说话人识别**：使用 pyannote community-1，可设置说话人数、名称、备注和单句归属。新任务默认开启，可按需关闭。
- **双通道排队**：本地处理与云端 AI 各有一条 FIFO 队列，按点击开始的顺序运行；不同任务可分别占用两个通道。云端任务内部保留批次并发、限流退避与重试。
- **字幕校对**：支持修改文本、时间与说话人，拆分、合并、查看历史及回滚；AI 结果写入时检查版本，避免覆盖同时进行的人工修改。
- **AI 润色与翻译**：支持全部或所选字幕、任务独立提示词、背景与术语、撤回润色、过期译文提示，以及拒译拆分和人工处理提示。自动润色并翻译默认关闭，避免未经选择产生 API 费用。
- **状态标签与筛选**：展示润色被拒、模型拦截、时间异常、说话人待校对等标记，可按分类筛选；筛选后点击“全选”，再对所选字幕批量润色或翻译。未选择字幕时批量操作仍处理全部。
- **预览与导出**：支持重叠字幕预览，播放失败时显示提示；前端不再提供兼容预览生成入口。导出整个任务为 SRT、VTT、TXT 或 JSON，未翻译或过期译文使用当前原文补位，起止时间相同的历史字幕在导出副本中延展 300ms。

## 环境要求

| 项目 | 要求 |
| --- | --- |
| 操作系统 | Windows 10 / 11，64 位；当前安装和启动脚本以 Windows 为目标 |
| Python | Python 3.12，包含 Tkinter（原生文件选择器使用） |
| Node.js | 22.12 或更新的受支持 LTS 版本，包含 npm |
| Python 包管理 | [uv](https://docs.astral.sh/uv/getting-started/installation/) |
| 浏览器 | 当前版本的 Chrome 或 Edge |
| GPU | 推荐支持 CUDA 12.8 运行时的 NVIDIA 显卡及兼容驱动；无 CUDA 时需在任务设置中明确选择 CPU，速度取决于硬件 |
| 磁盘与网络 | Python/CUDA 依赖及模型需要数 GB 以上空间，另需为媒体缓存留出空间；首次安装和下载模型需要联网 |

仓库不包含虚拟环境、模型、FFmpeg 可执行文件和前端构建产物。Python 运行依赖以 `requirements/run.lock.txt` 为准，公共核心锁在 `requirements/core.lock.txt`，测试依赖在 `requirements/dev.lock.txt`；前端依赖以 `frontend/package-lock.json` 为准。

## 快速开始

### 1. 获取代码并安装

下载仓库源码并解压，或在已安装 Git 的终端执行：

```powershell
git clone https://github.com/Terrorist1224/Tinglan.git
cd Tinglan
```

确认 `python`、`uv` 和 `npm.cmd` 可用，然后双击 **`安装.cmd`**。安装脚本会：

1. 检查环境、磁盘和运行中的进程，创建项目内的 `.venv`，按锁文件同步 Python 运行依赖，移除清单外的包。
2. 下载并校验 FFmpeg / FFprobe 到 `tools/`（已存在时验证可运行，不覆盖）。
3. 使用 `npm ci` 安装前端依赖，在临时目录构建成功后替换 `frontend/dist/`，保留上一份产物。

安装前先保存修改并停止服务、模型任务和测试。新安装至少预留 16 GiB，已有环境更新至少预留 4 GiB，用于依赖下载、缓存和构建。如果安装失败，查看命令窗口和 `.tmp/setup/<本次安装>/install.log`，修正环境或网络后重新运行。安装过程会连接 PyPI、PyTorch、npm 和 FFmpeg 下载站点。

### 2. 启动与准备模型

双击 **`启动.cmd`**，等待浏览器打开 <http://127.0.0.1:8765>。保留命令窗口，按 `Ctrl+C` 停止服务。

点击顶部“全局 API 设置”，在模型区域下载或从本地目录导入模型：

- **Whisper large-v3**：转录所需，注册表估计约 3.1 GB。
- **pyannote community-1**：说话人识别所需。下载前需在 [模型页面](https://huggingface.co/pyannote/speaker-diarization-community-1)接受访问条件，并在界面输入有读取权限的 Hugging Face Token。如果暂不使用，请关闭新任务的自动分人。

模型清单及 revision 见 `backend/models.py`；本地导入也会校验必须文件并生成完整性清单。模型权重不随本仓库分发。

### 3. 创建任务并导出

1. 导入音视频，确认音轨、语言、设备和是否自动分人，再点击开始。
2. 如需 AI 润色或翻译，在设置中填写 API 基础地址、模型名称和密钥。费用与数据处理规则取决于你选择的提供方。
3. 校对字幕，按需润色、翻译并预览，选择格式导出。

导入不会自动开始。队列在内存中维护，重启后不重放；中断任务需手动恢复，也不会自动重发付费请求。已保存的任务、字幕和修改历史保存在本机。未保存的页面草稿不保证在关闭或刷新后保留。

## 数据与隐私

- 本地转录与分人不上传音视频；AI 润色与翻译会把字幕及请求所带的上下文、背景和术语发送给你配置的 API。敏感内容使用前请确认提供方的数据政策。
- 任务数据库、媒体缓存及设置在 `data/`。填写并保存 API 密钥后，密钥以**本地明文文件** `data/api.key` 保存，当前界面没有仅会话保存开关。不要共享整个项目运行目录。
- 直接路径导入不会修改原文件；删除任务会清理听澜自己的记录和缓存，不会删除外置源文件。原文件移动或改名后，需要恢复路径或重新导入。
- 服务默认只绑定 `127.0.0.1:8765`，面向单机使用，不要直接暴露到公网。
- `.gitignore` 排除模型、用户数据、密钥、虚拟环境、日志、录音、缓存和构建产物；提交前仍需检查实际暂存内容，忽略规则不能清除已经进入 Git 历史的文件。

## 项目结构

```text
Tinglan/
├── backend/                 # SQLite、任务调度、本地推理、云端处理与导出
│   └── routes/              # FastAPI 路由，按任务、字幕、设置与资源拆分
├── frontend/
│   ├── src/                 # React + TypeScript，组件与共享状态 Hook
│   └── tests/               # Playwright 浏览器测试
├── scripts/                 # 安装、CMD 启动器、模型下载与样片工具
├── tests/                   # pytest 后端与回归测试
├── docs/
│   ├── DEVELOPMENT.md       # 开发导航、验证命令与上传检查
│   └── THIRD_PARTY.md       # 第三方组件与许可说明
├── 安装.cmd
├── 启动.cmd
├── requirements/            # Python 依赖清单与固定锁文件（core / run / dev）
├── pyproject.toml           # pytest 收集与临时目录配置
├── CHANGELOG.md
└── LICENSE
```

运行时生成的 `data/`、`models/`、`tools/`、`.venv/`、`.tmp/`、`frontend/node_modules/` 和 `frontend/dist/` 均不上传。

## 开发、更新与帮助

- 新开发者接手、修改导航、关键约定、故障定位和验证步骤：[开发指南](docs/DEVELOPMENT.md)。开发环境运行 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/install.ps1 -Dev`，随后用 `scripts/check.ps1` 执行完整自动回归。
- 空间盘点：`.venv\Scripts\python.exe scripts\space.py`。报告会识别硬链接，只列清理候选，不删除文件。模型和 GPU 依赖需要数 GB，拆分代码不会减少这些运行资源。
- 版本记录：[CHANGELOG](CHANGELOG.md)。
- 问题反馈：[GitHub Issues](https://github.com/Terrorist1224/Tinglan/issues)。请附复现步骤和已脱敏的错误，勿上传真实密钥、私人字幕、录音或整个 `data/`。
- 更新前先保存修改并停止服务，再运行 `安装.cmd` 同步依赖并构建，完成后重新启动服务并刷新浏览器。开发环境使用 `scripts/install.ps1 -Dev`；普通安装不保留测试依赖。重要数据请先备份 `data/`，备份同样包含隐私与密钥，不要上传。

## 许可证

项目代码采用 [MIT License](LICENSE)。模型权重、FFmpeg 和其他第三方组件遵循各自的许可及访问条件，详见[第三方说明](docs/THIRD_PARTY.md)。
