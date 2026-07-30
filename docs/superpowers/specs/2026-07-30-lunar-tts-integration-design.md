# Lunar TTS Integration

## Scope

PersonaLive integrates the local Qwen3-TTS subsystem from Lunar Astral Agents for Windows. It keeps the existing FastAPI endpoints and frontend behavior: each persona can have a reference WAV, assistant replies may be synthesized, and generated audio is stored with its conversation.

## Runtime

The bundled runtime is built from Lunar's `subsystem/qwen3_tts_lunar` source with its MinGW build path. The runtime provides `Qwen3_TTS_Lunar.exe` and `qwen3tts.dll`; PersonaLive starts the local service only when synthesis is requested and sends requests to its loopback HTTP API. Qwen3-TTS GGUF model files remain managed resources downloaded from ModelScope on the user's first installation.

## Boundaries

Only the TTS subsystem and its Windows build configuration are imported. PersonaLive does not import Lunar's larger application, WebView shell, data stores, or UI. The existing PersonaLive TTS router remains the public API boundary, so no browser-facing API changes are required.

## Licensing

The imported source, runtime binaries, and packaged Windows application include Lunar Astral Agents' non-commercial license and attribution. PersonaLive documentation identifies this as a non-commercial TTS component. Model files are not committed to GitHub.

## Failure Handling

Runtime status distinguishes missing runtime, missing model files, service startup failure, and synthesis errors. Installation progress remains visible in the existing settings UI. The service is loopback-only and is shut down with PersonaLive.

## Verification

Tests cover runtime discovery, service command construction, HTTP synthesis response handling, and preservation of existing persona audio storage behavior. CI builds the imported MinGW runtime and publishes it as the reusable Windows artifact.
