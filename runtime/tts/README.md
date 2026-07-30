# Bundled TTS Runtime

Windows releases must include `qwen3-tts-cli.exe` in this directory.
The executable is built from the pinned MIT `qwen3-tts.cpp` revision documented
in `THIRD_PARTY_NOTICES.md`. GGUF model files are not bundled and are downloaded
on demand into `models/Qwen3-TTS`.
