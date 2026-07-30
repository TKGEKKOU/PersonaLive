# 按需安装本地 ASR 设计

## 目标

PersonaLive 仓库只包含启动本地语音识别所需的 Worker 代码、安装逻辑和依赖清单，不提交模型、Python 虚拟环境、CUDA PyTorch 或 FFmpeg 二进制。用户可以选择自动安装，也可以复用已有的 Qwen3-ASR 模型与运行环境。

## 用户体验

- 设置页提供“启用本地语音识别”开关。
- 未安装时显示预计下载体积，并提供“自动下载安装”和“使用已有目录”。
- 自动安装必须由用户主动触发，不在打开页面或首次录音时静默下载。
- 安装完成后显示就绪状态，之后可离线转写。
- 用户可删除项目自动下载的模型与独立环境；外部目录只解除关联，不删除外部文件。
- 语音功能未启用或未就绪时，录音按钮不可用并显示明确状态。

## 文件结构

- `voice/asr/worker_server.py`：加载 Qwen3-ASR-0.6B 并提供本机 HTTP 转写接口。
- `voice/asr/local_worker.py`：启动、健康检查和调用 Worker。
- `voice/asr/requirements-local.txt`：独立环境依赖清单。
- `voice/asr/install.py`：创建环境、安装依赖、下载模型和报告状态。
- `data/asr/config.json`：保存启用状态及可选外部路径。
- `data/models/`、`.asr-venv/`：自动安装产物，保持 Git 忽略。

## 运行策略

运行时按以下优先级解析资源：

1. 用户在本地 ASR 配置中指定的 Python、模型和 FFmpeg 路径。
2. 项目自动安装的 `.asr-venv`、`data/models/Qwen3-ASR-0.6B` 与系统 FFmpeg。
3. 当前机器已有的 `D:\Qwen3_ASR` 兼容目录。

找不到完整资源时不启动 Worker，而是返回“本地语音识别尚未安装”。自动安装从官方 Python 包源与 Hugging Face 下载，安装完成后模型可离线使用。

## 接口

- `GET /api/asr/status`：返回启用、安装、下载和就绪状态。
- `PATCH /api/asr/config`：启用功能或保存已有资源路径。
- `POST /api/asr/install`：用户确认后启动自动安装。
- `DELETE /api/asr/install`：删除项目管理的 ASR 环境和模型，不删除外部目录。

## 错误处理

- 区分未启用、未安装、下载失败、CUDA 不可用、FFmpeg 缺失和推理失败。
- 安装失败保留诊断信息并允许重试。
- 音频转写失败时保留音频消息，并在气泡中提供重试。

## 验证

- 单元测试覆盖资源优先级、配置读写与删除边界。
- API 测试覆盖状态、安装触发和外部目录不会被删除。
- 前端测试覆盖未安装、安装中、就绪和失败状态。
- 使用真实 WebM 验证 FFmpeg 解码和 Qwen3-ASR 转写。
