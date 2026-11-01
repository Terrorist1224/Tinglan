# 开发指南

## 新接手者先做什么

1. 先读根目录 README 的环境要求与快速开始，再看下方目录表和修改导航。安装脚本以 Windows 为目标。
2. 用 `scripts/install.ps1 -Dev` 准备完整开发环境，运行 `scripts/check.ps1`，先确认原有测试通过再改代码。
3. 手动试用时先设置 `TINGLAN_DATA` 指向 `.tmp/manual-data/`，用合成样片走一遍导入、编辑、导出；不要使用真实用户库做练习。
4. 遇到问题按下方故障表找日志。涉及转录质量、分人或真实云端服务的修改，还要做对应的专项验收。

接手所需的约定以本仓库文档、代码和测试为准。历史操作快照位于 `.tmp/`，不应成为运行或理解项目的前提。

## 目录与调用链

沿用 Python 后端、React 前端和 Windows 脚本，不增加服务框架、全局状态库或第二套永久环境。

| 入口 | 职责 |
| --- | --- |
| `backend/main.py`、`backend/routes/`、`backend/schemas.py` | 应用组装、访问校验、HTTP 路由与请求模型 |
| `backend/settings.py`、`backend/config.py` | 设置与密钥读写、离线路径和当前 Python 解释器 |
| `backend/engine.py` | 双通道 FIFO、唯一队列状态、任务生命周期及自动衔接 |
| `backend/local_processing.py`、`backend/cloud_processing.py` | 本地子进程协议与结果落库、云端批次及并发退避 |
| `backend/worker.py`、`backend/models.py` | 隔离推理、模型注册与完整性校验 |
| `backend/db.py`、`backend/media.py`、`backend/export.py` | SQLite、媒体处理、字幕导出 |
| `frontend/src/App.tsx`、`frontend/src/hooks/useWorkspace.ts` | 页面装配、工作台状态及请求时序 |
| `frontend/src/components/` | 设置、导入、任务列表、状态、播放器和字幕编辑组件 |
| `frontend/src/api.ts`、`types.ts`、`constants.ts`、`format.ts` | HTTP 请求、接口类型、显示常量和时间格式化 |
| `scripts/install.ps1`、`check.ps1`、`space.py` | 安装、统一验证和只读空间盘点 |

转录路径：任务 API → 本地通道 → 模型子进程 → 检查点与字幕落库 → 可选分人 → 可选云端队列。

云端路径：任务 API → 云端通道 → 批次请求 → 响应校验 → 乐观锁写入。队列与锁只归 Engine 所有，处理模块不再反向导入 HTTP 入口。

## 修改导航

| 要改什么 | 先读这些实现 | 相关回归 |
| --- | --- | --- |
| 布局、按钮、字幕编辑显示 | `frontend/src/App.tsx`、`components/`、`style.css` | `frontend/tests/workspace.spec.ts` |
| 自动保存、断线恢复、切换任务 | `frontend/src/hooks/useWorkspace.ts`、`api.ts` | `frontend/tests/connection.spec.ts` |
| 导入、文件所有权、删除或缓存 | `backend/routes/tasks.py`、`backend/media.py` | `tests/test_media.py`、`tests/test_features.py` |
| 队列、暂停、重启、自动衔接 | `backend/engine.py` | `tests/test_queue_scheduler.py`、`tests/test_auto_translation.py` |
| 转录、检查点、分人 | `backend/worker.py`、`backend/local_processing.py` | `tests/test_worker.py`、`tests/test_core.py`、真实语音专项 |
| 云端批次、拒译、重试 | `backend/cloud_processing.py`、`backend/translation.py` | `tests/test_concurrency.py`、`tests/test_id_mismatch_recovery.py`、`tests/test_model_only_translation.py` |
| 编辑冲突、译文过期、撤回 | `backend/routes/subtitles.py`、`backend/db.py`、`frontend/src/types.ts` | `tests/test_core.py`、`tests/test_features.py` |
| 安装、启动、空间、模块边界 | `scripts/`、`backend/config.py`、`requirements/` | `tests/test_launch.py`、`tests/test_crash_diagnostics.py`、`tests/test_maintenance.py`、`tests/test_architecture.py` |

路由按资源分组：`tasks.py` 负责任务与媒体，`subtitles.py` 负责字幕与历史，`settings.py` 负责设置与上游模型列表，`resources.py` 负责系统能力与本地模型。请求结构在 `backend/schemas.py`；运行后可在同源的 `/docs` 查看 API，固定契约在 `tests/fixtures/api-contract.json`。

## 安装与开发

普通运行环境双击根目录 `安装.cmd`。开发环境使用同一 `.venv`，同时同步完整运行依赖和测试依赖：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/install.ps1 -Dev
npm.cmd --prefix frontend run build
.venv\Scripts\python.exe scripts\launch.py
```

安装前保存修改，停止服务、模型处理和测试。脚本检查运行中的项目 Python / FFmpeg 进程；发现仍在使用时拒绝修改环境。新安装至少预留 16 GiB，更新至少预留 4 GiB。

Python 使用 `uv pip sync`，会卸载所选锁文件之外的包。完整运行依赖为核心与 GPU 推理；开发锁只含核心与测试依赖，**单独同步开发锁会移除 GPU 包，仅供无推理 CI 使用**。本机开发使用 `-Dev`，不要把开发锁当成完整运行锁。

安装日志、旧版本清单和上一份前端产物位于 `.tmp/setup/<本次安装>/`。下载使用临时文件；FFmpeg 校验完成后才安装。前端构建成功后才切换，切换失败恢复旧产物。Python 同步失败会留下版本记录和错误，不承诺自动还原整个环境。

业务数据默认在 `data/`。隔离调试时，在导入后端之前设置：

```powershell
$env:TINGLAN_DATA = "$PWD/.tmp/manual-data"
.venv\Scripts\python.exe scripts\launch.py
```

不要把测试指向真实任务目录。模型、数据、密钥和用户源文件不参与代码重构迁移。`TINGLAN_DATA` 在导入 `backend.config` 时读取，改完需重新启动 Python 进程；它只隔离业务数据，模型仍在项目 `models/`，运行日志仍在项目 `.tmp/`。

页面由 FastAPI 提供 `frontend/dist/`，前端修改后运行 `npm.cmd --prefix frontend run build`，重启后端并刷新浏览器；重启能确保首次新增的静态资源目录被挂载。后端修改也需重启。`frontend` 的 `dev` 命令只启动 Vite，目前没有 API 代理配置；完整联调用上述同源构建流程，避免本机来源校验拒绝跨端口请求。

## 验证

统一入口会生成确定性的合成音视频，然后依次运行后端、类型检查与构建、浏览器回归；任一步失败即停止：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/check.ps1
```

单独验证：

```powershell
.venv\Scripts\python.exe scripts\make_fixtures.py
.venv\Scripts\python.exe -m pytest -q
npm.cmd --prefix frontend run build
npm.cmd --prefix frontend test
```

`npm test` 与 `npm run test:e2e` 均执行 Playwright。测试使用本机 Chrome，启动独立的 8766 端口服务；端口占用时失败，不复用未知服务。路径带空格的项目也应可运行。

pytest 只收集 `tests/`，数据、缓存和临时目录在 `.tmp/test-data/`、`.tmp/pytest-cache/`、`.tmp/pytest-tmp/`。浏览器数据在 `.tmp/browser-data/`，截图和报告在 `.tmp/e2e/`、`.tmp/e2e-report/`；统一检查日志在 `.tmp/check/`。

接口契约固定在 `tests/fixtures/api-contract.json`，结构重构不得改变。只有明确批准 API 变化后才能更新该文件，不得为消除失败覆盖基准。

合成音视频没有语音，只用于媒体、界面和业务流程测试。真实转录另用 `scripts/make_sample.ps1` 生成公开文本语音（需要 Windows Zira 语音），保留独立副本；固定模型、设备、参数，对比字幕文本、分段、词级时间戳和检查点。不要用正弦音频验收识别效果。

云端自动回归使用模拟响应，不发送付费请求。合成样片和模拟模型无法验收真实识别、分人质量或上游服务可用性；修改这些链路时，用独立数据、固定模型与参数做专项验收，并记录样本与结果。

`.github/workflows/checks.yml` 在 Windows 上安装核心与测试依赖，运行同一检查入口；不下载 Torch、模型或真实凭证。工作流配置可以本地评审，未经批准不推送或触发远端任务。

## 维护时必须保留的约定

- **队列所有权：** 只有 Engine 管理两条通道和调度锁。本地通道保护模型进程与资源，云端通道内再做批次并发；不同任务可同时占用不同通道，同一任务的本地阶段结束后才交给云端。
- **恢复边界：** 内存队列重启后清空，数据库中 `running` / `queued` 改为 `interrupted`。`params.operation` 保存云端目标、完成项、拒译项与配置快照；恢复时还要检查原文版本，不能直接相信旧的完成清单，也不能自动重放付费调用。
- **模型子进程协议：** Engine → `local_processing.run_worker()` → `python -m backend.worker`；输入在任务目录的 `worker-input.json`，stdout 的 `TINGLAN:` 前缀行是 JSON 事件，stderr 是阶段日志。增加事件时同步检查生产者、消费者与恢复测试。`unit` 的字幕和检查点必须在同一事务提交。
- **时间与版本：** 字幕接口、数据库和词级时间均使用毫秒，播放器使用秒。`version` 防止覆盖整条记录，`source_version` 跟随原文变化，`translated_version` 表示译文对应的原文版本；后两者不同则译文过期。人工译文标记也要保留。
- **草稿与响应时序：** `useWorkspace` 只在 App 调用一次，子组件通过 props 共用状态；更新草稿时同步 state/ref。保存期间的新编辑不能被旧响应覆盖，选择序号使旧任务响应失效，保存失败草稿不能被轮询替换，失败后等待显式重试。
- **本地安全与文件所有权：** 保留同源检查、允许的 Host 和写请求 `X-Tinglan: 1` 标记。绝对路径导入只引用外部文件；删除任务只清理任务目录及记录，不删除外部媒体。不得将任意请求路径直接交给删除操作。
- **模块依赖：** routes 可调用 settings、Engine 和 db；处理模块通过回调连接调度器，不反向导入 `backend.main`。新增接口同步检查请求 schema、前端类型、调用方和契约，避免只改一端。

## 故障定位

| 现象 | 先检查什么 |
| --- | --- |
| 安装或更新失败 | 命令窗口、`.tmp/setup/<本次安装>/install.log`；确认磁盘、网络、Python/uv/npm 和占用中的进程 |
| 启动退出或原生崩溃 | 启动窗口给出的 `.tmp/runtime/<本次启动>/runtime.log`；后端未捕获异常与线程堆栈在此记录 |
| 某任务转录/分人失败 | 当前 `TINGLAN_DATA/<task_id>/transcribe.log` 或 `diarize.log`；再看任务错误、`metrics.jsonl` 和模型完整性 |
| 页面没有反映修改或根路径 503 | `frontend/dist/` 是否构建成功；构建后重启后端并刷新页面 |
| 页面断线、403、端口占用 | 启动窗口、浏览器 Network、是否访问 `127.0.0.1:8765` 同源页面；不要通过关闭安全校验解决跨端口请求 |
| 字幕提示冲突或译文过期 | 对照当前数据库版本与发起请求时版本；保留人工草稿，重新确认内容后保存，不能强行覆盖版本号 |
| 云端 401/403、429 或模型拒译 | 任务错误与进度，分别核对凭证、限流退避和拒译项；自动回归的模拟响应不能证明真实服务可用 |
| 自动回归失败 | `.tmp/check/<本次检查>/check.log`、`.tmp/e2e-report/` 和 `.tmp/e2e/`，先读第一个失败步骤 |

`data/api.key`（或隔离数据目录中的 `api.key`）是本地明文密钥；数据库、字幕、媒体、阶段日志和诊断材料都可能包含私人内容。共享复现时使用合成样片和隔离数据，日志先脱敏；不要把整个数据目录或 `.tmp/` 发给接手者。

## 依赖与空间维护

依赖直接声明放在三个 `.in` 文件中，锁文件保留固定版本。变更依赖时，在隔离目录生成候选锁并评审差异；核心锁、运行锁和开发锁一起更新，不把升级混入结构调整。可用 uv 编译各入口，再核对组合后的版本与依赖完整性。

```powershell
# 当前本机开发环境的同步预演，不修改已安装软件包
uv pip sync --python .venv/Scripts/python.exe requirements/run.lock.txt requirements/dev.lock.txt --dry-run
uv pip check --python .venv/Scripts/python.exe

# 只读空间盘点
.venv\Scripts\python.exe scripts\space.py
.venv\Scripts\python.exe scripts\space.py --json > .tmp/space-report.json
```

空间报告区分逻辑大小、按文件身份去重的内容大小、候选目录可回收的文件内容；这些不等于 NTFS 分配块的精确占用。报告跳过符号链接与 junction，读不到文件会报错并返回非零。仍被候选之外路径引用的硬链接不算可回收。

盘点不会执行删除。清理前核对完整路径、进程与本次授权；保留 `data/`、`models/`、`record/`、历史 benchmark、诊断材料和恢复快照，不清空整个 `.tmp/`。不手删 `site-packages` 内的 DLL、LIB 或其他包文件。

## 数据与版本约定

- `backend/__init__.py`、前端 package 与 lock 文件的应用版本保持一致；结构重构不自动改版本号。
- 字幕 `version`、`source_version`、`translated_version` 用于编辑冲突与译文有效性，不是应用版本号。
- 队列不持久化；重启不重放任务、不自动重发付费请求。API 密钥保持现有本地明文保存方式。
- 数据库初始化先检查缺失列，只执行必要的历史兼容补列；其他 SQLite 错误应上报。
- 重构前使用 SQLite 备份接口备份数据库，并在隔离副本验证；不要把正在使用的主文件和 WAL 随意拼接成备份。
- 恢复材料留在项目 `.tmp/`，先验证再清理。使用对应快照恢复文件和受影响包，不用未经批准的 Git 写操作回滚。

## 上传前检查

1. 完成统一回归与相关真实验收，确认 README、CHANGELOG 与实际行为一致。
2. 使用 `git status --short`、`git diff` 阅读变更；暂存、提交、推送和标签均需具体批准。
3. 不包含密钥、真实数据、录音、权重、FFmpeg、环境、依赖目录、编译产物、日志或本机快照。
4. 共享错误和截图前核对敏感内容；不要上传整个 `data/` 或 `.tmp/`。
5. 不自动创建 Release、安装包或发布标签。

项目代码使用原有 MIT 许可证；第三方组件另见 [THIRD_PARTY.md](THIRD_PARTY.md)。
