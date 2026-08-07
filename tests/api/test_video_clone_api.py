import wave
from pathlib import Path


def wav_audio(frames: bytes, rate: int = 24000) -> bytes:
    import io

    stream = io.BytesIO()
    with wave.open(stream, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(frames)
    return stream.getvalue()


def test_from_video_creates_task_and_reports_status(client, tmp_path, monkeypatch):
    persona = client.post("/api/personas", json={"name": "Clone", "profile": {}}).json()
    tasks = {"task-1": {"task_id": "task-1", "state": "running", "phase": "extract", "progress": 10}}

    class FakeManager:
        tasks_dir = tmp_path

        def start(self, video_path, apply_result):
            return "task-1"

        def get(self, task_id):
            return tasks.get(task_id)

        def cancel(self, task_id):
            return True

        def cleanup(self, task_id):
            tasks.pop(task_id, None)

    client.app.state.clone_tasks = FakeManager()
    response = client.post(
        f"/api/tts/personas/{persona['id']}/reference/from-video",
        files={"video": ("clip.mp4", b"fake-video-bytes", "video/mp4")},
        headers={"X-YUMENO-Request": "web"},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["task_id"] == "task-1"
    assert "clone-tasks" in body["status_url"]

    status = client.get("/api/tts/clone-tasks/task-1", headers={"X-YUMENO-Request": "web"})
    assert status.status_code == 200
    assert status.json()["state"] == "running"


def test_from_video_rejects_unsupported_extension(client):
    persona = client.post("/api/personas", json={"name": "Clone", "profile": {}}).json()
    response = client.post(
        f"/api/tts/personas/{persona['id']}/reference/from-video",
        files={"video": ("clip.txt", b"nope", "text/plain")},
        headers={"X-YUMENO-Request": "web"},
    )
    assert response.status_code == 415


def test_task_status_reports_failure(client, monkeypatch):
    class FakeManager:
        def get(self, task_id):
            return {
                "task_id": task_id,
                "state": "failed",
                "phase": "separate",
                "progress": 40,
                "error": "人声分离模型未安装",
            }

    client.app.state.clone_tasks = FakeManager()
    status = client.get("/api/tts/clone-tasks/task-9", headers={"X-YUMENO-Request": "web"})
    assert status.status_code == 200
    assert status.json()["state"] == "failed"
    assert status.json()["error"] == "人声分离模型未安装"


def test_separator_status_and_install_roundtrip(client, tmp_path, monkeypatch):
    from voice.separator.install import SeparatorResourceManager

    manager = SeparatorResourceManager(tmp_path)
    client.app.state.separator_resources = manager

    status = client.get("/api/tts/separator/status", headers={"X-YUMENO-Request": "web"})
    assert status.status_code == 200
    assert status.json()["installed"] is False

    installed = client.post("/api/tts/separator/install", headers={"X-YUMENO-Request": "web"})
    assert installed.status_code == 202
    assert installed.json()["installing"] is True


def test_apply_clone_result_saves_persona_reference(client, tmp_path, monkeypatch):
    from app.routers import video_clone

    persona = client.post("/api/personas", json={"name": "Clone", "profile": {}}).json()
    monkeypatch.setattr(video_clone, "VOICE_ROOT", tmp_path)
    reference = tmp_path / "reference.wav"
    reference.write_bytes(wav_audio(b"\x00\x00" * 48000))  # 1s @ 24k
    video_clone.apply_clone_result(client.app, persona["id"], reference)
    updated = client.get(f"/api/personas/{persona['id']}").json()
    assert updated["profile"]["tts"]["enabled"] is True
    assert updated["profile"]["tts"]["reference_audio"] == f"{persona['id']}.wav"
    assert (tmp_path / f"{persona['id']}.wav").is_file()
