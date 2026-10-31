# 开发指南

## 目录与调用链

保留现有的 Python 后端、React 前端与脚本分层，不增加新的服务层或构建框架。

| 入口 | 职责 |
| --- | --- |
| `scripts/launch.py`、`backend/config.py` | 本机端口、Windows 事件循环、运行日志及数据路径 |
| `backend/main.py` | 本机访问校验、任务与字幕 API、模型管理、设置及导出 |
| `backend/engine.py` | 本地 / 云端双通道 FIFO、检查点、暂停恢复、自动衔接及 LLM 并发 |
| `backend/worker.py`、`backend/models.py` | 隔离的本地推理子进程、模型注册与完整性校验 |
| `backend/translation.py` | 提示词、批次、上下文、响应 ID 校验、重试与拒译处理 |
| `backend/db.py` | SQLite 数据、修改历史与乐观锁 |
| `backend/media.py`、`backend/export.py` | FFmpeg、媒体路径与空间校验、字幕格式和时间区间处理 |
| `frontend/src/App.tsx`、`frontend/src/types.ts` | 工作台、队列显示、编辑草稿、连接恢复及接口类型 |

转录路径：导入 → 任务 API → 本地通道 → 模型子进程 → 检查点与字幕落库 → 可选分人 → 可选云端队列。

AI 路径：任务 API → 云端通道 → 版本与处理状态筛选 → 批次请求 → 响应校验 → 乐观锁写入 → 页面更新。

## 安装与开发

首次运行根目录 `安装.cmd`，随后可在 PowerShell 中执行：

```powershell
# 构建供 CMD 启动器使用的网页（含 TypeScript 检查）
npm.cmd --prefix frontend run build

# 启动本机服务
.venv\Scripts\python.exe scripts\launch.py
```

业务数据默认存放在 `data/`。测试或调试隔离数据时，在导入后端模块前设置 `TINGLAN_DATA`，例如：

```powershell
$env:TINGLAN_DATA = "$PWD/.tmp/manual-data"
.venv\Scripts\python.exe scripts\launch.py
```

不要把测试指向真实任务目录。测试数据、日志和中间产物统一放 `.tmp/`；不要提交本机配置、媒体、权重或 FFmpeg 二进制。

## 验证

```powershell
# 首次验证前生成合成语音样片（需 Windows 的 Microsoft Zira Desktop 语音）
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/make_sample.ps1

# 后端测试；tests/conftest.py 自动隔离到 .tmp/test-data
.venv\Scripts\python.exe -m pytest -q

# 类型检查与生产构建
npm.cmd --prefix frontend run build

# 浏览器回归；默认使用本机 Chrome，并启动 8766 端口的测试服务
npm.cmd --prefix frontend run test:e2e
```

Playwright 数据在 `.tmp/browser-data/`，截图和报告在 `.tmp/e2e/` 与 `.tmp/e2e-report/`。测试端口不要运行其他服务；配置允许复用现有测试服务，复用前应确认该服务也使用隔离数据目录。

本地回归不等同于 GPU 推理或真实付费 API 验证。涉及这些路径的修改，需另外确认硬件、样本、访问条件及费用，再做联调，不应把 mock 结果描述为实际识别或翻译质量。

## 数据与版本约定

- `backend/__init__.py` 中的 `__version__` 用于 FastAPI 应用元数据；与 `frontend/package.json`、`frontend/package-lock.json` 中的项目版本保持一致。
- 数据库中的 `version`、`source_version`、`translated_version` 是编辑冲突与译文有效性标记，不是应用版本号，不随发布改写。
- 已保存任务与历史在数据库中；队列不持久化，API 密钥保存为本地明文文件。重启前先确认无活动任务。
- 项目 MIT 许可证沿用 GitHub 仓库已有的 `LICENSE`；第三方模型及组件许可单列在 [THIRD_PARTY.md](THIRD_PARTY.md)。

## 上传前检查

1. 运行上面的回归和构建，核对 README、CHANGELOG 与实际行为。
2. 使用 `git status --short` 和 `git diff --cached --stat` 核对上传清单，阅读 `git diff --cached`；不要只依赖 `.gitignore`。
3. 确认没有 `.env`、API / Hugging Face Token、`data/`、`models/`、`record/`、`tools/`、`.venv*/`、`.tmp/`、数据库、运行日志或私有字幕。
4. 确认不包含 `node_modules/`、`frontend/dist/`、测试报告和 TypeScript 构建缓存；检查大文件，GitHub 不接收普通 Git 中超过 100 MiB 的单文件。
5. 由维护者确认提交和推送操作。1.0.0 的指定标签名是 `发布1.0.0`，标签说明使用该版本更新日志，不创建 GitHub Release 或安装包。

旧阶段计划、含私人样本信息的测试记录及探索页面仅在本机 `.tmp/repository-prep/archive/` 留档，不作为公开项目文档。

## 文档依据

- [GitHub：About READMEs](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-readmes)
- [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/)
- [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html)
