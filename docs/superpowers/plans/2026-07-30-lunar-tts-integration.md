# Lunar TTS Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the failed upstream CLI runtime with Lunar's Windows TTS service while preserving PersonaLive's existing TTS API.

**Architecture:** Vendor Lunar's isolated TTS engine source and its required license under `third_party/lunar_tts`. GitHub Actions builds the engine with MSYS2/MinGW and packages the service executable and DLL under `runtime/tts`; Python starts the loopback service and forwards synthesis requests.

**Tech Stack:** Python/FastAPI, Lunar Qwen3-TTS C++/Go service, MSYS2 UCRT64, ModelScope GGUF downloads.

## Global Constraints

- Preserve Lunar Astral Agents non-commercial license and attribution with every source and binary distribution.
- Do not commit GGUF model files, user audio, or `.tmp/` content.
- Target Windows x64; models are downloaded from ModelScope after runtime installation.
- Do not import Lunar's UI, database, WebView, or unrelated subsystems.

---

### Task 1: Replace the failed runtime build source

**Files:**
- Delete: `.github/workflows/build-tts-runtime.yml`
- Delete: `.tmp/`
- Create: `third_party/lunar_tts/`
- Create: `third_party/lunar_tts/LICENSE`
- Create: `.github/workflows/build-lunar-tts-runtime.yml`
- Modify: `README.md`

**Interfaces:**
- Consumes: `D:\Downloads\Lunar-Astral-Agents-main\subsystem\qwen3_tts_lunar`
- Produces: `personalive-lunar-tts-runtime-windows-x64.zip` containing `Qwen3_TTS_Lunar.exe`, `qwen3tts.dll`, and `LICENSE-Lunar-Astral-Agents.txt`.

- [ ] **Step 1: Add a failing packaging check**

Create `tests/unit/test_lunar_tts_runtime_layout.py`:

```python
from pathlib import Path


def test_lunar_tts_build_workflow_packages_license_and_service():
    workflow = Path(".github/workflows/build-lunar-tts-runtime.yml").read_text(encoding="utf-8")
    assert "Qwen3_TTS_Lunar.exe" in workflow
    assert "qwen3tts.dll" in workflow
    assert "LICENSE-Lunar-Astral-Agents.txt" in workflow
```

- [ ] **Step 2: Run the check and verify it fails**

Run: `pytest tests/unit/test_lunar_tts_runtime_layout.py -q`

Expected: FAIL because the Lunar workflow does not exist.

- [ ] **Step 3: Import the isolated Lunar TTS source and license**

Copy `cpp/`, `module/`, `main.go`, `server.go`, `go.mod`, `go.sum`, `build.ps1`, `build_cpp.ps1`, and `build_ggml.ps1` from the downloaded Lunar subsystem. Copy the root Lunar `LICENSE` as `third_party/lunar_tts/LICENSE`. Do not copy `local_data`, UI assets, or other subsystems.

- [ ] **Step 4: Add the MinGW build workflow**

Configure MSYS2 UCRT64 with GCC, CMake, Ninja, and Go. Build the imported source without Vulkan, package the EXE, DLL, and license, then publish the ZIP release artifact.

- [ ] **Step 5: Verify and commit**

Run: `pytest tests/unit/test_lunar_tts_runtime_layout.py -q`

Expected: PASS.

Commit: `git commit -m "feat: vendor Lunar TTS runtime source"`

### Task 2: Adapt the local Python worker to the Lunar service

**Files:**
- Modify: `voice/tts/install.py`
- Modify: `voice/tts/local_worker.py`
- Modify: `tests/unit/test_local_tts_worker.py`
- Modify: `tests/unit/test_tts_install.py`

**Interfaces:**
- Consumes: `runtime/tts/Qwen3_TTS_Lunar.exe`, `runtime/tts/qwen3tts.dll`, `models/Qwen3-TTS`.
- Produces: `LocalTTS.synthesize(text: str, output: Path, reference_audio: Path | None) -> Path`.

- [ ] **Step 1: Update the worker test for HTTP synthesis**

Replace the CLI command assertion with a fake local HTTP response that contains base64 WAV data, then assert `output.read_bytes() == b"RIFFaudio"`.

- [ ] **Step 2: Run the worker test and verify it fails**

Run: `pytest tests/unit/test_local_tts_worker.py -q`

Expected: FAIL because `LocalTTS` still invokes `qwen3-tts-cli.exe`.

- [ ] **Step 3: Implement service lifecycle and request forwarding**

Change runtime discovery to require both `Qwen3_TTS_Lunar.exe` and `qwen3tts.dll`. Start the executable with a loopback port and model directory, wait for `/health`, then POST JSON containing `text` and the optional reference WAV path to its synthesis endpoint. Decode the returned base64 WAV into `output`; surface startup, HTTP, and response errors as `TTSGenerationError`.

- [ ] **Step 4: Keep installation model-only**

Update `TTSResourceManager.status()` and `_install()` to report the Lunar EXE and DLL as bundled resources while downloading only the two GGUF files from ModelScope.

- [ ] **Step 5: Verify and commit**

Run: `pytest tests/unit/test_local_tts_worker.py tests/unit/test_tts_install.py -q`

Expected: PASS.

Commit: `git commit -m "feat: run Lunar TTS service locally"`

### Task 3: Document attribution and validate the existing TTS API

**Files:**
- Modify: `README.md`
- Modify: `tests/api/test_tts_synthesis.py`

**Interfaces:**
- Consumes: the unchanged `POST /api/tts/personas/{persona_id}/conversations/{conversation_id}/synthesize` endpoint.
- Produces: assistant audio messages stored under PersonaLive's existing data directory.

- [ ] **Step 1: Add an attribution assertion**

Add a test that asserts `README.md` contains `Lunar Astral Agents` and `Non-Commercial License`.

- [ ] **Step 2: Run the targeted API test**

Run: `pytest tests/api/test_tts_synthesis.py -q`

Expected: PASS because the router interface is intentionally unchanged.

- [ ] **Step 3: Add user-facing attribution**

Document that local TTS is powered by the imported Lunar component, is non-commercial, has its included license in `third_party/lunar_tts/LICENSE`, and still downloads models separately from ModelScope.

- [ ] **Step 4: Run verification and commit**

Run: `pytest tests/unit/test_lunar_tts_runtime_layout.py tests/unit/test_local_tts_worker.py tests/unit/test_tts_install.py tests/api/test_tts_synthesis.py -q`

Expected: PASS.

Commit: `git commit -m "docs: disclose Lunar TTS license"`
