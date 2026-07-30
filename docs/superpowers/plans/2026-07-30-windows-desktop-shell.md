# Windows Desktop Shell Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a PyWebView Windows launcher and make local ASR resources self-contained under the PersonaLive application directory.

**Architecture:** Focused managers own Docker and FastAPI lifecycle; a small launcher composes them with a PyWebView window. ASR resource resolution prioritizes relative release paths, then explicit configuration, then the existing development bundle.

**Tech Stack:** Python 3.11, PyWebView, Uvicorn, FastAPI, Docker Compose, PyInstaller, pytest.

## Task 1: Desktop Lifecycle

- [ ] Add failing tests for Docker and FastAPI managers.
- [ ] Implement `desktop/docker_manager.py`, `desktop/server_manager.py`, `desktop/launcher.py`, and `desktop_main.py`.
- [ ] Add `requirements-desktop.txt` and run focused tests.

## Task 2: Self-Contained ASR Paths

- [ ] Add failing tests for application-relative runtime, model, and FFmpeg priority.
- [ ] Update ASR resource manager and Worker shutdown lifecycle.
- [ ] Add release resource manifest and Git ignore rules.
- [ ] Run ASR and voice-flow tests.

## Task 3: Verification And Documentation

- [ ] Update README with desktop development launch instructions.
- [ ] Run focused and full regression tests.
- [ ] Start the desktop dependencies and verify health plus real WebM transcription.
