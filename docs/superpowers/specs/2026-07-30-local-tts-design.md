# PersonaLive 本地 TTS 设计

## 目标

为角色回复增加可选的本地语音合成。用户首次在应用内安装资源，之后离线使用；角色可以保存独立参考声音，生成的语音随对话历史恢复并随对话清空。

## 技术选择

- Python 独立 Worker 负责 HTTP、进程、校验和错误处理。
- MIT 许可的 `qwen3-tts.cpp` DLL 负责 GGUF 推理，支持 Vulkan 和 CPU。
- Lunar Astral Agents 仅作为行为和架构参考，不复制其非商业许可代码。
- 第一版生成完整 WAV；按句流式播放留到后续版本。

## 资源布局

```text
runtime/tts/                 qwen3tts.dll 与运行库
models/Qwen3-TTS/            主模型和 tokenizer GGUF
data/tts/voices/             角色参考音频
data/tts/cache/              speaker embedding
data/audio/assistant/        角色回复 WAV
```

模型默认从 ModelScope `qwqpotato/qwen3-tts-gguf` 下载，预编译 Windows 运行库从 PersonaLive 发布源下载。下载前展示约 2-3 GB 体积，完成后校验 SHA-256。

## 业务流程

1. 用户在设置页安装本地 TTS。
2. 用户为角色启用语音、上传或录制参考声音，并完成试听。
3. Agent 文字回复保存后，后台异步请求 TTS Worker。
4. Worker 返回二进制 WAV；服务端保存音频并更新消息状态。
5. 前端根据角色设置自动播放，或由用户手动播放。
6. TTS 失败只影响语音，不影响文字回复；允许重试。

## 安全与资源

- Worker 只监听 `127.0.0.1`，不启用 CORS，只接受项目内受控声音文件。
- 上传限制为 WAV、文件大小和音频时长，并校验真实音频内容。
- 默认按需启动、空闲退出；低显存模式避免 ASR 与 TTS 长期同时驻留。
- 清空对话删除回复音频；删除角色声音配置删除参考音频和 embedding。

## 第一版验收

- 新用户无需项目外 TTS 目录或额外 Python 环境。
- 应用内可安装、删除和检查 TTS 资源。
- 每个角色可配置独立声音、语速和自动播放。
- 回复音频可播放、可恢复、可重试并随对话删除。
- TTS 不可用时文字聊天保持正常。
