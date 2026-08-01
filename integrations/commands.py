def parse_command(text: str) -> tuple[str, str] | None:
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None
    parts = stripped[1:].split(maxsplit=1)
    command = parts[0]
    argument = parts[1].strip() if len(parts) > 1 else ""
    if command == "角色" and argument:
        return ("persona", argument)
    if command == "同意" and not argument:
        return ("approve", "")
    if command == "拒绝" and not argument:
        return ("reject", "")
    if command == "帮助" and not argument:
        return ("help", "")
    return None
