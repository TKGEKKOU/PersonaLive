from integrations.commands import parse_command


def test_parse_persona_command():
    assert parse_command("/角色 小爱") == ("persona", "小爱")
    assert parse_command("  /角色   小爱  ") == ("persona", "小爱")


def test_parse_approve_reject_help():
    assert parse_command("/同意") == ("approve", "")
    assert parse_command("/拒绝") == ("reject", "")
    assert parse_command("/帮助") == ("help", "")


def test_parse_plain_message_returns_none():
    assert parse_command("你好") is None
    assert parse_command("/角色") is None
    assert parse_command("") is None
