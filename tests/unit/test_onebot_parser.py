from integrations.onebot11.parser import OneBotMessage, parse_message_event


def test_private_message_with_text():
    payload = {
        "post_type": "message",
        "message_type": "private",
        "self_id": 10001,
        "user_id": 20001,
        "message": [{"type": "text", "data": {"text": "你好"}}],
        "raw_message": "你好",
    }
    event = parse_message_event(payload)
    assert event is not None
    assert event.message_type == "private"
    assert event.user_id == "20001"
    assert event.group_id is None
    assert event.self_id == "10001"
    assert event.text == "你好"
    assert event.is_at is False


def test_group_message_with_at_detection():
    payload = {
        "post_type": "message",
        "message_type": "group",
        "self_id": 10001,
        "user_id": 20001,
        "group_id": 30001,
        "message": [
            {"type": "at", "data": {"qq": "10001"}},
            {"type": "text", "data": {"text": " 介绍一下自己"}},
        ],
        "raw_message": "[CQ:at,qq=10001] 介绍一下自己",
    }
    event = parse_message_event(payload)
    assert event is not None
    assert event.group_id == "30001"
    assert event.text == " 介绍一下自己"
    assert event.is_at is True


def test_non_message_post_type_returns_none():
    assert parse_message_event({"post_type": "notice"}) is None
    assert parse_message_event({}) is None


def test_string_message_and_cq_at_fallback():
    payload = {
        "post_type": "message",
        "message_type": "group",
        "self_id": 10001,
        "user_id": 20001,
        "group_id": 30001,
        "message": "hello [CQ:at,qq=10001]",
        "raw_message": "hello [CQ:at,qq=10001]",
    }
    event = parse_message_event(payload)
    assert event is not None
    assert event.text == "hello [CQ:at,qq=10001]"
    assert event.is_at is True
