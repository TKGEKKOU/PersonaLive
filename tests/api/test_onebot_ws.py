from types import SimpleNamespace

import pytest
from starlette.websockets import WebSocketDisconnect


def _prepare(client, tmp_path, monkeypatch):
    from integrations import config as integrations_config

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.database import Base
    from persona.service import create_persona

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    im_session = sessionmaker(bind=engine, expire_on_commit=False)
    client.app.state.im_router.session_factory = im_session

    config_path = tmp_path / "integrations.json"
    bindings_path = tmp_path / "bindings.json"
    config_path.write_text(
        '{"onebot11": {"enabled": true, "access_token": "", '
        '"group_trigger": "at", "prefix": "", "default_persona_id": ""}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "app.main.onebot_runtime_config",
        lambda root: integrations_config.onebot_config(
            integrations_config.load_integrations(config_path)
        ),
    )
    client.app.state.im_router.bindings_path = bindings_path
    client.app.state.im_router.integrations_path = config_path

    with im_session() as session:
        persona = create_persona(session, "小爱")
        session.commit()
        persona_id = persona.id
    config_path.write_text(
        '{"onebot11": {"enabled": true, "access_token": "", '
        '"group_trigger": "at", "prefix": "", "default_persona_id": "'
        + persona_id + '"}}',
        encoding="utf-8",
    )

    class FakeAgent:
        def query(self, question, context):
            return SimpleNamespace(
                status="completed", answer=f"回答：{question}", pending_action=None
            )

        def resume(self, context, specialist, approved):
            return SimpleNamespace(status="completed", answer="已确认", pending_action=None)

    client.app.state.im_router.agent_service = FakeAgent()
    return persona_id


def test_private_message_receives_reply(client, tmp_path, monkeypatch):
    _prepare(client, tmp_path, monkeypatch)
    with client.websocket_connect("/api/onebot/ws") as ws:
        ws.send_json({
            "post_type": "message",
            "message_type": "private",
            "self_id": 10001,
            "user_id": 20001,
            "message": [{"type": "text", "data": {"text": "你好"}}],
            "raw_message": "你好",
        })
        action = ws.receive_json()
        assert action["action"] == "send_private_msg"
        assert action["params"]["user_id"] == 20001
        assert action["params"]["message"] == "回答：你好"


def test_group_message_without_at_is_ignored(client, tmp_path, monkeypatch):
    _prepare(client, tmp_path, monkeypatch)
    with client.websocket_connect("/api/onebot/ws") as ws:
        ws.send_json({
            "post_type": "message",
            "message_type": "group",
            "self_id": 10001,
            "user_id": 20001,
            "group_id": 30001,
            "message": [{"type": "text", "data": {"text": "你好"}}],
            "raw_message": "你好",
        })
        ws.send_json({
            "post_type": "message",
            "message_type": "group",
            "self_id": 10001,
            "user_id": 20001,
            "group_id": 30001,
            "message": [
                {"type": "at", "data": {"qq": "10001"}},
                {"type": "text", "data": {"text": " 在吗"}},
            ],
            "raw_message": "[CQ:at,qq=10001] 在吗",
        })
        action = ws.receive_json()
        assert action["action"] == "send_group_msg"
        assert action["params"]["message"] == "回答：在吗"


def test_persona_command_binds_session(client, tmp_path, monkeypatch):
    persona_id = _prepare(client, tmp_path, monkeypatch)
    with client.websocket_connect("/api/onebot/ws") as ws:
        ws.send_json({
            "post_type": "message",
            "message_type": "private",
            "self_id": 10001,
            "user_id": 20001,
            "message": [{"type": "text", "data": {"text": "/角色 小爱"}}],
            "raw_message": "/角色 小爱",
        })
        action = ws.receive_json()
        assert action["params"]["message"] == "已绑定角色「小爱」。"
        bindings = client.app.state.im_router.bindings_path.read_text(encoding="utf-8")
        assert persona_id in bindings


def test_disabled_integration_rejects_connection(client, tmp_path, monkeypatch):
    from integrations import config as integrations_config

    config_path = tmp_path / "integrations.json"
    config_path.write_text('{"onebot11": {"enabled": false}}', encoding="utf-8")
    monkeypatch.setattr(
        "app.main.onebot_runtime_config",
        lambda root: integrations_config.onebot_config(
            integrations_config.load_integrations(config_path)
        ),
    )
    try:
        with client.websocket_connect("/api/onebot/ws") as ws:
            data = ws.receive()
        assert data.get("code") == 1008
    except WebSocketDisconnect:
        pass
