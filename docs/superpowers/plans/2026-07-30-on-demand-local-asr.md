# On-Demand Local ASR Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship all code and dependency manifests needed to enable, configure, install, reuse, and remove local Qwen3-ASR without committing model or runtime binaries.

**Architecture:** A focused ASR resource manager persists configuration under `data/asr`, resolves external/project/bundle resources, and exposes local-only FastAPI management endpoints. The existing Worker remains isolated on port 8765; the settings page controls installation and readiness, while model/runtime directories remain ignored by Git.

**Tech Stack:** Python 3.11, FastAPI, qwen-asr 0.0.6, Hugging Face Hub, vanilla JavaScript/CSS, pytest.

## Global Constraints

- Automatic downloads require an explicit user action.
- External paths are never deleted.
- Project-managed resources live only in `.asr-venv/` and `data/models/Qwen3-ASR-0.6B`.
- Existing `D:\Qwen3_ASR` remains supported as a fallback.
- No model, CUDA runtime, Python environment, or FFmpeg binary is committed.

---

### Task 1: ASR Resource Manager And API

**Files:**
- Create: `voice/asr/install.py`
- Create: `app/routers/asr.py`
- Modify: `voice/asr/local_worker.py`
- Modify: `voice/asr/worker_server.py`
- Modify: `app/main.py`
- Test: `tests/unit/test_asr_install.py`
- Test: `tests/api/test_asr_settings_api.py`

**Interfaces:**
- Produces: `ASRResourceManager.status()`, `configure()`, `install()`, and `remove_managed()`.
- Produces: `GET /api/asr/status`, `PATCH /api/asr/config`, `POST /api/asr/install`, `DELETE /api/asr/install`.
- Consumes: existing `require_local()` and local Worker manager.

- [ ] Write failing tests for config persistence, resource priority, external-directory deletion protection, and API response shapes.
- [ ] Run the two focused test modules and confirm failures are caused by missing manager/routes.
- [ ] Implement atomic JSON config writes, resource resolution, synchronous explicit installation, status reporting, and managed-resource deletion.
- [ ] Register the router and make Worker launch consume resolved paths through environment variables.
- [ ] Run focused tests and commit the backend deliverable.

### Task 2: Settings Page Controls

**Files:**
- Modify: `static/index.html`
- Modify: `static/app.js`
- Modify: `static/styles.css`
- Test: `tests/unit/test_static_voice_assets.py`

**Interfaces:**
- Consumes: the four `/api/asr/*` endpoints from Task 1.
- Produces: enable toggle, existing Python/model/FFmpeg path fields, install/remove buttons, status and download-size display.

- [ ] Extend the static asset test with assertions for controls, endpoint calls, and disabled recording while ASR is unavailable.
- [ ] Run the focused static test and confirm it fails on missing controls.
- [ ] Add the unframed settings section and bind load/configure/install/remove actions.
- [ ] Keep confirmation immediately before install and delete; refresh recording availability from `/api/asr/status`.
- [ ] Run the static test plus `node --check static/app.js` and commit the frontend deliverable.

### Task 3: Distribution Verification

**Files:**
- Modify: `README.md`
- Modify: `.gitignore`
- Modify: `voice/asr/requirements-local.txt`

**Interfaces:**
- Documents environment variables `PERSONALIVE_ASR_PYTHON`, `PERSONALIVE_ASR_MODEL`, and `PERSONALIVE_ASR_FFMPEG`.
- Guarantees generated resources are absent from Git status.

- [ ] Update dependency manifest and README with automatic-install and existing-directory workflows.
- [ ] Verify no model, environment, or FFmpeg binary is tracked.
- [ ] Run ASR unit/API/static tests and the existing voice-message flow tests.
- [ ] Check both health endpoints and perform one real WebM transcription when local resources are available.
- [ ] Commit the distribution deliverable.
