# 第三方组件

听澜代码采用根目录的 MIT 许可证。下列依赖及模型分别遵循上游许可；项目许可证不替代上游许可或模型访问条件。实际安装版本以 `requirements/run.lock.txt` 和 `frontend/package-lock.json` 为准，模型仓库、文件及 revision 以 `backend/models.py` 为准。

## 运行组件

| 组件 | 来源 | 许可 / 用途 |
| --- | --- | --- |
| faster-whisper | [SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper) | MIT；本地语音识别 |
| CTranslate2 | [OpenNMT/CTranslate2](https://github.com/OpenNMT/CTranslate2) | MIT；推理运行时 |
| Whisper large-v3 | [openai/whisper](https://github.com/openai/whisper)、[Systran 模型](https://huggingface.co/Systran/faster-whisper-large-v3) | MIT；转录权重，使用时同时阅读模型卡及其适用限制 |
| pyannote.audio | [pyannote/pyannote-audio](https://github.com/pyannote/pyannote-audio) | MIT；说话人识别框架 |
| pyannote community-1 | [模型页面](https://huggingface.co/pyannote/speaker-diarization-community-1) | CC-BY-4.0；下载需接受模型页面访问条件，使用及再分发时遵循署名和其他许可要求 |
| PyTorch | [pytorch/pytorch](https://github.com/pytorch/pytorch) | BSD-3-Clause；张量计算及 CUDA 支持；随附运行库另有各自条款 |
| NumPy | [numpy/numpy](https://github.com/numpy/numpy) | BSD-3-Clause；音频与数值处理 |
| SoundFile | [bastibe/python-soundfile](https://github.com/bastibe/python-soundfile) | BSD-3-Clause；音频 I/O；libsndfile 遵循其自身许可 |
| FastAPI / Uvicorn | [FastAPI](https://github.com/fastapi/fastapi)、[Uvicorn](https://github.com/encode/uvicorn) | MIT / BSD-3-Clause；本地 HTTP 服务 |
| HTTPX / python-multipart | [HTTPX](https://github.com/encode/httpx)、[python-multipart](https://github.com/Kludex/python-multipart) | BSD-3-Clause / Apache-2.0；HTTP 请求与上传 |
| srt | [cdown/srt](https://github.com/cdown/srt) | MIT；SRT 字幕处理 |
| React / React DOM | [facebook/react](https://github.com/facebook/react) | MIT；网页工作台 |
| Hugging Face Hub | [huggingface/huggingface_hub](https://github.com/huggingface/huggingface_hub) | Apache-2.0；模型下载 |
| FFmpeg / FFprobe | [FFmpeg](https://ffmpeg.org/legal.html)、[Windows 构建](https://www.gyan.dev/ffmpeg/builds/) | 安装脚本下载 Windows essentials GPL 构建供本机使用；可执行文件不纳入仓库或本次分发 |

## 开发与测试

TypeScript、Vite、pytest 和 Playwright 仅用于开发、构建或测试，许可及传递依赖条款见各自上游和安装包中的 LICENSE / NOTICE。

开发阶段曾使用 [google/fleurs](https://huggingface.co/datasets/google/fleurs) 公共样片（CC-BY-4.0），相关样片、固定 revision 和来源记录保留在本机测试材料，不随仓库分发。Windows 合成语音样片用于验证导入、编辑与导出流程，不代表真实识别质量。

Qwen3-ASR、WhisperX、独立 Wav2Vec2 对齐模型与 NLTK 不是本版直接运行依赖。历史下载环境和缓存即使仍在本机，也不会上传。

## 分发边界

仓库仅分发项目源码、依赖清单和文档，不包含模型权重、FFmpeg 二进制、Python / Node 依赖安装目录或测试媒体。重新分发依赖、修改上游实现或打包安装程序前，应重新核对所用具体版本的 LICENSE / NOTICE、署名及源码提供义务；这份清单不是对所有传递依赖的完整法律审计。
