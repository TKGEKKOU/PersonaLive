# PersonaLive 应用内置 Embedding 与语音资源管理设计

日期：2026-07-31

## 目标

让 Windows 本地用户无需修改代码即可下载、选择和运行 Embedding 模型。默认使用 `Qwen/Qwen3-Embedding-0.6B`，优先通过国内 ModelScope 下载，模型统一保存到项目根目录 `models`。同时统一 ASR、TTS、Embedding 的资源管理反馈，但不改变现有 ASR/TTS 模型、推理链路、角色音色或 API 行为。

## 范围

### 本次包含

- 应用内置 Embedding 模式，保留现有通义千问和自定义 OpenAI 兼容模式。
- 默认模型 `Qwen/Qwen3-Embedding-0.6B`。
- ModelScope 和 Hugging Face 下载源，由用户明确选择；默认 ModelScope。
- 内置模型列表和自定义模型 ID，两者均不需要修改代码。
- 下载、取消、删除、打开目录、进度、速度、剩余时间、错误原因和实际运行设备。
- 默认优先 GPU；CUDA 不可用或加载失败时自动回退 CPU。
- 自动探测模型向量维度，不要求用户手填本地模型维度。
- ASR/TTS 资源区统一上述状态和交互文案，补充国内下载源及恢复提示。

### 本次不包含

- 不替换 ASR/TTS 模型或运行时。
- 不修改语音识别、语音合成、参考音色和聊天调用链。
- 不自动执行 Hugging Face 模型仓库代码，`trust_remote_code` 固定为关闭。
- 不保证任意 Hugging Face 模型兼容；仅支持标准 Transformers 或 Sentence Transformers Embedding 模型。
- 不自动迁移已有 Milvus 向量或重新入库资料。

## 方案

采用 FastAPI 主进程内的懒加载模型管理器。它与当前本地单用户架构一致，比新增常驻子服务或工作进程更容易维护。模型仅在探测维度、入库或检索时加载；切换模型后释放旧实例，再按新配置加载。

模型管理器负责四件事：

1. 将模型 ID 解析为受控的 `models` 子目录，禁止路径越界。
2. 使用所选下载源下载快照，并持续发布结构化进度。
3. 使用 Sentence Transformers 加载模型，默认尝试 CUDA，失败后回退 CPU。
4. 提供统一的 `embed_documents` 和 `embed_query` 接口，供现有 Milvus 调用链使用。

云端模式继续使用 `OpenAIEmbeddings`。本地模式由同一工厂函数返回应用内置适配器，因此索引和检索调用方不需要区分实现。

## 配置模型

新增持久化配置：

- `embedding_provider`: `qwen`、`managed_local` 或 `custom`
- `embedding_model_source`: `modelscope` 或 `huggingface`
- `embedding_model`: 模型 ID
- `embedding_device`: `auto`、`cuda` 或 `cpu`，默认 `auto`
- `embedding_dimensions`: 本地模式由探测结果写入

内置模式不显示 Base URL 和 API Key。云端、自定义模式保留现有字段和行为。

## 下载与目录

- 模型根目录固定为 `<project_root>/models`。
- 默认下载源为 ModelScope，Hugging Face 作为用户手动选择的备选源。
- 下载失败不会静默切换来源，错误信息需要指出当前来源和可选恢复方式。
- 模型 ID 会转换为稳定目录名，同时在目录内保存来源和原始 ID 元数据。
- 删除操作仅允许删除模型根目录中的受管理模型，并沿用现有确认交互。
- 打开目录使用 ASR/TTS 共用的 Windows 目录打开函数，并返回实际路径供前端展示。

## 前端交互

Embedding 区在选择“应用内置”后展示：

- 模型：默认 Qwen3 0.6B、已下载模型以及“自定义模型 ID”。
- 自定义示例：`Qwen/Qwen3-Embedding-0.6B`。
- 下载源：ModelScope / Hugging Face。
- 设备：自动（GPU 优先）/ GPU / CPU。
- 状态：未下载、下载中、已就绪、加载中、GPU、CPU 回退或错误。
- 操作：打开目录、删除模型、取消下载、下载并启用。

下载状态与 ASR/TTS 使用相同的信息顺序：当前阶段、文件或模型、已下载量、总量、速度、剩余时间。ASR/TTS 只统一布局、反馈和帮助说明，不更改原有按钮能力。

## 维度与 Milvus 门禁

本地模型下载完成后，以一条短文本执行维度探测并保存结果。入库和检索前比较当前模型维度与 Milvus Collection 维度：

- 一致：正常执行。
- 不一致：阻止入库或检索，明确提示重建 Collection 并重新导入资料。

切换模型不自动删除 Collection 或资料，避免隐式破坏用户数据。

## 错误处理

- CUDA 不可用：自动模式回退 CPU，并显示原因；强制 GPU 模式直接报错。
- CUDA 显存不足：自动模式释放失败实例后回退 CPU；强制 GPU 模式报错。
- 模型架构不兼容：显示模型 ID、来源和“不支持远程代码模型”的说明。
- 下载中断：保留下载工具可复用的缓存，下次下载继续；前端恢复为可重试状态。
- 目录打开失败：后端返回明确错误，前端保留当前资源状态并显示失败原因。

## 验证

- 单元测试覆盖配置解析、模型目录边界、来源选择、取消状态、设备回退和维度探测。
- API 测试覆盖状态、下载、取消、删除和打开目录，不下载真实大模型。
- Embedding 工厂测试确认云端模式行为不变、本地模式选择内置适配器。
- 静态前端测试覆盖新增控件和关键提示。
- 手动验证设置页三种模式切换、下载状态、目录反馈，以及 ASR/TTS 现有功能不减少。

## 验收标准

用户可以只通过设置页完成模型源选择、模型 ID 填写、下载、设备选择、启用和目录管理。默认配置可下载并运行 Qwen3-Embedding-0.6B；GPU 不可用时自动使用 CPU。现有云端 Embedding、ASR、TTS 和角色语音行为保持可用。
