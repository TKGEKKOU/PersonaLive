import io
import wave
from pathlib import Path


def wav_bytes(frames: bytes = b"\x00\x00" * 24000, rate: int = 24000) -> bytes:
    stream = io.BytesIO()
    with wave.open(stream, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(frames)
    return stream.getvalue()


def install_manager(client, tmp_path, monkeypatch):
    from voice.studio import VoiceStudioManager

    manager = VoiceStudioManager(tmp_path, separator_factory=lambda: None, vad_factory=lambda: None, voices_root=tmp_path / "voices")
    client.app.state.voice_studio = manager
    return manager


def fake_segments_result(session_dir: Path, audio_wav):
    """Craft a pipeline result with two persisted segment wavs."""
    segments_dir = session_dir / "segments"
    segments_dir.mkdir(parents=True, exist_ok=True)
    segments = []
    for index, seconds in enumerate([3.0, 5.0]):
        path = segments_dir / f"segment_{index + 1:03d}.wav"
        path.write_bytes(wav_bytes(b"\x00\x00" * int(24000 * seconds)))
        segments.append({"index": index, "seconds": seconds, "rms": 0.3 + index, "start_24k": 0, "end_24k": int(24000 * seconds), "file": path.name, "source": "auto"})
    return {"audio_44k": audio_wav, "segments": segments}


def patch_ffmpeg(monkeypatch):
    import voice.studio as studio_module

    bundled = Path("runtime/ffmpeg/ffmpeg.exe")
    monkeypatch.setattr(studio_module, "find_ffmpeg", lambda _root: bundled.resolve())


def test_session_lifecycle_and_video_flow(client, tmp_path, monkeypatch):
    headers = {"X-YUMENO-Request": "web"}
    manager = install_manager(client, tmp_path, monkeypatch)
    audio = tmp_path / "audio_44k.wav"
    audio.write_bytes(wav_bytes(rate=44100))

    def fake_run(session_id, video_path, cancel):
        manager._store_segments(session_id, fake_segments_result(manager._session_dir(session_id), audio))

    monkeypatch.setattr(manager, "_run_video", fake_run)

    created = client.post("/api/voice-studio/sessions", headers=headers)
    assert created.status_code == 201
    session_id = created.json()["session_id"]
    assert client.get(f"/api/voice-studio/sessions/{session_id}", headers=headers).json()["phase"] == "idle"

    started = client.post(
        f"/api/voice-studio/sessions/{session_id}/video",
        files={"video": ("clip.mp4", b"fake-video", "video/mp4")},
        headers=headers,
    )
    assert started.status_code == 202

    import time

    for _ in range(50):
        state = client.get(f"/api/voice-studio/sessions/{session_id}", headers=headers).json()
        if state["phase"] == "segments":
            break
        time.sleep(0.05)
    assert state["phase"] == "segments"
    assert len(state["segments"]) == 2

    segment = client.get(f"/api/voice-studio/sessions/{session_id}/segments/0/audio", headers=headers)
    assert segment.status_code == 200
    assert segment.headers["content-type"] == "audio/wav"

    selected = client.post(
        f"/api/voice-studio/sessions/{session_id}/segments/select",
        json={"indices": [0, 1]},
        headers=headers,
    )
    assert selected.status_code == 200
    assert selected.json()["reference_seconds"] == 8.0

    completed = client.post(
        f"/api/voice-studio/sessions/{session_id}/complete",
        json={"name": "月华温柔音"},
        headers=headers,
    )
    assert completed.status_code == 200
    voice_id = completed.json()["voice_id"]
    voices = client.get("/api/voice-studio/voices", headers=headers).json()["voices"]
    assert voices[0]["name"] == "月华温柔音"
    assert (tmp_path / "voices" / f"{voice_id}.wav").is_file()

    play = client.get(f"/api/voice-studio/voices/{voice_id}/audio", headers=headers)
    assert play.status_code == 200

    removed = client.delete(f"/api/voice-studio/voices/{voice_id}", headers=headers)
    assert removed.json()["deleted"] is True
    assert client.get("/api/voice-studio/voices", headers=headers).json()["voices"] == []


def test_audio_upload_requires_separate_confirm(client, tmp_path, monkeypatch):
    headers = {"X-YUMENO-Request": "web"}
    patch_ffmpeg(monkeypatch)
    manager = install_manager(client, tmp_path, monkeypatch)
    audio = tmp_path / "audio_44k.wav"
    audio.write_bytes(wav_bytes(rate=44100))

    def fake_convert(session_id, audio_paths, cancel):
        work = manager._session_dir(session_id) / "work"
        work.mkdir(parents=True, exist_ok=True)
        target = work / "audio_44k.wav"
        target.write_bytes(wav_bytes(rate=44100))
        manager._update_meta(session_id, {"phase": "audio_ready", "progress": 100, "audio_files": [{"name": "voice.mp3", "seconds": 1.0}]})

    def fake_separate(session_id, audio_wav, cancel):
        manager._store_segments(session_id, fake_segments_result(manager._session_dir(session_id), audio))

    monkeypatch.setattr(manager, "_convert_audio_files", fake_convert)
    monkeypatch.setattr(manager, "_run_separation", fake_separate)
    session_id = client.post("/api/voice-studio/sessions", headers=headers).json()["session_id"]
    uploaded = client.post(
        f"/api/voice-studio/sessions/{session_id}/audio",
        files={"files": ("voice.mp3", b"fake-mp3", "audio/mpeg")},
        headers=headers,
    )
    assert uploaded.status_code == 202
    import time

    for _ in range(50):
        state = client.get(f"/api/voice-studio/sessions/{session_id}", headers=headers).json()
        if state["phase"] == "audio_ready":
            break
        time.sleep(0.05)
    assert state["phase"] == "audio_ready"
    assert state["source_kind"] == "audio"

    # 未确认前不会自动分离；确认后才进入片段
    separated = client.post(f"/api/voice-studio/sessions/{session_id}/separate", headers=headers)
    assert separated.status_code == 202
    for _ in range(50):
        state = client.get(f"/api/voice-studio/sessions/{session_id}", headers=headers).json()
        if state["phase"] == "segments":
            break
        time.sleep(0.05)
    assert state["phase"] == "segments"

    direct = client.post(
        f"/api/voice-studio/sessions/{session_id}/reference/upload",
        files={"audio": ("clean.wav", wav_bytes(b"\x00\x00" * 48000), "audio/wav")},
        headers=headers,
    )
    assert direct.status_code == 202
    assert direct.json()["phase"] == "reference"
    assert direct.json()["reference_source"] == "upload"


def test_batch_audio_and_user_uploaded_segments(client, tmp_path, monkeypatch):
    headers = {"X-YUMENO-Request": "web"}
    patch_ffmpeg(monkeypatch)
    manager = install_manager(client, tmp_path, monkeypatch)

    def fake_convert(session_id, audio_paths, cancel):
        work = manager._session_dir(session_id) / "work"
        work.mkdir(parents=True, exist_ok=True)
        target = work / "audio_44k.wav"
        target.write_bytes(wav_bytes(rate=44100))
        manager._update_meta(
            session_id,
            {"phase": "audio_ready", "progress": 100, "audio_files": [{"name": "a.mp3", "seconds": 1.0}, {"name": "b.mp3", "seconds": 1.0}]},
        )

    monkeypatch.setattr(manager, "_convert_audio_files", fake_convert)
    session_id = client.post("/api/voice-studio/sessions", headers=headers).json()["session_id"]
    uploaded = client.post(
        f"/api/voice-studio/sessions/{session_id}/audio",
        files=[
            ("files", ("a.mp3", b"fake-a", "audio/mpeg")),
            ("files", ("b.mp3", b"fake-b", "audio/mpeg")),
        ],
        headers=headers,
    )
    assert uploaded.status_code == 202
    import time

    for _ in range(50):
        state = client.get(f"/api/voice-studio/sessions/{session_id}", headers=headers).json()
        if state["phase"] == "audio_ready":
            break
        time.sleep(0.05)
    assert len(state["audio_files"]) == 2

    uploaded_segments = client.post(
        f"/api/voice-studio/sessions/{session_id}/segments/upload",
        files=[
            ("files", ("one.wav", wav_bytes(b"\x00\x00" * 48000), "audio/wav")),
            ("files", ("two.wav", wav_bytes(b"\x00\x00" * 72000), "audio/wav")),
        ],
        headers=headers,
    )
    assert uploaded_segments.status_code == 202
    segments = uploaded_segments.json()["segments"]
    assert len(segments) == 2
    assert all(segment["source"] == "upload" for segment in segments)
    first_index = segments[0]["index"]
    removed = client.delete(f"/api/voice-studio/sessions/{session_id}/segments/{first_index}", headers=headers)
    assert removed.json()["deleted"] is True
    assert len(client.get(f"/api/voice-studio/sessions/{session_id}", headers=headers).json()["segments"]) == 1


def test_voice_studio_rejects_bad_files(client, tmp_path, monkeypatch):
    headers = {"X-YUMENO-Request": "web"}
    install_manager(client, tmp_path, monkeypatch)
    session_id = client.post("/api/voice-studio/sessions", headers=headers).json()["session_id"]
    bad = client.post(
        f"/api/voice-studio/sessions/{session_id}/video",
        files={"video": ("clip.txt", b"nope", "text/plain")},
        headers=headers,
    )
    assert bad.status_code == 415
    missing = client.get("/api/voice-studio/sessions/nope", headers=headers)
    assert missing.status_code == 404


def test_reference_preview_uses_named_voice(client, tmp_path, monkeypatch):
    headers = {"X-YUMENO-Request": "web"}
    manager = install_manager(client, tmp_path, monkeypatch)
    session_id = client.post("/api/voice-studio/sessions", headers=headers).json()["session_id"]
    reference = tmp_path / "reference.wav"
    reference.write_bytes(wav_bytes())
    monkeypatch.setattr(manager, "reference_path", lambda _: reference)

    class FakeTTS:
        def synthesize(self, text, output, reference_audio=None):
            assert text == "你好，测试音色"
            output.write_bytes(wav_bytes())

    client.app.state.tts_resources.status = lambda: {"ready": True}
    client.app.state.tts_factory = lambda: FakeTTS()

    class FakeSimilarity:
        def similarity(self, audio_a, audio_b):
            assert str(audio_a) == str(reference)
            return 0.87

        def close(self):
            pass

    client.app.state.voice_similarity = FakeSimilarity()
    response = client.post(
        f"/api/voice-studio/sessions/{session_id}/reference/preview",
        json={"text": "你好，测试音色"},
        headers=headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["similarity"] == 0.87
    assert data["audio"]
