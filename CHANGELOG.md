# Changelog

本文件记录各版本值得关注的变更，采用 [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) 的分类和倒序结构，版本号遵循 [Semantic Versioning](https://semver.org/spec/v2.0.0.html)。

## [Unreleased]

## [1.0.0] - 2026-11-01

首个正式编号版本，汇总当前可用功能。以下修复包含 1.0.0 之前本地开发阶段已经完成的工作，不代表本次仓库整理重新实现了这些功能。

### Added

- Windows CMD 安装与启动入口，以及基于 React、FastAPI 和 SQLite 的本地字幕工作台。
- 本地路径直读、原生文件多选、拖拽与网页上传，多音轨选择及内嵌字幕提取。
- Whisper large-v3 本地转录、词级时间信息、检查点，以及可选的 pyannote community-1 说话人识别。
- 本地处理与云端 AI 两条独立 FIFO 队列，按开始请求顺序执行，支持任务间转录和云端处理并行。
- 任务独立的提示词、背景、术语、说话人信息，以及可选的转录后自动润色并翻译。
- 字幕编辑、拆分与合并、历史回滚、AI 润色撤回、翻译与过期译文提示。
- 兼容 OpenAI Chat Completions 的 API 配置，批次并发、限流退避、重试和拒译拆分。
- 重叠字幕预览及 SRT、VTT、TXT、JSON 导出，未完成或过期翻译使用原文补位。
- 本机运行日志与模型子进程崩溃诊断。

### Changed

- 将项目版本统一为 1.0.0；继续通过 CMD 启动，不制作安装包或 GitHub Release。
- 整理 README、开发指南、第三方组件说明及 Git 忽略规则，保留现有前后端和脚本分层。
- 浏览器回归统一使用主 Python 环境，并同步当前设置按钮名称，移除对额外 `.venv-test` 的依赖。
- 新任务默认开启说话人识别，默认关闭自动润色并翻译；队列重启后不自动重放。

### Fixed

- Windows 启动器显式选择 Selector 事件循环，处理已确认的 Uvicorn Proactor 策略绕过问题。
- 页面断连后的状态恢复与轮询覆盖问题，保留未保存草稿并提示连接状态。
- 任务记录存在但缓存目录缺失时无法删除的问题，同时保留外置源文件保护。
- 人工编辑与 AI 写入并发时的版本冲突处理，以及导出时过期译文和重叠时间区间处理。

### Security

- 上传范围排除模型权重、FFmpeg 二进制、虚拟环境、数据库、密钥、私人录音、日志与临时产物。
- 保留本机绑定、来源校验、数据变更请求标记和安全响应头，不将工作台作为公网服务部署。

[Unreleased]: https://github.com/Terrorist1224/Tinglan/compare/发布1.0.0...HEAD
[1.0.0]: https://github.com/Terrorist1224/Tinglan/tree/发布1.0.0
