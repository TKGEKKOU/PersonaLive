# 插件框架 + OneBot 11 接入 + 前端模块化 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 PersonaLive 增加最小可用插件框架（本地 `plugins/` 扫描 + 事件总线 + 生命周期 + 配置持久化）、OneBot 11 QQ 接入（正向 WebSocket + 角色绑定 + 聊天命令），并把前端拆成单页外壳 + 模块视图/脚本，新增“接入”“插件”两个页面。

**Architecture:** 后端新增 `extensions/`（与应用无关的插件框架）和 `integrations/`（平台接入层）。OneBot 11 适配器把消息解析成统一的 `MessageEvent` 发布到 `EventBus`，内置 IM 路由器消费事件并调用现有 `PersonaAgentService`。前端保持单页体验，`index.html` 只留外壳，各模块视图与脚本按文件拆分。

**Tech Stack:** FastAPI（WebSocket 复用现有 8001 端口）、Python 3.11、LangGraph Agent（现有）、原生 JS（无前端构建工具）、pytest + TestClient。

## Global Constraints

- 只使用 OneBot 11 开放协议，不复制 AstrBot 代码，不引入现成 OneBot SDK。
- 插件为本地可信代码，不沙箱；`plugins/` 与 `data/` 均位于项目根目录。
- 新增依赖为零；PyYAML 已存在但不用于插件清单（仅 JSON）。
- 所有新增 Python 文件使用 UTF-8，遵循现有扁平包结构。
- 配置文件写入采用临时文件 + `os.replace` 原子写（与 `app/routers/settings.py` 一致）。
- 前端拆分不改变 DOM 结构、样式与交互，观感与拆分前一致；拆分后所有元素 id 不变。
- 现有测试断言迁移到 `views/*.html` 后，原有断言内容一律保留。
- 测试环境无 pytest-asyncio；异步函数测试用 `asyncio.run()` 包裹。
- API 路由新增项沿用 `require_local` 本机校验。

---

### Task 1: EventBus 与 MessageEvent

**Files:**
- Create: `extensions/__init__.py`
- Create: `extensions/events.py`
- Test: `tests/unit/test_event_bus.py`

**Interfaces:**
- Consumes: 无。
- Produces: `EventBus.subscribe(event: str, handler) -> Callable[[], None]`；`async EventBus.publish(event: str, payload) -> None`；`MessageEvent` dataclass；`EVENT_MESSAGE = "message"`。Task 3/4/9 依赖。

- [ ] **Step 1: 写失败测试**

```python
import asyncio

from extensions.events import EVENT_MESSAGE, EventBus, MessageEvent


def test_sync_and_async_handlers_both_receive_payload():
    bus = EventBus()
    received = []

    def sync_handler(payload):
        received.append(("sync", payload))

    async def async_handler(payload):
        await asyncio.sleep(0)
        received.append(("async", payload))

    bus.subscribe("x", sync_handler)
    bus.subscribe("x", async_handler)
    asyncio.run(bus.publish("x", {"n": 1}))
    assert received == [("sync", {"n": 1}), ("async", {"n": 1})]


def test_unsubscribe_removes_handler():
    bus = EventBus()
    received = []
    unsub = bus.subscribe("x", lambda payload: received.append(payload))
    unsub()
    asyncio.run(bus.publish("x", 1))
    assert received == []


def test_handler_exception_does_not_block_other_handlers():
    bus = EventBus()
    received = []

    def failing(payload):
        raise RuntimeError("boom")

    def ok(payload):
        received.append(payload)

    bus.subscribe("x", failing)
    bus.subscribe("x", ok)
    asyncio.run(bus.publish("x", 2))
    assert received == [2]


def test_message_event_fields():
    event = MessageEvent(
        platform="onebot11",
        chat_type="private",
        chat_id="10001",
        user_id="10001",
        content="你好",
        raw_content="你好",
        reply=lambda text: None,
    )
    assert event.platform == "onebot11"
    assert event.chat_id == "10001"
    assert event.is_at is False
    assert EVENT_MESSAGE == "message"
```

- [ ] **Step 2: 运行确认失败**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_event_bus.py -q`
Expected: FAIL（`ModuleNotFoundError: extensions`）。

- [ ] **Step 3: 实现**

`extensions/__init__.py`：

```python
"""插件框架核心包：事件总线、清单、加载器、管理器。"""
```

`extensions/events.py`：

```python
import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

Handler = Callable[[Any], Any]

EVENT_MESSAGE = "message"


@dataclass(frozen=True)
class MessageEvent:
    platform: str
    chat_type: str
    chat_id: str
    user_id: str
    content: str
    raw_content: str
    reply: Callable[[str], None]
    is_at: bool = False


class EventBus:
    """进程内事件分发器；单个 handler 异常不影响其他订阅者。"""

    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = {}

    def subscribe(self, event: str, handler: Handler) -> Callable[[], None]:
        self._handlers.setdefault(event, []).append(handler)

        def unsubscribe() -> None:
            handlers = self._handlers.get(event)
            if handlers and handler in handlers:
                handlers.remove(handler)

        return unsubscribe

    async def publish(self, event: str, payload: Any) -> None:
        for handler in list(self._handlers.get(event, ())):
            try:
                result = handler(payload)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                logger.exception("event handler failed for event=%s", event)
```

- [ ] **Step 4: 运行确认通过**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_event_bus.py -q`
Expected: PASS（3 passed）。

- [ ] **Step 5: 提交**

```bash
git add extensions tests/unit/test_event_bus.py
git commit -m "feat: add event bus and message event"
```

---

### Task 2: 插件清单解析

**Files:**
- Create: `extensions/manifest.py`
- Test: `tests/unit/test_plugin_manifest.py`

**Interfaces:**
- Consumes: 无。
- Produces: `PluginManifest`（name/version/description/author/entry/config_schema 字段）；`PluginManifestError`；`PluginManifest.load(plugin_dir: Path) -> PluginManifest`；`discover_plugins(plugins_root: Path) -> list[Path]`（返回含 `plugin.json` 的目录，按名称排序）。Task 3/4 依赖。

- [ ] **Step 1: 写失败测试**

```python
import json
from pathlib import Path

import pytest

from extensions.manifest import PluginManifest, PluginManifestError, discover_plugins


def _write_plugin(root: Path, name: str, data: dict) -> Path:
    plugin_dir = root / name
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(json.dumps(data), encoding="utf-8")
    (plugin_dir / "main.py").write_text("", encoding="utf-8")
    return plugin_dir


def test_load_valid_manifest(tmp_path: Path):
    plugin_dir = _write_plugin(tmp_path, "my_plugin", {
        "name": "my_plugin",
        "version": "0.1.0",
        "description": "测试插件",
        "author": "tester",
        "entry": "main.py",
        "config_schema": {"greeting": {"type": "string", "default": "hi"}},
    })
    manifest = PluginManifest.load(plugin_dir)
    assert manifest.name == "my_plugin"
    assert manifest.version == "0.1.0"
    assert manifest.description == "测试插件"
    assert manifest.author == "tester"
    assert manifest.entry == "main.py"
    assert manifest.config_schema["greeting"]["default"] == "hi"


def test_load_uses_defaults(tmp_path: Path):
    plugin_dir = _write_plugin(tmp_path, "minimal", {"name": "minimal", "version": "1.0.0"})
    manifest = PluginManifest.load(plugin_dir)
    assert manifest.entry == "main.py"
    assert manifest.config_schema == {}
    assert manifest.description == ""
    assert manifest.author == ""


@pytest.mark.parametrize("bad", [
    {"version": "0.1.0"},
    {"name": "minimal"},
    {"name": "Bad Name", "version": "0.1.0"},
    {"name": "other_dir", "version": "0.1.0"},
    {"name": "minimal", "version": ""},
    {"name": "minimal", "version": "0.1.0", "entry": "missing.py"},
])
def test_invalid_manifests_raise(tmp_path: Path, bad: dict):
    plugin_dir = _write_plugin(tmp_path, "minimal", bad)
    with pytest.raises(PluginManifestError):
        PluginManifest.load(plugin_dir)


def test_load_rejects_corrupt_json(tmp_path: Path):
    plugin_dir = tmp_path / "broken"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(PluginManifestError):
        PluginManifest.load(plugin_dir)


def test_discover_plugins_lists_only_dirs_with_manifest(tmp_path: Path):
    _write_plugin(tmp_path, "aaa", {"name": "aaa", "version": "0.1.0"})
    _write_plugin(tmp_path, "bbb", {"name": "bbb", "version": "0.1.0"})
    (tmp_path / "empty").mkdir()
    assert [p.name for p in discover_plugins(tmp_path)] == ["aaa", "bbb"]
```

- [ ] **Step 2: 运行确认失败**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_plugin_manifest.py -q`
Expected: FAIL（`ModuleNotFoundError: extensions.manifest`）。

- [ ] **Step 3: 实现**

`extensions/manifest.py`：

```python
import json
import re
from dataclasses import dataclass, field
from pathlib import Path


class PluginManifestError(ValueError):
    pass


@dataclass(frozen=True)
class PluginManifest:
    name: str
    version: str
    description: str = ""
    author: str = ""
    entry: str = "main.py"
    config_schema: dict = field(default_factory=dict)

    @classmethod
    def load(cls, plugin_dir: Path) -> "PluginManifest":
        manifest_path = plugin_dir / "plugin.json"
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PluginManifestError(f"{manifest_path} 无法读取或不是合法 JSON") from exc
        if not isinstance(data, dict):
            raise PluginManifestError(f"{manifest_path} 顶层必须是 JSON 对象")

        name = str(data.get("name") or "").strip()
        if not re.fullmatch(r"[a-z0-9_-]+", name):
            raise PluginManifestError("name 必须匹配 [a-z0-9_-]+")
        if name != plugin_dir.name:
            raise PluginManifestError("name 必须与插件目录名一致")

        version = str(data.get("version") or "").strip()
        if not version:
            raise PluginManifestError("version 不能为空")

        entry = str(data.get("entry") or "main.py").strip()
        if not entry or not (plugin_dir / entry).is_file():
            raise PluginManifestError(f"入口文件 {entry} 不存在")

        config_schema = data.get("config_schema") or {}
        if not isinstance(config_schema, dict):
            raise PluginManifestError("config_schema 必须是对象")
        return cls(
            name=name,
            version=version,
            description=str(data.get("description") or ""),
            author=str(data.get("author") or ""),
            entry=entry,
            config_schema=config_schema,
        )


def discover_plugins(plugins_root: Path) -> list[Path]:
    if not plugins_root.is_dir():
        return []
    return sorted(
        (child for child in plugins_root.iterdir() if (child / "plugin.json").is_file()),
        key=lambda path: path.name,
    )
```

- [ ] **Step 4: 运行确认通过**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_plugin_manifest.py -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add extensions/manifest.py tests/unit/test_plugin_manifest.py
git commit -m "feat: parse and validate plugin manifests"
```

---

### Task 3: 插件入口加载与 PluginContext

**Files:**
- Create: `extensions/storage.py`
- Create: `extensions/loader.py`
- Create: `extensions/api.py`
- Test: `tests/unit/test_plugin_loader.py`

**Interfaces:**
- Consumes: Task 1 的 `EventBus`、`EVENT_MESSAGE`；Task 2 的 `PluginManifest`。
- Produces: `read_json(path) -> dict` / `write_json(path, data) -> None`（storage）；`load_plugin_entry(plugin_dir: Path, manifest: PluginManifest) -> ModuleType`；`PluginContext`（`config`、`save_config`、`query_agent`、`log`、`on_event`、`handlers_for(event)`）。Task 4/9 依赖。

- [ ] **Step 1: 写失败测试**

```python
import asyncio
import json
from pathlib import Path

from extensions.api import PluginContext
from extensions.events import EventBus
from extensions.loader import load_plugin_entry
from extensions.manifest import PluginManifest
from extensions.storage import read_json, write_json


def test_write_and_read_json_roundtrip(tmp_path: Path):
    target = tmp_path / "data" / "test.json"
    write_json(target, {"a": 1, "nested": {"b": True}})
    assert read_json(target) == {"a": 1, "nested": {"b": True}}


def test_read_json_missing_file_returns_empty(tmp_path: Path):
    assert read_json(tmp_path / "missing.json") == {}


def test_load_plugin_entry_imports_module(tmp_path: Path):
    plugin_dir = tmp_path / "greeter"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.json").write_text(
        json.dumps({"name": "greeter", "version": "0.1.0", "entry": "main.py"}),
        encoding="utf-8",
    )
    (plugin_dir / "main.py").write_text(
        "GREETING = '你好'\n\ndef on_load(ctx):\n    ctx.log.info('loaded')\n",
        encoding="utf-8",
    )
    manifest = PluginManifest.load(plugin_dir)
    module = load_plugin_entry(plugin_dir, manifest)
    assert module.GREETING == "你好"
    assert callable(module.on_load)


def test_plugin_context_events_and_agent_runner(tmp_path: Path):
    bus = EventBus()
    calls = []

    async def runner(question, persona_id, conversation_id):
        calls.append((question, persona_id, conversation_id))
        return {"status": "completed", "answer": "ok"}

    ctx = PluginContext(
        name="greeter",
        config={"greeting": "hi"},
        config_path=tmp_path / "config.json",
        event_bus=bus,
        log=None,
        agent_runner=runner,
    )

    @ctx.on_event("message")
    async def handler(event):
        calls.append(event)

    assert len(ctx.handlers_for("message")) == 1
    result = asyncio.run(ctx.query_agent("问题", "p1", "c1"))
    assert result["answer"] == "ok"
    assert calls[0] == ("问题", "p1", "c1")


def test_plugin_context_save_config_persists(tmp_path: Path):
    config_path = tmp_path / "config.json"
    ctx = PluginContext(
        name="greeter",
        config={"greeting": "hi"},
        config_path=config_path,
        event_bus=EventBus(),
        log=None,
        agent_runner=None,
    )
    asyncio.run(ctx.save_config({"greeting": "hello"}))
    assert json.loads(config_path.read_text(encoding="utf-8")) == {"greeting": "hello"}
    assert ctx.config == {"greeting": "hello"}
```

- [ ] **Step 2: 运行确认失败**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_plugin_loader.py -q`
Expected: FAIL（`ModuleNotFoundError`）。

- [ ] **Step 3: 实现**

`extensions/storage.py`：

```python
import json
import os
import tempfile
from pathlib import Path


def read_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=".tmp.", suffix=".json", delete=False
    ) as temporary:
        json.dump(data, temporary, ensure_ascii=False, indent=2)
        temporary.write("\n")
        temporary_path = Path(temporary.name)
    os.replace(temporary_path, path)
```

`extensions/loader.py`：

```python
import importlib.util
from pathlib import Path
from types import ModuleType

from extensions.manifest import PluginManifest


def load_plugin_entry(plugin_dir: Path, manifest: PluginManifest) -> ModuleType:
    entry_path = plugin_dir / manifest.entry
    module_name = f"personalive_plugin_{manifest.name}"
    spec = importlib.util.spec_from_file_location(module_name, entry_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载插件入口 {entry_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
```

`extensions/api.py`：

```python
import asyncio
import logging
from collections.abc import Callable
from pathlib import Path

from extensions.events import EventBus
from extensions.storage import read_json, write_json

AgentRunner = Callable[[str, str, str], dict]
EventHandler = Callable[..., object]


class PluginContext:
    """插件运行时 API；on_event 收集的 handler 由 PluginManager 注册到 EventBus。"""

    def __init__(
        self,
        name: str,
        config: dict,
        config_path: Path,
        event_bus: EventBus,
        log: logging.Logger | None,
        agent_runner: AgentRunner | None = None,
    ) -> None:
        self._name = name
        self._config = dict(config)
        self._config_path = config_path
        self._event_bus = event_bus
        self.log = log or logging.getLogger(f"plugin.{name}")
        self._agent_runner = agent_runner
        self._handlers: dict[str, list[EventHandler]] = {}

    @property
    def config(self) -> dict:
        return dict(self._config)

    def on_event(self, event: str):
        def decorator(handler: EventHandler) -> EventHandler:
            self._handlers.setdefault(event, []).append(handler)
            return handler

        return decorator

    def handlers_for(self, event: str) -> tuple[EventHandler, ...]:
        return tuple(self._handlers.get(event, ()))

    def event_handlers(self) -> dict[str, list[EventHandler]]:
        return {event: list(handlers) for event, handlers in self._handlers.items()}

    async def save_config(self, updates: dict) -> None:
        self._config.update(updates)
        existing = read_json(self._config_path)
        existing.update({self._name: self._config})
        write_json(self._config_path, existing)

    async def query_agent(self, question: str, persona_id: str, conversation_id: str) -> dict:
        if self._agent_runner is None:
            raise RuntimeError("agent runner 未注入")
        return await asyncio.to_thread(self._agent_runner, question, persona_id, conversation_id)
```

- [ ] **Step 4: 运行确认通过**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_plugin_loader.py -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add extensions/storage.py extensions/loader.py extensions/api.py tests/unit/test_plugin_loader.py
git commit -m "feat: load plugin entries and expose plugin context"
```

---

### Task 4: PluginManager 生命周期与持久化

**Files:**
- Create: `extensions/manager.py`
- Test: `tests/unit/test_plugin_manager.py`

**Interfaces:**
- Consumes: Task 1/2/3 全部。
- Produces: `PluginInfo` dataclass（name/version/description/author/enabled/config/error）；`PluginManager(plugins_root, data_dir, event_bus, agent_runner=None)`，方法 `load_all() -> list[PluginInfo]`、`list_plugins() -> list[PluginInfo]`、`enable(name) -> PluginInfo`、`disable(name) -> PluginInfo`、`reload(name) -> PluginInfo`、`save_config(name, updates) -> PluginInfo`、`unload_all() -> None`。Task 5/9 依赖。

- [ ] **Step 1: 写失败测试**

```python
import asyncio
import json
from pathlib import Path

import pytest

from extensions.events import EventBus
from extensions.manager import PluginManager


def _make_plugin(root: Path, name: str, entry_code: str) -> None:
    plugin_dir = root / name
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps({"name": name, "version": "0.1.0", "entry": "main.py",
                    "config_schema": {"greeting": {"default": "hi"}}}),
        encoding="utf-8",
    )
    (plugin_dir / "main.py").write_text(entry_code, encoding="utf-8")


def test_load_all_and_lifecycle(tmp_path: Path):
    _make_plugin(tmp_path / "plugins", "greeter",
                 "from extensions.api import PluginContext\n"
                 "EVENTS = []\n"
                 "def on_load(ctx: PluginContext):\n"
                 "    EVENTS.append('load')\n"
                 "    @ctx.on_event('message')\n"
                 "    async def h(event):\n"
                 "        EVENTS.append(event)\n"
                 "def on_unload():\n"
                 "    EVENTS.append('unload')\n")
    bus = EventBus()
    manager = PluginManager(tmp_path / "plugins", tmp_path / "data", bus)
    plugins = manager.load_all()
    assert len(plugins) == 1
    assert plugins[0].name == "greeter"
    assert plugins[0].enabled is False
    assert plugins[0].config == {"greeting": "hi"}

    info = manager.enable("greeter")
    assert info.enabled is True
    await_publish = asyncio.run(bus.publish("message", "payload"))
    assert await_publish is None
    module = manager._plugins["greeter"].module
    assert module.EVENTS == ["load", "payload"]

    assert manager.disable("greeter").enabled is False
    assert module.EVENTS == ["load", "payload", "unload"]


def test_broken_plugin_does_not_break_others(tmp_path: Path):
    _make_plugin(tmp_path / "plugins", "good",
                 "def on_load(ctx):\n    pass\n")
    bad_dir = tmp_path / "plugins" / "bad"
    bad_dir.mkdir(parents=True)
    (bad_dir / "plugin.json").write_text(
        json.dumps({"name": "bad", "version": "0.1.0"}), encoding="utf-8")
    (bad_dir / "main.py").write_text("raise RuntimeError('broken')\n", encoding="utf-8")
    manager = PluginManager(tmp_path / "plugins", tmp_path / "data", EventBus())
    plugins = manager.load_all()
    by_name = {p.name: p for p in plugins}
    assert by_name["good"].error is None
    assert "broken" in (by_name["bad"].error or "")


def test_state_and_config_persist(tmp_path: Path):
    _make_plugin(tmp_path / "plugins", "greeter",
                 "def on_load(ctx):\n    pass\n")
    manager = PluginManager(tmp_path / "plugins", tmp_path / "data", EventBus())
    manager.load_all()
    manager.enable("greeter")
    manager.save_config("greeter", {"greeting": "hello"})
    state = json.loads((tmp_path / "data" / "plugin_state.json").read_text(encoding="utf-8"))
    config = json.loads((tmp_path / "data" / "plugin_configs.json").read_text(encoding="utf-8"))
    assert state == {"greeter": True}
    assert config == {"greeter": {"greeting": "hello"}}

    manager2 = PluginManager(tmp_path / "plugins", tmp_path / "data", EventBus())
    plugins = manager2.load_all()
    assert plugins[0].enabled is True
    assert plugins[0].config == {"greeting": "hello"}


def test_unknown_plugin_raises(tmp_path: Path):
    manager = PluginManager(tmp_path / "plugins", tmp_path / "data", EventBus())
    with pytest.raises(KeyError):
        manager.enable("missing")
```

- [ ] **Step 2: 运行确认失败**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_plugin_manager.py -q`
Expected: FAIL（`ModuleNotFoundError: extensions.manager`）。

- [ ] **Step 3: 实现**

`extensions/manager.py`：

```python
import asyncio
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from types import ModuleType

from extensions.api import AgentRunner, PluginContext
from extensions.events import EventBus
from extensions.manifest import PluginManifest, PluginManifestError, discover_plugins
from extensions.storage import read_json, write_json


@dataclass(frozen=True)
class PluginInfo:
    name: str
    version: str
    description: str
    author: str
    enabled: bool
    config: dict
    error: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class _PluginRecord:
    manifest: PluginManifest
    module: ModuleType | None = None
    context: PluginContext | None = None
    unsubscribers: list = None
    enabled: bool = False
    error: str | None = None

    def __post_init__(self) -> None:
        self.unsubscribers = []


class PluginManager:
    def __init__(
        self,
        plugins_root: Path,
        data_dir: Path,
        event_bus: EventBus,
        agent_runner: AgentRunner | None = None,
    ) -> None:
        self.plugins_root = plugins_root
        self.data_dir = data_dir
        self.event_bus = event_bus
        self.agent_runner = agent_runner
        self._plugins: dict[str, _PluginRecord] = {}
        self._state_path = data_dir / "plugin_state.json"
        self._config_path = data_dir / "plugin_configs.json"

    def load_all(self) -> list[PluginInfo]:
        self.unload_all()
        state = read_json(self._state_path)
        configs = read_json(self._config_path)
        for plugin_dir in discover_plugins(self.plugins_root):
            try:
                manifest = PluginManifest.load(plugin_dir)
            except PluginManifestError as exc:
                self._plugins[plugin_dir.name] = _PluginRecord(
                    manifest=PluginManifest(plugin_dir.name, "0.0.0"),
                    error=str(exc),
                )
                continue
            defaults = {
                key: (value.get("default") if isinstance(value, dict) else None)
                for key, value in manifest.config_schema.items()
            }
            config = {**defaults, **(configs.get(manifest.name) or {})}
            record = _PluginRecord(manifest=manifest, context=None)
            self._plugins[manifest.name] = record
            if state.get(manifest.name, False):
                self._load_record(manifest.name)
        return self.list_plugins()

    def _load_record(self, name: str) -> None:
        record = self._plugins[name]
        try:
            from extensions.loader import load_plugin_entry

            module = load_plugin_entry(self.plugins_root / name, record.manifest)
            context = PluginContext(
                name=name,
                config={**record.manifest.default_config(), **read_json(self._config_path).get(name, {})},
                config_path=self._config_path,
                event_bus=self.event_bus,
                log=logging.getLogger(f"plugin.{name}"),
                agent_runner=self.agent_runner,
            )
            record.module = module
            record.context = context
            if hasattr(module, "on_load"):
                result = module.on_load(context)
                if asyncio.iscoroutine(result):
                    asyncio.get_event_loop().run_until_complete(result)
            for event, handlers in context.event_handlers().items():
                for handler in handlers:
                    record.unsubscribers.append(self.event_bus.subscribe(event, handler))
            record.enabled = True
            record.error = None
        except Exception as exc:
            record.module = None
            record.context = None
            record.enabled = False
            record.error = str(exc)

    def _unload_record(self, name: str) -> None:
        record = self._plugins.get(name)
        if record is None:
            return
        for unsub in record.unsubscribers:
            unsub()
        record.unsubscribers = []
        if record.module is not None and hasattr(record.module, "on_unload"):
            try:
                result = record.module.on_unload()
                if asyncio.iscoroutine(result):
                    asyncio.get_event_loop().run_until_complete(result)
            except Exception:
                logging.getLogger(__name__).exception("plugin on_unload failed: %s", name)
        record.module = None
        record.context = None
        record.enabled = False

    def list_plugins(self) -> list[PluginInfo]:
        return [self._info(name) for name in sorted(self._plugins)]

    def _info(self, name: str) -> PluginInfo:
        record = self._plugins[name]
        return PluginInfo(
            name=record.manifest.name,
            version=record.manifest.version,
            description=record.manifest.description,
            author=record.manifest.author,
            enabled=record.enabled,
            config=record.context.config if record.context else read_json(self._config_path).get(name, {}),
            error=record.error,
        )

    def enable(self, name: str) -> PluginInfo:
        if name not in self._plugins:
            raise KeyError(name)
        record = self._plugins[name]
        if not record.enabled:
            self._load_record(name)
        state = read_json(self._state_path)
        state[name] = True
        write_json(self._state_path, state)
        return self._info(name)

    def disable(self, name: str) -> PluginInfo:
        if name not in self._plugins:
            raise KeyError(name)
        self._unload_record(name)
        state = read_json(self._state_path)
        state[name] = False
        write_json(self._state_path, state)
        return self._info(name)

    def reload(self, name: str) -> PluginInfo:
        if name not in self._plugins:
            raise KeyError(name)
        was_enabled = self._plugins[name].enabled
        self._unload_record(name)
        if was_enabled:
            self._load_record(name)
        return self._info(name)

    def save_config(self, name: str, updates: dict) -> PluginInfo:
        if name not in self._plugins:
            raise KeyError(name)
        configs = read_json(self._config_path)
        current = configs.get(name) or {}
        current.update(updates)
        configs[name] = current
        write_json(self._config_path, configs)
        return self.reload(name)

    def unload_all(self) -> None:
        for name in list(self._plugins):
            self._unload_record(name)
        self._plugins = {}
```

在 `extensions/manifest.py` 的 `PluginManifest` 类末尾追加方法（Task 4 的 manager 依赖）：

```python
    def default_config(self) -> dict:
        return {
            key: (value.get("default") if isinstance(value, dict) else None)
            for key, value in self.config_schema.items()
        }
```

- [ ] **Step 4: 运行确认通过**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_plugin_manager.py -q`
Expected: PASS。若 `_PluginRecord.manifest_config()` 报错，按上文修正为 `manifest.default_config()` 合并方式后重跑。

- [ ] **Step 5: 提交**

```bash
git add extensions/manifest.py extensions/manager.py tests/unit/test_plugin_manager.py
git commit -m "feat: manage plugin lifecycle and persistence"
```

---

### Task 5: 插件管理 API 与 create_app 接线

**Files:**
- Create: `app/routers/plugins.py`
- Modify: `app/schemas.py`（追加 3 个 Pydantic 模型）
- Modify: `app/main.py`（构造 PluginManager、注册路由）
- Test: `tests/api/test_plugins_api.py`

**Interfaces:**
- Consumes: Task 4 的 `PluginManager`。
- Produces: `GET /api/plugins`、`PUT /api/plugins/{name}`（body `{"enabled": bool}`）、`PUT /api/plugins/{name}/config`（body `{"config": {...}}`）；`app.state.plugin_manager`。Task 11 前端依赖。

- [ ] **Step 1: 写失败测试**

```python
import json
from pathlib import Path


def _install_fixture_plugin(tmp_path: Path, monkeypatch, client) -> None:
    from app.main import create_app

    plugins_root = tmp_path / "plugins"
    plugin_dir = plugins_root / "greeter"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps({"name": "greeter", "version": "0.1.0", "entry": "main.py",
                    "config_schema": {"greeting": {"default": "hi"}}}),
        encoding="utf-8",
    )
    (plugin_dir / "main.py").write_text("def on_load(ctx):\n    pass\n", encoding="utf-8")
    manager = client.app.state.plugin_manager
    manager.plugins_root = plugins_root
    manager.data_dir = tmp_path / "data"
    manager._state_path = manager.data_dir / "plugin_state.json"
    manager._config_path = manager.data_dir / "plugin_configs.json"
    manager.load_all()


def test_plugins_list_and_enable(client, tmp_path, monkeypatch):
    _install_fixture_plugin(tmp_path, monkeypatch, client)
    listed = client.get("/api/plugins")
    assert listed.status_code == 200
    plugins = listed.json()
    assert plugins[0]["name"] == "greeter"
    assert plugins[0]["version"] == "0.1.0"
    assert plugins[0]["enabled"] is False

    enabled = client.put("/api/plugins/greeter", json={"enabled": True})
    assert enabled.status_code == 200
    assert enabled.json()["enabled"] is True

    config = client.put("/api/plugins/greeter/config", json={"config": {"greeting": "hello"}})
    assert config.status_code == 200
    assert config.json()["config"]["greeting"] == "hello"


def test_plugins_unknown_name_returns_404(client):
    response = client.put("/api/plugins/missing", json={"enabled": True})
    assert response.status_code == 404


def test_plugins_api_rejects_extra_fields(client):
    response = client.put("/api/plugins/greeter", json={"enabled": True, "extra": 1})
    assert response.status_code == 422
```

说明：`client` fixture 来自 `tests/conftest.py`（`create_app(initialize_database=False)`）。当前 `create_app` 尚未创建 `plugin_manager`，所以测试先失败（AttributeError）。测试中直接改写 manager 的路径字段而非重造 app，保持与现有 `test_settings.py` 的 monkeypatch 风格一致。

- [ ] **Step 2: 运行确认失败**

Run: `.\.venv\Scripts\python.exe -m pytest tests/api/test_plugins_api.py -q`
Expected: FAIL（`AttributeError: 'FastAPI' object has no attribute 'plugin_manager'`）。

- [ ] **Step 3: 实现**

`app/schemas.py` 末尾追加：

```python
class PluginEnablePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class PluginConfigPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    config: dict[str, Any]


class PluginInfoResponse(BaseModel):
    name: str
    version: str
    description: str
    author: str
    enabled: bool
    config: dict[str, Any]
    error: str | None = None
```

`app/routers/plugins.py`：

```python
from fastapi import APIRouter, HTTPException, Request

from app.routers.settings import require_local
from app.schemas import PluginConfigPayload, PluginEnablePayload, PluginInfoResponse


router = APIRouter(prefix="/api/plugins", tags=["plugins"])


def _manager(request: Request):
    return request.app.state.plugin_manager


@router.get("", response_model=list[PluginInfoResponse])
def list_plugins(request: Request) -> list[PluginInfoResponse]:
    require_local(request)
    return [_to_response(item) for item in _manager(request).list_plugins()]


@router.put("/{name}", response_model=PluginInfoResponse)
def set_plugin_enabled(name: str, payload: PluginEnablePayload, request: Request) -> PluginInfoResponse:
    require_local(request)
    manager = _manager(request)
    try:
        info = manager.enable(name) if payload.enabled else manager.disable(name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Plugin not found") from exc
    return _to_response(info)


@router.put("/{name}/config", response_model=PluginInfoResponse)
def update_plugin_config(name: str, payload: PluginConfigPayload, request: Request) -> PluginInfoResponse:
    require_local(request)
    try:
        info = _manager(request).save_config(name, payload.config)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Plugin not found") from exc
    return _to_response(info)


def _to_response(info) -> PluginInfoResponse:
    return PluginInfoResponse(
        name=info.name,
        version=info.version,
        description=info.description,
        author=info.author,
        enabled=info.enabled,
        config=info.config,
        error=info.error,
    )
```

`app/main.py` 修改：

- import 区追加：

```python
from app.routers.plugins import router as plugins_router
from extensions.events import EventBus
from extensions.manager import PluginManager
```

- `create_app` 内、`app.include_router(settings_router)` 之后追加：

```python
    app.include_router(plugins_router)
    app.state.event_bus = EventBus()
    app.state.plugin_manager = PluginManager(
        settings.project_root / "plugins",
        settings.project_root / "data",
        app.state.event_bus,
    )
    app.state.plugin_manager.load_all()
```

- lifespan 的 shutdown 分支追加：

```python
        app.state.plugin_manager.unload_all()
```

- [ ] **Step 4: 运行确认通过**

Run: `.\.venv\Scripts\python.exe -m pytest tests/api/test_plugins_api.py -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add app/schemas.py app/routers/plugins.py app/main.py tests/api/test_plugins_api.py
git commit -m "feat: plugin management API"
```

---

### Task 6: OneBot 11 消息解析

**Files:**
- Create: `integrations/__init__.py`
- Create: `integrations/onebot11/__init__.py`
- Create: `integrations/onebot11/parser.py`
- Test: `tests/unit/test_onebot_parser.py`

**Interfaces:**
- Consumes: 无（纯函数）。
- Produces: `OneBotMessage` dataclass（post_type/message_type/user_id/group_id/self_id/text/is_at）；`parse_message_event(payload: dict) -> OneBotMessage | None`。Task 9 依赖。

- [ ] **Step 1: 写失败测试**

```python
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
```

- [ ] **Step 2: 运行确认失败**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_onebot_parser.py -q`
Expected: FAIL（`ModuleNotFoundError`）。

- [ ] **Step 3: 实现**

`integrations/__init__.py`：

```python
"""平台接入层：第一版为 OneBot 11。"""
```

`integrations/onebot11/__init__.py`：

```python
"""OneBot 11 协议适配。"""
```

`integrations/onebot11/parser.py`：

```python
import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class OneBotMessage:
    post_type: str
    message_type: str
    user_id: str
    self_id: str
    text: str
    is_at: bool
    group_id: str | None = None


def parse_message_event(payload: dict) -> OneBotMessage | None:
    if not isinstance(payload, dict) or payload.get("post_type") != "message":
        return None
    message_type = str(payload.get("message_type") or "")
    if message_type not in {"private", "group"}:
        return None
    self_id = str(payload.get("self_id") or "")
    user_id = str(payload.get("user_id") or "")
    group_id = str(payload.get("group_id") or "") or None
    raw_message = str(payload.get("raw_message") or "")
    text, is_at = _extract_message(payload.get("message"), self_id, raw_message)
    return OneBotMessage(
        post_type="message",
        message_type=message_type,
        user_id=user_id,
        self_id=self_id,
        text=text,
        is_at=is_at,
        group_id=group_id,
    )


def _extract_message(message: Any, self_id: str, raw_message: str) -> tuple[str, bool]:
    if isinstance(message, str):
        return message, _cq_at_matches(message, self_id)
    if not isinstance(message, list):
        return raw_message, _cq_at_matches(raw_message, self_id)
    parts: list[str] = []
    is_at = False
    for segment in message:
        if not isinstance(segment, dict):
            continue
        seg_type = segment.get("type")
        data = segment.get("data") or {}
        if seg_type == "text":
            parts.append(str(data.get("text") or ""))
        elif seg_type == "at":
            if str(data.get("qq") or "") == self_id:
                is_at = True
    return "".join(parts), is_at


def _cq_at_matches(text: str, self_id: str) -> bool:
    return any(qq == self_id for qq in re.findall(r"\[CQ:at,qq=(\d+)\]", text))
```

- [ ] **Step 4: 运行确认通过**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_onebot_parser.py -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add integrations tests/unit/test_onebot_parser.py
git commit -m "feat: parse OneBot 11 message events"
```

---

### Task 7: 接入配置与 API

**Files:**
- Create: `integrations/config.py`
- Create: `app/routers/integrations.py`
- Modify: `app/schemas.py`（追加接入配置模型）
- Modify: `app/main.py`（注册路由）
- Test: `tests/api/test_integrations_api.py`

**Interfaces:**
- Consumes: Task 1 的 `EVENT_MESSAGE`（本任务不需要）；`extensions.storage.read_json/write_json`。
- Produces: `integrations/config.py` 的 `load_integrations(path) -> dict`、`save_integrations(path, data)`、`onebot_config(data) -> dict`（返回带默认值的配置）；`GET /api/integrations`、`PUT /api/integrations/onebot11`。Task 9 依赖配置读取。

- [ ] **Step 1: 写失败测试**

```python
import json


DEFAULTS = {
    "enabled": False,
    "access_token": "",
    "group_trigger": "at",
    "prefix": "",
    "default_persona_id": "",
}


def test_get_integrations_returns_defaults(client, tmp_path, monkeypatch):
    from app.routers import integrations as integrations_router

    path = tmp_path / "data" / "integrations.json"
    monkeypatch.setattr(integrations_router, "INTEGRATIONS_PATH", path)
    response = client.get("/api/integrations")
    assert response.status_code == 200
    body = response.json()
    assert body["onebot11"]["enabled"] is False
    assert body["onebot11"]["access_token_configured"] is False
    assert body["onebot11"]["group_trigger"] == "at"
    assert body["onebot11"]["connected"] is False
    assert body["onebot11"]["client_count"] == 0


def test_put_integrations_persists(client, tmp_path, monkeypatch):
    from app.routers import integrations as integrations_router

    path = tmp_path / "data" / "integrations.json"
    monkeypatch.setattr(integrations_router, "INTEGRATIONS_PATH", path)
    response = client.put(
        "/api/integrations/onebot11",
        json={"enabled": True, "access_token": "secret-token",
              "group_trigger": "prefix", "prefix": "机器人，", "default_persona_id": "p1"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is True
    assert body["access_token_configured"] is True
    assert "secret-token" not in response.text
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["onebot11"]["access_token"] == "secret-token"
    assert saved["onebot11"]["group_trigger"] == "prefix"


def test_put_integrations_rejects_invalid_trigger(client, tmp_path, monkeypatch):
    from app.routers import integrations as integrations_router

    path = tmp_path / "data" / "integrations.json"
    monkeypatch.setattr(integrations_router, "INTEGRATIONS_PATH", path)
    response = client.put("/api/integrations/onebot11", json={"group_trigger": "bogus"})
    assert response.status_code == 422
```

- [ ] **Step 2: 运行确认失败**

Run: `.\.venv\Scripts\python.exe -m pytest tests/api/test_integrations_api.py -q`
Expected: FAIL（`ModuleNotFoundError: app.routers.integrations`）。

- [ ] **Step 3: 实现**

`integrations/config.py`：

```python
from pathlib import Path

from extensions.storage import read_json, write_json


ONEBOT_DEFAULTS = {
    "enabled": False,
    "access_token": "",
    "group_trigger": "at",
    "prefix": "",
    "default_persona_id": "",
}


def load_integrations(path: Path) -> dict:
    return read_json(path)


def save_integrations(path: Path, data: dict) -> None:
    write_json(path, data)


def onebot_config(data: dict) -> dict:
    raw = data.get("onebot11") or {}
    config = dict(ONEBOT_DEFAULTS)
    config.update({key: raw.get(key, default) for key, default in ONEBOT_DEFAULTS.items()})
    if config["group_trigger"] not in {"at", "prefix"}:
        config["group_trigger"] = "at"
    return config
```

`app/schemas.py` 末尾追加：

```python
class OneBotConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    access_token: str | None = None
    group_trigger: Literal["at", "prefix"] | None = None
    prefix: str | None = None
    default_persona_id: str | None = None
```

`app/routers/integrations.py`：

```python
import json

from fastapi import APIRouter, Request

from app.routers.settings import require_local
from app.schemas import OneBotConfigUpdate
from integrations.config import load_integrations, onebot_config, save_integrations
from settings import Settings


router = APIRouter(prefix="/api/integrations", tags=["integrations"])
INTEGRATIONS_PATH = Settings.load().project_root / "data" / "integrations.json"


def _manager(request: Request):
    return getattr(request.app.state, "onebot", None)


def _onebot_response(request: Request) -> dict:
    config = onebot_config(load_integrations(INTEGRATIONS_PATH))
    manager = _manager(request)
    status = manager.status() if manager is not None else {
        "connected": False, "client_count": 0, "error": None
    }
    return {
        "enabled": config["enabled"],
        "access_token_configured": bool(config["access_token"]),
        "group_trigger": config["group_trigger"],
        "prefix": config["prefix"],
        "default_persona_id": config["default_persona_id"],
        "ws_path": "/api/onebot/ws",
        "connected": status.get("connected", False),
        "client_count": status.get("client_count", 0),
        "error": status.get("error"),
    }


@router.get("")
def get_integrations(request: Request) -> dict:
    require_local(request)
    return {"onebot11": _onebot_response(request)}


@router.put("/onebot11")
def update_onebot(payload: OneBotConfigUpdate, request: Request) -> dict:
    require_local(request)
    data = load_integrations(INTEGRATIONS_PATH)
    current = onebot_config(data)
    updates = payload.model_dump(exclude_unset=True)
    current.update({key: value for key, value in updates.items() if value is not None})
    data["onebot11"] = current
    save_integrations(INTEGRATIONS_PATH, data)
    manager = _manager(request)
    if manager is not None:
        manager.config_changed(current)
    return _onebot_response(request)
```

`app/main.py` 修改：

- import 区追加：`from app.routers.integrations import router as integrations_router`
- `app.include_router(plugins_router)` 之后追加：`app.include_router(integrations_router)`

- [ ] **Step 4: 运行确认通过**

Run: `.\.venv\Scripts\python.exe -m pytest tests/api/test_integrations_api.py -q`
Expected: PASS（`_manager` 不存在时返回占位状态，测试断言不受影响）。

- [ ] **Step 5: 提交**

```bash
git add integrations/config.py app/schemas.py app/routers/integrations.py app/main.py tests/api/test_integrations_api.py
git commit -m "feat: integrations config API"
```

---

### Task 8: IM 会话绑定与命令解析

**Files:**
- Create: `integrations/bindings.py`
- Create: `integrations/commands.py`
- Test: `tests/unit/test_im_bindings.py`
- Test: `tests/unit/test_im_commands.py`

**Interfaces:**
- Consumes: `extensions.storage.read_json/write_json`。
- Produces: `integrations/bindings.py` 的 `load_bindings(path) -> dict`、`save_bindings(path, data)`、`persona_for(bindings, chat_type, chat_id, default_persona_id) -> str | None`、`bind_persona(bindings, chat_type, chat_id, persona_id) -> None`；`integrations/commands.py` 的 `parse_command(text: str) -> tuple[str, str] | None`（返回 `("persona", 名称)`、`("approve", "")`、`("reject", "")`、`("help", "")`，非命令返回 None）。Task 9 依赖。

- [ ] **Step 1: 写失败测试**

`tests/unit/test_im_bindings.py`：

```python
import json

from integrations.bindings import (
    bind_persona,
    load_bindings,
    persona_for,
    save_bindings,
)


def test_bind_and_resolve(tmp_path):
    path = tmp_path / "bindings.json"
    bindings = load_bindings(path)
    bind_persona(bindings, "private", "20001", "p1")
    bind_persona(bindings, "group", "30001", "p2")
    save_bindings(path, bindings)
    assert persona_for(load_bindings(path), "private", "20001", "default-p") == "p1"
    assert persona_for(load_bindings(path), "group", "30001", "default-p") == "p2"
    assert persona_for(load_bindings(path), "private", "99999", "default-p") == "default-p"
    assert persona_for(load_bindings(path), "private", "99999", "") is None


def test_bindings_survive_file_roundtrip(tmp_path):
    path = tmp_path / "bindings.json"
    bindings = load_bindings(path)
    bind_persona(bindings, "group", "30001", "p2")
    save_bindings(path, bindings)
    assert json.loads(path.read_text(encoding="utf-8"))["group"]["30001"] == "p2"
```

`tests/unit/test_im_commands.py`：

```python
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
```

- [ ] **Step 2: 运行确认失败**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_im_bindings.py tests/unit/test_im_commands.py -q`
Expected: FAIL（`ModuleNotFoundError`）。

- [ ] **Step 3: 实现**

`integrations/bindings.py`：

```python
from pathlib import Path

from extensions.storage import read_json, write_json


def load_bindings(path: Path) -> dict:
    return read_json(path)


def save_bindings(path: Path, data: dict) -> None:
    write_json(path, data)


def persona_for(
    bindings: dict,
    chat_type: str,
    chat_id: str,
    default_persona_id: str,
) -> str | None:
    bound = (bindings.get(chat_type) or {}).get(chat_id)
    return bound or default_persona_id or None


def bind_persona(bindings: dict, chat_type: str, chat_id: str, persona_id: str) -> None:
    bindings.setdefault(chat_type, {})[chat_id] = persona_id
```

`integrations/commands.py`：

```python
import re


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
```

- [ ] **Step 4: 运行确认通过**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_im_bindings.py tests/unit/test_im_commands.py -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add integrations/bindings.py integrations/commands.py tests/unit/test_im_bindings.py tests/unit/test_im_commands.py
git commit -m "feat: IM session bindings and chat commands"
```

---

### Task 9: OneBot WebSocket 服务与 IM 消息路由

**Files:**
- Create: `agents/context_factory.py`
- Create: `integrations/onebot11/ws_server.py`
- Create: `integrations/onebot11/router.py`
- Modify: `persona/service.py`（追加按名称查找）
- Modify: `app/main.py`（接线 manager/router/订阅/路由）
- Modify: `integrations/config.py`（追加 `onebot_runtime_config`）
- Test: `tests/api/test_onebot_ws.py`

**Interfaces:**
- Consumes: Task 1 `EventBus`/`MessageEvent`/`EVENT_MESSAGE`；Task 6 parser；Task 7 config；Task 8 bindings/commands；现有 `PersonaAgentService`、`PersonaAgentContext`。
- Produces: `OneBotConnectionManager(config_provider)`（`status() -> dict`、`config_changed(config)`、`send_action(action, params)`、`handle_connection(websocket, event_bus)`）；`ImMessageRouter(agent_service, session_factory, bindings_path, integrations_path)`（`async handle(event)`）；WS 端点 `ws://127.0.0.1:<APP_PORT>/api/onebot/ws`；`agents/context_factory.py` 的 `persona_agent_context(session_factory, persona_id, conversation_id)` 与 `build_agent_runner(session_factory, agent_service)`；`persona.service.find_persona_by_name(session, name)`；`integrations.config.onebot_runtime_config(project_root)`。

- [ ] **Step 1: 写失败测试**

```python
from types import SimpleNamespace


def _prepare(client, tmp_path, monkeypatch):
    from integrations import config as integrations_config

    config_path = tmp_path / "integrations.json"
    bindings_path = tmp_path / "bindings.json"
    config_path.write_text(
        '{"onebot11": {"enabled": true, "access_token": "", '
        '"group_trigger": "at", "prefix": "", "default_persona_id": ""}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        integrations_config,
        "onebot_runtime_config",
        lambda root: integrations_config.onebot_config(
            integrations_config.load_integrations(config_path)
        ),
    )
    client.app.state.im_router.bindings_path = bindings_path
    client.app.state.im_router.integrations_path = config_path

    from persona.service import create_persona

    with client.app.state.session_factory() as session:
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

    client.app.state.agent_service = FakeAgent()
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
        # 不应产生回复；先发一条 @ 消息验证通道仍可回复
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
            "message": [{"type": "text", "data": {"text": f"/角色 小爱"}}],
            "raw_message": f"/角色 小爱",
        })
        action = ws.receive_json()
        assert action["params"]["message"] == "已绑定角色「小爱」。"
        bindings = (client.app.state.im_router.bindings_path).read_text(encoding="utf-8")
        assert persona_id in bindings


def test_disabled_integration_rejects_connection(client, tmp_path, monkeypatch):
    from integrations import config as integrations_config

    config_path = tmp_path / "integrations.json"
    config_path.write_text(
        '{"onebot11": {"enabled": false}}', encoding="utf-8")
    monkeypatch.setattr(
        integrations_config,
        "onebot_runtime_config",
        lambda root: integrations_config.onebot_config(
            integrations_config.load_integrations(config_path)
        ),
    )
    import pytest
    from starlette.websockets import WebSocketDisconnect

    try:
        with client.websocket_connect("/api/onebot/ws") as ws:
            data = ws.receive()
        assert data.get("code") == 1008
    except WebSocketDisconnect:
        # starlette TestClient 对 accept 前 close 可能直接抛 WebSocketDisconnect
        pass
```

- [ ] **Step 2: 运行确认失败**

Run: `.\.venv\Scripts\python.exe -m pytest tests/api/test_onebot_ws.py -q`
Expected: FAIL（`ModuleNotFoundError: agents.context_factory` 或 WebSocket 端点 404）。

- [ ] **Step 3: 实现**

`agents/context_factory.py`：

```python
from collections.abc import Callable

from agents.context import PersonaAgentContext
from app.models import Persona
from persona.service import PersonaNotFound, resolve_knowledge_scope


def persona_agent_context(
    session_factory: Callable,
    persona_id: str,
    conversation_id: str,
) -> PersonaAgentContext:
    with session_factory() as session:
        scope = resolve_knowledge_scope(session, persona_id)
        persona = session.get(Persona, persona_id)
        if persona is None:
            raise PersonaNotFound(persona_id)
        return PersonaAgentContext(
            persona_id=persona.id,
            workspace_id=scope.workspace_id,
            knowledge_space_ids=scope.knowledge_space_ids,
            conversation_id=conversation_id,
            persona_name=persona.name,
            persona_type=persona.persona_type,
            persona_profile=persona.profile_json,
            session_factory=session_factory,
        )


def build_agent_runner(session_factory: Callable, agent_service):
    """把 Agent 查询包装成同步 runner，供插件与 IM 路由复用。"""

    def run(question: str, persona_id: str, conversation_id: str) -> dict:
        context = persona_agent_context(session_factory, persona_id, conversation_id)
        result = agent_service.query(question, context)
        return {
            "status": result.status,
            "answer": result.answer,
            "specialist": result.specialist,
            "pending_action": result.pending_action,
            "tool_calls": list(result.tool_calls),
            "evidence": list(result.evidence),
            "trace": list(result.trace),
        }

    return run
```

`persona/service.py` 追加（文件顶部已有 `from sqlalchemy.orm import Session`，追加 `from sqlalchemy import select`）：

```python
def find_persona_by_name(session: Session, name: str) -> Persona | None:
    statement = (
        select(Persona)
        .where(Persona.workspace_id == LOCAL_WORKSPACE_ID, Persona.name == name)
        .order_by(Persona.created_at, Persona.id)
    )
    return session.scalars(statement).first()
```

`integrations/config.py` 末尾追加：

```python
def onebot_runtime_config(project_root: Path) -> dict:
    return onebot_config(load_integrations(project_root / "data" / "integrations.json"))
```

`integrations/onebot11/router.py`：

```python
import asyncio
import logging
from dataclasses import replace
from pathlib import Path
from typing import Any

from agents.context_factory import persona_agent_context
from extensions.events import MessageEvent
from integrations.bindings import (
    bind_persona,
    load_bindings,
    persona_for,
    save_bindings,
)
from integrations.commands import parse_command
from integrations.config import load_integrations, onebot_config
from persona.service import PersonaNotFound, find_persona_by_name


logger = logging.getLogger(__name__)


class ImMessageRouter:
    def __init__(
        self,
        agent_service,
        session_factory,
        bindings_path: Path,
        integrations_path: Path,
    ) -> None:
        self.agent_service = agent_service
        self.session_factory = session_factory
        self.bindings_path = bindings_path
        self.integrations_path = integrations_path
        self._locks: dict[str, asyncio.Lock] = {}

    def conversation_id(self, chat_type: str, chat_id: str) -> str:
        return f"im:onebot11:{chat_type}:{chat_id}"

    def _lock(self, key: str) -> asyncio.Lock:
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    async def handle(self, event: MessageEvent) -> None:
        if event.platform != "onebot11":
            return
        config = onebot_config(load_integrations(self.integrations_path))
        if event.chat_type == "group":
            if config["group_trigger"] == "at" and not event.is_at:
                return
            if config["group_trigger"] == "prefix":
                prefix = config.get("prefix") or ""
                if not prefix or not event.content.startswith(prefix):
                    return
                event = replace(event, content=event.content[len(prefix):].strip())
        async with self._lock(event.chat_id):
            command = parse_command(event.content)
            if command is not None:
                await self._handle_command(event, command)
            else:
                await self._handle_question(event)

    async def _handle_command(self, event: MessageEvent, command: tuple[str, str]) -> None:
        kind, argument = command
        if kind == "persona":
            self._bind_persona(event, argument)
        elif kind == "approve":
            await self._resume(event, True)
        elif kind == "reject":
            await self._resume(event, False)
        elif kind == "help":
            event.reply("可用命令：/角色 <名称> 绑定角色；/同意、/拒绝 处理待确认操作；/帮助 查看命令。")

    def _bind_persona(self, event: MessageEvent, name: str) -> None:
        with self.session_factory() as session:
            persona = find_persona_by_name(session, name)
        if persona is None:
            event.reply(f"没有找到名为「{name}」的角色，请到资料页确认角色名称。")
            return
        bindings = load_bindings(self.bindings_path)
        bind_persona(bindings, event.chat_type, event.chat_id, persona.id)
        save_bindings(self.bindings_path, bindings)
        event.reply(f"已绑定角色「{persona.name}」。")

    def _default_persona_id(self) -> str:
        config = onebot_config(load_integrations(self.integrations_path))
        return str(config.get("default_persona_id") or "")

    def _persona_for(self, event: MessageEvent) -> str | None:
        bindings = load_bindings(self.bindings_path)
        return persona_for(bindings, event.chat_type, event.chat_id, self._default_persona_id())

    def _context(self, event: MessageEvent) -> Any:
        return persona_agent_context(
            self.session_factory,
            self._persona_for(event),
            self.conversation_id(event.chat_type, event.chat_id),
        )

    async def _handle_question(self, event: MessageEvent) -> None:
        if self._persona_for(event) is None:
            event.reply("还没有绑定角色。请先用 /角色 <名称> 绑定，或在设置页配置默认角色。")
            return
        try:
            context = self._context(event)
        except PersonaNotFound:
            event.reply("绑定的角色不存在了，请重新用 /角色 <名称> 绑定。")
            return
        try:
            result = await asyncio.to_thread(
                self.agent_service.query, event.content, context
            )
        except Exception:
            logger.exception("im agent query failed")
            event.reply("角色暂时无法回复，请稍后再试。")
            return
        if result.status == "pending_confirmation":
            action = result.pending_action or {}
            tool = str(action.get("tool") or "操作")
            event.reply(f"需要确认：{tool}。回复 /同意 或 /拒绝。")
        else:
            event.reply(result.answer or "（空回复）")

    async def _resume(self, event: MessageEvent, approved: bool) -> None:
        if self._persona_for(event) is None:
            event.reply("还没有绑定角色。")
            return
        try:
            context = self._context(event)
            result = await asyncio.to_thread(
                self.agent_service.resume, context, "conversation", approved
            )
        except Exception:
            logger.exception("im agent resume failed")
            event.reply("操作处理失败，请稍后再试。")
            return
        if result.status == "pending_confirmation" or not result.answer:
            event.reply("当前没有待确认的操作。")
        else:
            event.reply(result.answer)
```

`integrations/onebot11/ws_server.py`：

```python
import asyncio
import logging
from collections.abc import Callable

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from extensions.events import EVENT_MESSAGE, EventBus, MessageEvent
from integrations.onebot11.parser import parse_message_event


logger = logging.getLogger(__name__)
router = APIRouter()


class OneBotConnectionManager:
    def __init__(self, config_provider: Callable[[], dict]) -> None:
        self._config_provider = config_provider
        self._connections: list[WebSocket] = []
        self._tasks: set[asyncio.Task] = set()
        self._error: str | None = None

    def config(self) -> dict:
        return self._config_provider()

    def config_changed(self, config: dict) -> None:
        if not config.get("enabled"):
            for websocket in list(self._connections):
                self._spawn(websocket.close(code=1008, reason="integration disabled"))

    def status(self) -> dict:
        config = self.config()
        return {
            "connected": bool(config.get("enabled")) and bool(self._connections),
            "client_count": len(self._connections),
            "error": self._error,
        }

    def send_action(self, action: str, params: dict) -> None:
        payload = {"action": action, "params": params, "echo": ""}
        for websocket in list(self._connections):
            self._spawn(websocket.send_json(payload))

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _token_ok(self, websocket: WebSocket) -> bool:
        token = str(self.config().get("access_token") or "")
        if not token:
            return True
        header = websocket.headers.get("authorization") or ""
        if header == f"Bearer {token}":
            return True
        return websocket.query_params.get("access_token") == token

    async def handle_connection(self, websocket: WebSocket, event_bus: EventBus) -> None:
        if not self.config().get("enabled"):
            await websocket.close(code=1008, reason="integration disabled")
            return
        if not self._token_ok(websocket):
            await websocket.close(code=1008, reason="invalid access token")
            return
        await websocket.accept()
        self._connections.append(websocket)
        self._error = None
        try:
            while True:
                payload = await websocket.receive_json()
                await self._publish_event(payload, event_bus)
        except WebSocketDisconnect:
            pass
        except Exception as exc:
            self._error = str(exc)
            logger.exception("onebot websocket error")
        finally:
            if websocket in self._connections:
                self._connections.remove(websocket)

    async def _publish_event(self, payload: dict, event_bus: EventBus) -> None:
        message = parse_message_event(payload)
        if message is None:
            return

        def reply(text: str) -> None:
            if message.message_type == "group":
                self.send_action(
                    "send_group_msg",
                    {"group_id": message.group_id, "message": text},
                )
            else:
                self.send_action(
                    "send_private_msg",
                    {"user_id": message.user_id, "message": text},
                )

        event = MessageEvent(
            platform="onebot11",
            chat_type=message.message_type,
            chat_id=message.group_id or message.user_id,
            user_id=message.user_id,
            content=message.text,
            raw_content=message.text,
            reply=reply,
            is_at=message.is_at,
        )
        await event_bus.publish(EVENT_MESSAGE, event)


@router.websocket("/api/onebot/ws")
async def onebot_ws(websocket: WebSocket) -> None:
    manager = websocket.app.state.onebot
    await manager.handle_connection(websocket, websocket.app.state.event_bus)
```

`app/main.py` 修改：

- import 区追加：

```python
from agents.context_factory import build_agent_runner
from app.routers.integrations import router as integrations_router
from app.routers.plugins import router as plugins_router
from extensions.events import EVENT_MESSAGE, EventBus
from extensions.manager import PluginManager
from integrations.config import onebot_runtime_config
from integrations.onebot11.router import ImMessageRouter
from integrations.onebot11.ws_server import OneBotConnectionManager
from integrations.onebot11.ws_server import router as onebot_ws_router
```

（若 Task 5 已加过 `plugins_router`/`EventBus`/`PluginManager` 的 import，合并去重。）

- 在 `app.state.agent_service = ...` 两行之后、include_router 之前追加：

```python
    app.state.event_bus = EventBus()
    app.state.onebot = OneBotConnectionManager(
        lambda: onebot_runtime_config(settings.project_root)
    )
    app.state.im_router = ImMessageRouter(
        app.state.agent_service,
        app.state.session_factory,
        settings.project_root / "data" / "im_bindings.json",
        settings.project_root / "data" / "integrations.json",
    )
    app.state.event_bus.subscribe(EVENT_MESSAGE, app.state.im_router.handle)
    app.state.plugin_manager = PluginManager(
        settings.project_root / "plugins",
        settings.project_root / "data",
        app.state.event_bus,
        agent_runner=build_agent_runner(app.state.session_factory, app.state.agent_service),
    )
    app.state.plugin_manager.load_all()
```

（若 Task 5 已创建 plugin_manager，替换那段为以上带 runner 的版本。）

- include_router 区追加：

```python
    app.include_router(onebot_ws_router)
    app.include_router(plugins_router)
    app.include_router(integrations_router)
```

- [ ] **Step 4: 运行确认通过**

Run: `.\.venv\Scripts\python.exe -m pytest tests/api/test_onebot_ws.py -q`
Expected: PASS。若 `receive_json` 超时（无回复事件），检查 group_trigger 判断与 `_prepare` 中 `onebot_runtime_config` 的 monkeypatch 是否生效。

- [ ] **Step 5: 提交**

```bash
git add agents/context_factory.py persona/service.py integrations tests/api/test_onebot_ws.py app/main.py
git commit -m "feat: OneBot 11 websocket integration with IM message routing"
```

---

### Task 10: 前端模块化拆分（外壳 + 视图 + 脚本）

**Files:**
- Create: `static/views/chat.html`
- Create: `static/views/personas.html`
- Create: `static/views/settings.html`
- Create: `static/js/common.js`
- Create: `static/js/chat.js`
- Create: `static/js/personas.js`
- Create: `static/js/settings.js`
- Modify: `static/index.html`（替换为外壳）
- Modify: `static/app.js`（替换为入口，函数迁出后只留外壳逻辑）
- Modify: `tests/api/test_web.py`
- Modify: `tests/unit/test_static_voice_assets.py`

**Interfaces:**
- Consumes: 现有 `static/index.html`（拆分前的完整单页）与 `static/app.js`（1685 行）作为搬运源。
- Produces: `window.PL.modules.<chat|upload|integrations|plugins|settings>` 注册表；`switchView(view)` 动态加载 `views/<name>.html` 并调用模块 `init()`；视图切换不刷新页面。Task 11 依赖此结构。

- [ ] **Step 1: 创建视图片段（纯搬运，不改 DOM）**

在 `static/` 下新建 `views/` 目录。用当前（拆分前）`static/index.html` 的以下区间原样复制为片段文件（`<section>` 标签连同内容一起复制，不要修改任何 id/class/结构）：

| 目标文件 | 来源（当前 index.html 行号） | 说明 |
| --- | --- | --- |
| `static/views/personas.html` | 第 26–75 行 | `<section id="upload-view" class="view is-hidden">…</section>` |
| `static/views/chat.html` | 第 77–94 行 | `<section id="chat-view" class="view is-hidden">…</section>`（含 chat-process-panel） |
| `static/views/settings.html` | 第 96–154 行 | `<section id="settings-view" class="view is-hidden">…</section>`（含全部设置表单） |

行号以执行时文件为准，按 `<section id="…">` 边界判断。三个片段文件都是片段（不是完整 HTML 文档），不含 `<!doctype>`。

- [ ] **Step 2: 替换 index.html 为外壳**

`static/index.html` 全文替换为（其余全局覆盖层原样保留）：

```html
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>PersonaLive</title>
  <link rel="stylesheet" href="/static/styles.css">
  <script src="/static/vendor/lucide.min.js" defer></script>
  <script src="/static/audio-recorder.js" defer></script>
  <script src="/static/js/chat.js" defer></script>
  <script src="/static/js/personas.js" defer></script>
  <script src="/static/js/settings.js" defer></script>
  <script src="/static/js/integrations.js" defer></script>
  <script src="/static/js/plugins.js" defer></script>
  <script src="/static/js/common.js" defer></script>
  <script src="/static/js/app.js" defer></script>
</head>
<body>
  <div class="app-shell">
  <aside class="site-sidebar">
    <div class="sidebar-inner">
      <nav class="primary-nav" aria-label="主导航">
        <button id="nav-chat" class="nav-item" type="button" data-view="chat" title="对话"><i data-lucide="message-circle"></i><span>对话</span></button>
        <button id="nav-upload" class="nav-item" type="button" data-view="upload" title="资料"><i data-lucide="library"></i><span>资料</span></button>
        <button id="nav-integrations" class="nav-item" type="button" data-view="integrations" title="接入"><i data-lucide="plug"></i><span>接入</span></button>
        <button id="nav-plugins" class="nav-item" type="button" data-view="plugins" title="插件"><i data-lucide="puzzle"></i><span>插件</span></button>
        <button id="nav-settings" class="nav-item" type="button" data-view="settings" title="设置"><i data-lucide="settings-2"></i><span>设置</span></button>
      </nav>
      <button id="sidebar-toggle" class="sidebar-toggle" type="button" aria-label="固定展开侧边栏" aria-pressed="false" title="固定展开侧边栏"><i class="sidebar-collapse-icon" data-lucide="panel-left-close"></i><i class="sidebar-expand-icon" data-lucide="panel-left-open"></i></button>
    </div>
  </aside>

  <main class="page-shell">
    <div id="view-root"></div>
  </main>
  </div>

  <div id="preview-backdrop" class="backdrop"></div><aside id="preview-drawer" class="preview-drawer" aria-hidden="true"><header class="drawer-header"><strong id="preview-title">资料预览</strong><button id="close-preview" class="close-button" type="button"><i data-lucide="x"></i></button></header><pre id="preview-content"></pre></aside>
  <dialog id="settings-confirm-dialog" class="settings-confirm-dialog" aria-labelledby="settings-confirm-title"><form method="dialog"><h2 id="settings-confirm-title">保存前确认</h2><p id="settings-confirm-detail" class="settings-confirm-detail"></p><div class="settings-confirm-actions"><button id="settings-confirm-cancel" class="button button-secondary" type="button">取消</button><button id="settings-confirm-submit" class="button button-primary" type="button">确认保存</button></div></form></dialog>
  <dialog id="delete-persona-dialog" class="settings-confirm-dialog" aria-labelledby="delete-persona-title"><form method="dialog"><h2 id="delete-persona-title">永久删除角色</h2><p id="delete-persona-detail" class="settings-confirm-detail">角色及其所有数据将永久删除，无法恢复。</p><p id="delete-persona-error" class="inline-error" role="alert"></p><div class="settings-confirm-actions"><button id="delete-persona-cancel" class="button button-secondary" type="button">取消</button><button id="delete-persona-confirm" class="button button-danger" type="button">永久删除</button></div></form></dialog>
</body>
</html>
```

- [ ] **Step 3: 创建 `static/js/common.js`**

内容 = 当前 `static/app.js` 中以下部分的原样搬移（函数体一字不改）：

- 第 2–58 行：`state`、`$`、`LLM_PRESETS`、`EMBEDDING_PRESETS`、`WEB_SEARCH_GUIDES`、`API_KEY_FIELDS`。
- 第 68–75 行：`icons`、`api`、`setText`。
- 第 273–309 行：`loadStatus`、`refreshSystemStatus`、`toggleStatusCards`、`formatDuration`。
- 第 307–425 行：`renderSystemStatusDetail`、`renderServiceStatus`。
- 第 1684–1685 行：`details`、`empty`。
- 第 1682–1683 行：`openPreview`、`closePreview`。

文件开头保留 `"use strict";`。

- [ ] **Step 4: 创建 `static/js/chat.js`、`static/js/personas.js`、`static/js/settings.js`**

每个文件开头：

```js
"use strict";
window.PL = window.PL || { modules: {} };
```

按以下映射把函数从当前 `static/app.js` 搬入对应文件（函数体一字不改；文件内函数顺序不要求与源文件一致）：

**chat.js**（末尾追加 `window.PL.modules.chat = { init: initChat };` 与 `initChat`/`bindChatEvents`，见 Step 5）：

| 函数 | 当前行号 |
| --- | --- |
| connectRealtime / closeRealtime / setRealtimeBusy | 567 / 582 / 611 |
| toggleChatProcess / resetChatProcess / renderChatProcess | 622 / 628 / 636 |
| showReplyLoading / replaceReplyLoading | 658 / 666 |
| handleRealtimeEvent / sendRealtime / clearRealtimeSubmission / failRealtimeSubmission / awaitRealtimeAcknowledgement / cancelRealtimeTurn | 673 / 732 / 738 / 747 / 755 / 768 |
| updateComposerControls / isConversationBusy | 772 / 784 |
| setAudioButton / renderAudioState / updateAudioClock / audioErrorMessage / startAudioRecording / handleUnexpectedAudioStop / finishAudioRecording / audioExtension / cancelAudioActivity | 788 / 800 / 820 / 827 / 834 / 864 / 874 / 909 / 917 |
| togglePersonaDrawer / closePersonaMenu / toggleChatSettingsMenu / closeChatSettingsMenu | 948 / 953 / 954 / 961 |
| submitQuestion / resizeComposer / appendMessage / appendVoiceControl / appendAudioMessage / updateAudioMessage / retryVoiceMessage / loadConversationMessages / clearConversation | 1114 / 1131 / 1138 / 1143 / 1163 / 1184 / 1189 / 1195 / 1204 |
| handleAgentResult / appendAnswer / collectStreamVoice / flushStreamVoice / enqueueVoiceAudio / playNextVoiceAudio / synthesizeAnswer / appendResultDetails / renderConfirmation / resumeAgent | 1214 / 1224 / 1225 / 1229 / 1235 / 1239 / 1246 / 1257 / 1258 / 1262 |

**personas.js**（末尾追加 `window.PL.modules.upload = { init: initPersonas };` 与 `initPersonas`/`bindPersonasEvents`，见 Step 5）：

| 函数 | 当前行号 |
| --- | --- |
| switchMaterialMode | 268 |
| summarizeFiles / uploadDraft / renderDraft / renderCandidates / renderDocuments / selectCandidate / saveDraft / confirmDraft / retryDocument / pollDraft / resetDraft | 427 / 432 / 447 / 457 / 472 / 488 / 493 / 501 / 512 / 516 / 521 |
| loadPersonas / fillPersonaSelect / renderPersonaList | 523 / 531 / 537 |
| loadEditPersona | 931 |
| loadEditReference / markTtsStep / syncEditTtsControls / syncEditTtsPreview / previewSelectedReference / confirmReferenceUpload / playEditReference / removeEditReference / generateEditPreview / openTtsSettings | 968 / 979 / 984 / 985 / 989 / 998 / 1016 / 1026 / 1035 / 1048 |
| requestPersonaDeletion / confirmPersonaDeletion / loadEditDocuments / saveEditPersona / uploadEditDocuments | 1049 / 1056 / 1081 / 1087 / 1099 |

同时创建两个最小占位文件（避免 Task 10 完成后外壳引用的脚本 404；Task 11 会替换为完整实现）：

`static/js/integrations.js`：

```js
"use strict";
window.PL = window.PL || { modules: {} };
window.PL.modules.integrations = { init: async function initIntegrations() {} };
```

`static/js/plugins.js`：

```js
"use strict";
window.PL = window.PL || { modules: {} };
window.PL.modules.plugins = { init: async function initPlugins() {} };
```

**settings.js**（末尾追加 `window.PL.modules.settings = { init: initSettings };` 与 `initSettings`/`bindSettingsEvents`，见 Step 5）：

| 函数 | 当前行号 |
| --- | --- |
| setApiKeyVisibilityIcon / ensureApiKeyValue / toggleApiKeyVisibility / copyApiKey / resetApiKeyInputs | 204 / 214 / 231 / 245 / 254 |
| keyStateLabel / buildConfigDetail | 377 / 382 |
| loadSettings / loadEmbeddingStatus / embeddingResourcePayload / installEmbedding / cancelEmbedding / removeEmbedding / openEmbeddingDirectory | 1274 / 1301 / 1330 / 1333 / 1343 / 1350 / 1358 |
| loadAsrStatus / saveAsrConfig / installAsr / removeAsr / cancelAsr / openAsrDirectory | 1366 / 1394 / 1402 / 1412 / 1452 / 1459 |
| loadTtsStatus / saveTtsConfig / installTts / removeTts / cancelTts / openTtsDirectory / previewTts | 1420 / 1468 / 1474 / 1483 / 1491 / 1498 / 1506 |
| normalizedUrl / inferProvider / applyLlmPreset / applyEmbeddingPreset / applyManagedEmbeddingPreset / markEmbeddingSelectionChanged / renderEmbeddingInstallAction / syncManagedEmbeddingPreset / renderEmbeddingSettings / renderEmbeddingWarning / renderWebSearchSettings | 1527 / 1528 / 1532 / 1536 / 1545 / 1550 / 1557 / 1574 / 1579 / 1588 / 1600 |
| requestSettingsSave / requestSettingsReset / openSettingsConfirmation / isHttpUrl / validateSettings / confirmSettingsAction / saveSettings / resetSettings | 1621 / 1622 / 1623 / 1634 / 1638 / 1656 / 1661 / 1672 |
| prepareSettingsSections | 172 |

注意：`loadPersonas`（personas.js）内部调用 `renderPersonaList`（chat.js），跨文件全局函数调用，无需改动。

- [ ] **Step 5: 重写 `static/js/app.js`（入口）**

`static/app.js` 全文替换为：

```js
"use strict";

const MODULES = {
  chat: { view: "chat", init: window.PL.modules.chat?.init },
  upload: { view: "personas", init: window.PL.modules.upload?.init },
  integrations: { view: "integrations", init: window.PL.modules.integrations?.init },
  plugins: { view: "plugins", init: window.PL.modules.plugins?.init },
  settings: { view: "settings", init: window.PL.modules.settings?.init },
};

function bindShellEvents() {
  $("sidebar-toggle").addEventListener("click", () => setSidebarPinned(!document.body.classList.contains("sidebar-pinned")));
  $("refresh-status")?.addEventListener("click", refreshSystemStatus);
  $("collapse-status")?.addEventListener("click", toggleStatusCards);
  document.querySelectorAll("[data-view]").forEach((button) => button.addEventListener("click", () => switchView(button.dataset.view)));
}

function setSidebarPinned(pinned) {
  document.body.classList.toggle("sidebar-pinned", pinned);
  $("sidebar-toggle").setAttribute("aria-pressed", String(pinned));
}

async function switchView(view) {
  if (view !== "chat" && (state.audioStarting || state.audioMode !== "idle")) cancelAudioActivity();
  const entry = MODULES[view];
  if (!entry) return;
  document.querySelectorAll("[data-view]").forEach((button) => button.classList.toggle("is-active", button.dataset.view === view));
  const root = $("view-root");
  if (!root) return;
  const response = await fetch(`/static/views/${entry.view}.html`);
  root.innerHTML = await response.text();
  if (entry.init) entry.init();
  icons();
}

document.addEventListener("DOMContentLoaded", async () => {
  bindShellEvents();
  await switchView("chat");
  await Promise.all([loadStatus(), loadPersonas(), loadSettings(), loadEmbeddingStatus(), loadAsrStatus(), loadTtsStatus()]);
  icons();
});
```

`setSidebarPinned` 原实现位于当前 `app.js` 内（`bindEvents` 中调用），搬入本文件。原 `bindEvents()` 删除，其事件绑定按模块拆分如下：

**`chat.js` 的 `bindChatEvents()`**（`initChat` 调用）：

```js
function initChat() {
  bindChatEvents();
  bindChatGlobalEvents();
  renderPersonaList();
}

function bindChatEvents() {
  $("question-form").addEventListener("submit", submitQuestion);
  $("chat-process-toggle").addEventListener("click", toggleChatProcess);
  $("question").addEventListener("input", resizeComposer);
  $("cancel-generation").addEventListener("click", cancelRealtimeTurn);
  $("record-audio").addEventListener("click", () => state.audioMode === "recording" ? finishAudioRecording() : startAudioRecording());
  $("cancel-audio").addEventListener("click", cancelAudioActivity);
  $("confirm-action").addEventListener("click", () => resumeAgent(true));
  $("cancel-action").addEventListener("click", () => resumeAgent(false));
  $("question").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      if (!$("send-question").disabled) $("question-form").requestSubmit();
    }
  });
  $("clear-conversation").addEventListener("click", clearConversation);
  $("chat-persona-toggle").addEventListener("click", togglePersonaDrawer);
  $("chat-settings-toggle").addEventListener("click", (event) => { event.stopPropagation(); toggleChatSettingsMenu(); });
  document.querySelectorAll("#chat-settings-menu button").forEach((button) => button.addEventListener("click", closeChatSettingsMenu));
  $("assistant-voice-toggle").addEventListener("change", () => localStorage.setItem("personalive:assistant-voice", $("assistant-voice-toggle").checked ? "on" : "off"));
}
```

注意：`closePersonaMenu`/`closeChatSettingsMenu` 的 `document` 级监听器每次切换视图都会重新注册，会累积。在 `chat.js` 用模块级标志只注册一次（`initChat` 已调用 `bindChatGlobalEvents`）：

```js
let chatGlobalEventsBound = false;

function bindChatGlobalEvents() {
  if (chatGlobalEventsBound) return;
  chatGlobalEventsBound = true;
  document.addEventListener("click", (event) => { if (!event.target.closest(".chat-persona-picker")) closePersonaMenu(); });
  document.addEventListener("click", (event) => { if (!event.target.closest(".chat-settings")) closeChatSettingsMenu(); });
}
```

`initChat` 中调用 `bindChatEvents()` 后再调用 `bindChatGlobalEvents()`，且 `bindChatEvents` 内不再包含这两条 `document.addEventListener`。

**`personas.js` 的 `initPersonas`/`bindPersonasEvents()`**：

```js
function initPersonas() {
  bindPersonasEvents();
  fillPersonaSelect($("edit-persona-select"), "请选择角色");
}

function bindPersonasEvents() {
  document.querySelectorAll('input[name="material-action"]').forEach((input) => input.addEventListener("change", () => switchMaterialMode(input.value)));
  $("document-files").addEventListener("change", () => summarizeFiles("document-files", "file-summary", "未选择文件"));
  $("edit-document-files").addEventListener("change", () => summarizeFiles("edit-document-files", "edit-file-summary", "添加文件或图片"));
  $("batch-form").addEventListener("submit", uploadDraft);
  $("reset-batch").addEventListener("click", resetDraft);
  $("save-draft").addEventListener("click", saveDraft);
  $("confirm-draft").addEventListener("click", confirmDraft);
  $("edit-persona-select").addEventListener("change", loadEditPersona);
  $("edit-persona-form").addEventListener("submit", saveEditPersona);
  $("edit-tts-reference").addEventListener("change", previewSelectedReference);
  $("edit-tts-confirm-upload").addEventListener("click", confirmReferenceUpload);
  $("edit-tts-preview-reference").addEventListener("click", playEditReference);
  $("edit-tts-generate-preview").addEventListener("click", generateEditPreview);
  $("edit-tts-open-settings").addEventListener("click", openTtsSettings);
  $("edit-tts-remove-reference").addEventListener("click", removeEditReference);
  $("edit-tts-enabled").addEventListener("change", syncEditTtsControls);
  $("edit-upload-form").addEventListener("submit", uploadEditDocuments);
  $("delete-persona").addEventListener("click", requestPersonaDeletion);
  $("delete-persona-cancel").addEventListener("click", () => $("delete-persona-dialog").close());
  $("delete-persona-confirm").addEventListener("click", confirmPersonaDeletion);
  $("close-preview").addEventListener("click", closePreview);
  $("preview-backdrop").addEventListener("click", closePreview);
}
```

**`settings.js` 的 `initSettings`/`bindSettingsEvents()`**：

```js
function initSettings() {
  bindSettingsEvents();
  prepareSettingsSections();
  loadSettings();
  loadEmbeddingStatus();
  loadAsrStatus();
  loadTtsStatus();
}

function bindSettingsEvents() {
  $("settings-form").addEventListener("submit", requestSettingsSave);
  $("reset-settings").addEventListener("click", requestSettingsReset);
  $("settings-confirm-cancel").addEventListener("click", () => $("settings-confirm-dialog").close());
  $("settings-confirm-submit").addEventListener("click", confirmSettingsAction);
  $("llm-provider").addEventListener("change", applyLlmPreset);
  ["openai-api-key", "embedding-api-key", "web-search-api-key"].forEach((id) => {
    $(`toggle-${id}`).addEventListener("click", () => toggleApiKeyVisibility(id));
    $(`copy-${id}`).addEventListener("click", () => copyApiKey(id));
  });
  $("embedding-provider").addEventListener("change", applyEmbeddingPreset);
  $("managed-embedding-preset").addEventListener("change", applyManagedEmbeddingPreset);
  $("embedding-model").addEventListener("input", markEmbeddingSelectionChanged);
  ["embedding-base-url", "embedding-model", "embedding-api-key", "embedding-dimensions", "chunk-size", "chunk-overlap"].forEach((id) => $(id).addEventListener("input", renderEmbeddingWarning));
  $("web-search-enabled").addEventListener("change", renderWebSearchSettings);
  $("web-search-provider").addEventListener("change", renderWebSearchSettings);
  ["web-search-api-key", "web-search-base-url"].forEach((id) => $(id).addEventListener("input", renderWebSearchSettings));
  $("save-asr").addEventListener("click", saveAsrConfig);
  $("install-asr").addEventListener("click", installAsr);
  $("cancel-asr").addEventListener("click", cancelAsr);
  $("remove-asr").addEventListener("click", removeAsr);
  $("open-asr-directory").addEventListener("click", openAsrDirectory);
  $("install-embedding").addEventListener("click", installEmbedding);
  $("cancel-embedding").addEventListener("click", cancelEmbedding);
  $("remove-embedding").addEventListener("click", removeEmbedding);
  $("open-embedding-directory").addEventListener("click", openEmbeddingDirectory);
  $("tts-enabled").addEventListener("change", saveTtsConfig);
  $("tts-use-gpu").addEventListener("change", saveTtsConfig);
  $("install-tts").addEventListener("click", installTts);
  $("cancel-tts").addEventListener("click", cancelTts);
  $("remove-tts").addEventListener("click", removeTts);
  $("open-tts-directory").addEventListener("click", openTtsDirectory);
  $("preview-tts").addEventListener("click", previewTts);
  document.querySelectorAll("[data-collapsible]").forEach((section) => section.addEventListener("toggle", () => {
    const label = section.querySelector(".section-toggle-label");
    if (label) label.textContent = section.open ? "收起" : "展开";
  }));
}
```

`data-collapsible` 的 toggle 逻辑若原实现与上面示例不同，以当前 `app.js` 第 164 行起的实际实现为准原样搬入。

- [ ] **Step 6: 更新 `tests/api/test_web.py`**

全文替换为：

```python
def test_root_redirects_to_web_workbench(client):
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/static/index.html"


def test_web_workbench_exposes_shell_and_module_views(client):
    response = client.get("/static/index.html")
    assert response.status_code == 200
    assert 'href="/static/styles.css"' in response.text
    assert 'src="/static/js/app.js"' in response.text
    for element_id in (
        "nav-upload",
        "nav-chat",
        "nav-settings",
        "nav-integrations",
        "nav-plugins",
        "view-root",
        "preview-drawer",
        "settings-confirm-dialog",
        "delete-persona-dialog",
    ):
        assert f'id="{element_id}"' in response.text

    views = {
        "personas": (
            "upload-view",
            "material-action",
            "edit-persona-select",
            "edit-persona-form",
            "generation-mode",
            "batch-form",
            "draft-editor",
            "delete-persona",
        ),
        "chat": (
            "chat-view",
            "chat-persona-menu",
            "chat-log",
            "question-form",
            "confirmation-panel",
            "chat-persona-toggle",
        ),
        "settings": (
            "settings-view",
            "settings-system-status",
            "settings-open-milvus",
            "settings-form",
            "reset-settings",
            "settings-confirm-cancel",
            "settings-confirm-submit",
            "llm-provider",
            "openai-api-key",
            "embedding-provider",
            "embedding-api-key",
            "managed-embedding-preset",
            "embedding-model-source",
            "embedding-device",
            "embedding-dimensions",
            "embedding-dimension-warning",
            "web-search-provider",
            "web-search-api-key",
            "web-search-base-url",
            "web-search-guide",
            "open-asr-directory",
            "open-tts-directory",
        ),
    }
    for name, ids in views.items():
        body = client.get(f"/static/views/{name}.html")
        assert body.status_code == 200
        for element_id in ids:
            assert f'id="{element_id}"' in body.text

    scripts = {
        "/static/js/app.js": (
            'fetch(`/static/views/${entry.view}.html`)',
            'switchView("chat")',
        ),
        "/static/js/personas.js": (
            'fetch("/api/persona-drafts/upload"',
            'fetch(`/api/persona-drafts/${state.draft.id}`',
            'fetch(`/api/persona-drafts/${state.draft.id}/confirm`',
            'fetch(`/api/personas/${persona.id}`, { method: "DELETE" })',
        ),
        "/static/js/chat.js": (
            'fetch(`/api/personas/${state.activePersona.id}/agent/query`',
            'fetch(`/api/personas/${state.activePersona.id}/agent/resume`',
        ),
        "/static/js/settings.js": (
            'fetch("/api/settings"',
            'method: "DELETE"',
            '确认重置配置',
        ),
    }
    for path, contracts in scripts.items():
        script = client.get(path)
        assert script.status_code == 200
        for contract in contracts:
            assert contract in script.text

    assert "...(state.draft.profile || {})" in client.get("/static/js/personas.js").text
    assert "https://api.deepseek.com" in client.get("/static/js/settings.js").text
    assert "https://dashscope.aliyuncs.com/compatible-mode/v1" in client.get("/static/js/settings.js").text
    assert "text-embedding-v4" in client.get("/static/js/settings.js").text
    assert "获取 Key 与填写示例" in client.get("/static/views/settings.html").text
    assert "请输入 API Key" in client.get("/static/js/settings.js").text
    assert "已保存，可输入新 Key 替换" in client.get("/static/js/settings.js").text
    assert "已配置，留空保持" not in client.get("/static/js/settings.js").text
    assert "保存前确认" in client.get("/static/index.html").text
    assert "永久删除，无法恢复" in client.get("/static/index.html").text
```

注意：若某契约字符串实际位于其他文件（例如 `method: "DELETE"` 或 `确认重置配置`），以 grep 结果为准调整归属文件，断言内容不变。

- [ ] **Step 7: 更新 `tests/unit/test_static_voice_assets.py`**

文件顶部加 helper：

```python
def read_view(name: str) -> str:
    return (ROOT / "static" / "views" / f"{name}.html").read_text(encoding="utf-8")
```

逐测试替换 `html = (ROOT / "static" / "index.html").read_text(...)` 为：

| 测试 | 读取来源 | 说明 |
| --- | --- | --- |
| test_cloud_asr_key_controls_are_removed | `read_view("settings")` | asr 控件在设置页 |
| test_local_asr_install_controls_are_present | `read_view("settings")` | 同上 |
| test_local_tts_install_controls_are_present | `read_view("settings") + read_view("personas")`（拼接后断言） | tts 设置 + edit-tts 控件在资料页 |
| test_tts_workflows_have_guidance_and_chat_controls | `read_view("settings") + read_view("personas") + read_view("chat")` | chat 控件在对话页 |
| test_chat_uses_single_compact_persona_menu | `read_view("chat")` | |
| test_chat_is_default_and_home_guidance_is_removed | `read_view("chat")`（upload-view 断言改为 `read_view("personas")`；settings-system-status/settings-open-milvus 断言改为 `read_view("settings")`；`switchView("chat")` 断言在 app.js） | |
| test_settings_service_status_covers_required_local_dependencies | `read_view("settings")` | |
| test_managed_embedding_controls_and_model_sources_are_present | `read_view("settings")` | |
| test_api_key_fields_support_reveal_and_copy | `read_view("settings")` | |
| test_primary_navigation_uses_collapsible_sidebar_with_chat_first | `index.html`（不改）+ `app.js` + `styles.css` | nav 顺序断言保留 |
| test_settings_are_rendered_as_one_continuous_page | `read_view("settings")` | |
| test_pages_drop_decorative_section_labels_and_repeated_intros | `read_view("chat") + read_view("personas") + read_view("settings")` | |
| test_chat_process_is_outside_bubbles_and_loading_state_exists | `read_view("chat")` | |

`script` 相关断言统一改为读取 `static/js/app.js`、`static/js/chat.js`、`static/js/settings.js`、`static/js/personas.js` 中实际包含目标字符串的文件（以 grep 为准，例如 `assistant-voice-toggle`、`collectStreamVoice`、`voicePlaybackQueue` 在 chat.js；`renderServiceStatus` 在 common.js；`prepareSettingsSections` 在 settings.js；`setSidebarPinned` 在 app.js；`appendAudioMessage`/`loadConversationMessages` 在 chat.js）。

`test_streaming_voice_is_synthesized_once_after_final_text` 与 `test_frontend_renders_persistent_audio_messages` 的脚本断言改读 `static/js/chat.js`。

- [ ] **Step 8: 跑全部前端相关测试**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_static_voice_assets.py tests/api/test_web.py -q`
Expected: PASS。

若某断言找不到字符串，用 `Select-String` 在 `static/js/` 下定位实际文件后调整读取来源；**断言内容一律不得删除或弱化**。

- [ ] **Step 9: 浏览器验证观感一致**

启动服务（需 Docker 或至少 MySQL 不可用时页面仍可打开）：

```powershell
.\.venv\Scripts\python.exe -B main.py
```

用 Microsoft Edge 打开 `http://127.0.0.1:8001/static/index.html`，依次检查对话页、资料页、设置页与拆分前截图逐项对比（侧边栏、顶部状态卡、表单布局、对话框）。发现布局差异时只修 `styles.css` 或视图片段，不改结构。

- [ ] **Step 10: 提交**

```bash
git add static tests/api/test_web.py tests/unit/test_static_voice_assets.py
git commit -m "refactor: split frontend into shell, views and module scripts"
```

---

### Task 11: 前端接入页与插件页

**Files:**
- Create: `static/views/integrations.html`
- Create: `static/views/plugins.html`
- Create: `static/js/integrations.js`
- Create: `static/js/plugins.js`
- Modify: `static/styles.css`（追加少量页面样式，复用现有 `.panel`/`.field`/`.settings-grid` 为主）
- Modify: `tests/api/test_web.py`（views 断言追加两个页面）
- Test: `tests/unit/test_frontend_modules.py`（新增，锁住新页面关键元素）

**Interfaces:**
- Consumes: Task 5 的 `/api/plugins`；Task 7 的 `/api/integrations`；Task 10 的 `window.PL.modules` 结构。
- Produces: `window.PL.modules.integrations.init` 与 `window.PL.modules.plugins.init`（Task 10 的 `app.js` 已引用）。

- [ ] **Step 1: 创建 `static/views/integrations.html`**

```html
<section id="integrations-view" class="view">
  <div class="panel integrations-panel">
    <div class="panel-heading"><div><h2>QQ 接入</h2><p class="panel-subtitle">通过 OneBot 11 正向 WebSocket 连接 NapCat 等转发端，在 QQ 中与角色对话。</p></div><span id="integration-status-pill" class="status-pill">未启用</span></div>
    <form id="onebot-form" class="settings-form">
      <div class="settings-grid settings-grid-form">
        <label class="toggle-field settings-feature-toggle"><input id="onebot-enabled" type="checkbox"><span>启用 QQ 接入</span></label>
        <label class="field base-url-field"><span>连接地址（NapCat 填这个）</span><input id="onebot-ws-path" readonly></label>
        <label class="field key-field"><span>Access Token（可选）</span><span class="secret-input"><input id="onebot-access-token" type="password" autocomplete="off" placeholder="留空则不鉴权"><span class="secret-actions"><button id="toggle-onebot-token" type="button" title="显示或隐藏" aria-label="显示或隐藏"><i data-lucide="eye"></i></button></span></span></label>
        <label class="field provider-field"><span>群聊触发</span><select id="onebot-group-trigger"><option value="at">@机器人</option><option value="prefix">关键词前缀</option></select></label>
        <label id="onebot-prefix-field" class="field is-hidden"><span>触发前缀</span><input id="onebot-prefix" placeholder="例如：机器人，"></label>
        <label class="field"><span>默认角色</span><select id="onebot-default-persona"><option value="">未设置</option></select></label>
      </div>
      <div class="asr-resource-bar"><div><strong id="onebot-state">未启用</strong><p id="onebot-status" class="inline-status"></p></div><div class="asr-actions"><button id="save-onebot" class="button button-primary" type="submit">保存配置</button></div></div>
      <p id="onebot-save-status" class="inline-status"></p>
    </form>
  </div>
</section>
```

- [ ] **Step 2: 创建 `static/views/plugins.html`**

```html
<section id="plugins-view" class="view">
  <div class="panel plugins-panel">
    <div class="panel-heading"><div><h2>插件</h2><p class="panel-subtitle">从项目 <code>plugins/</code> 目录自动加载，启用后即时生效；修改插件代码需重启应用。</p></div><span id="plugins-count" class="status-pill">0 个插件</span></div>
    <div id="plugin-list" class="plugin-list"></div>
    <p id="plugins-status" class="inline-status"></p>
  </div>
</section>
```

- [ ] **Step 3: 创建 `static/js/integrations.js`**

```js
"use strict";
window.PL = window.PL || { modules: {} };
window.PL.modules.integrations = { init: initIntegrations };

let integrationLoaded = false;

async function initIntegrations() {
  bindIntegrationEvents();
  await loadIntegrations();
  await fillPersonaOptions();
}

function bindIntegrationEvents() {
  $("onebot-form").addEventListener("submit", saveOnebotConfig);
  $("onebot-group-trigger").addEventListener("change", renderOnebotTrigger);
  $("toggle-onebot-token").addEventListener("click", () => {
    const input = $("onebot-access-token");
    input.type = input.type === "password" ? "text" : "password";
  });
}

function renderOnebotTrigger() {
  $("onebot-prefix-field").classList.toggle("is-hidden", $("onebot-group-trigger").value !== "prefix");
}

function renderIntegrationStatus(cfg) {
  const pill = $("integration-status-pill");
  if (!cfg.enabled) {
    pill.textContent = "未启用";
    pill.className = "status-pill";
    setText("onebot-state", "未启用");
    setText("onebot-status", "启用并保存配置后，NapCat 可连接此地址。");
  } else if (cfg.connected) {
    pill.textContent = "已连接";
    pill.className = "status-pill status-pill-ok";
    setText("onebot-state", "已连接");
    setText("onebot-status", `当前 ${cfg.client_count} 个 OneBot 客户端在线。`);
  } else {
    pill.textContent = "未连接";
    pill.className = "status-pill status-pill-warn";
    setText("onebot-state", "等待连接");
    setText("onebot-status", cfg.error || "NapCat 尚未连接，请检查地址与 Token。");
  }
}

async function loadIntegrations() {
  const data = await api(fetch("/api/integrations"));
  const cfg = data.onebot11 || {};
  $("onebot-enabled").checked = Boolean(cfg.enabled);
  $("onebot-ws-path").value = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${cfg.ws_path || "/api/onebot/ws"}`;
  $("onebot-access-token").value = "";
  $("onebot-group-trigger").value = cfg.group_trigger || "at";
  $("onebot-prefix").value = cfg.prefix || "";
  renderOnebotTrigger();
  renderIntegrationStatus(cfg);
}

async function fillPersonaOptions() {
  const personas = await api(fetch("/api/personas"));
  const select = $("onebot-default-persona");
  select.innerHTML = '<option value="">未设置</option>';
  const current = (await api(fetch("/api/integrations"))).onebot11.default_persona_id || "";
  for (const persona of personas) {
    const option = document.createElement("option");
    option.value = persona.id;
    option.textContent = persona.name;
    select.append(option);
  }
  select.value = current;
}

async function saveOnebotConfig(event) {
  event.preventDefault();
  setText("onebot-save-status");
  const payload = {
    enabled: $("onebot-enabled").checked,
    group_trigger: $("onebot-group-trigger").value,
    prefix: $("onebot-prefix").value.trim(),
    default_persona_id: $("onebot-default-persona").value,
  };
  const token = $("onebot-access-token").value.trim();
  if (token) payload.access_token = token;
  try {
    const saved = await api(fetch("/api/integrations/onebot11", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }));
    renderIntegrationStatus(saved);
    setText("onebot-save-status", "配置已保存" + (saved.enabled ? "，等待客户端连接。" : "，接入已关闭。"));
  } catch (reason) {
    setText("onebot-save-status", reason.message || reason);
  }
}
```

- [ ] **Step 4: 创建 `static/js/plugins.js`**

```js
"use strict";
window.PL = window.PL || { modules: {} };
window.PL.modules.plugins = { init: initPlugins };

async function initPlugins() {
  await renderPluginList();
}

async function renderPluginList() {
  const list = $("plugin-list");
  list.innerHTML = "";
  let plugins = [];
  try {
    plugins = await api(fetch("/api/plugins"));
  } catch (reason) {
    setText("plugins-status", reason.message || reason);
    return;
  }
  $("plugins-count").textContent = `${plugins.length} 个插件`;
  if (!plugins.length) {
    list.append(empty("还没有插件。在项目 plugins/ 目录放入带 plugin.json 的插件后重启应用。"));
    return;
  }
  for (const plugin of plugins) {
    list.append(renderPluginCard(plugin));
  }
}

function renderPluginCard(plugin) {
  const card = document.createElement("div");
  card.className = "plugin-card";
  card.dataset.plugin = plugin.name;
  const head = document.createElement("div");
  head.className = "plugin-card-head";
  const title = document.createElement("div");
  const name = document.createElement("strong");
  name.textContent = plugin.name;
  const meta = document.createElement("span");
  meta.textContent = `v${plugin.version}${plugin.author ? ` · ${plugin.author}` : ""}`;
  title.append(name, meta);
  const toggle = document.createElement("label");
  toggle.className = "toggle-field";
  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkbox.checked = Boolean(plugin.enabled);
  checkbox.addEventListener("change", () => setPluginEnabled(plugin.name, checkbox.checked));
  const toggleText = document.createElement("span");
  toggleText.textContent = plugin.enabled ? "启用" : "禁用";
  toggle.append(checkbox, toggleText);
  head.append(title, toggle);
  card.append(head);
  if (plugin.description) {
    const description = document.createElement("p");
    description.className = "plugin-description";
    description.textContent = plugin.description;
    card.append(description);
  }
  if (plugin.error) {
    const error = document.createElement("p");
    error.className = "inline-error";
    error.textContent = plugin.error;
    card.append(error);
  } else {
    card.append(renderPluginConfig(plugin));
  }
  return card;
}

function renderPluginConfig(plugin) {
  const details = document.createElement("details");
  details.className = "plugin-config";
  const summary = document.createElement("summary");
  summary.textContent = "配置";
  details.append(summary);
  const form = document.createElement("div");
  form.className = "settings-grid settings-grid-four";
  const inputs = {};
  const config = plugin.config || {};
  const keys = Object.keys(config);
  if (!keys.length) {
    const note = document.createElement("p");
    note.className = "inline-status";
    note.textContent = "该插件没有可配置项。";
    details.append(note);
    return details;
  }
  for (const key of keys) {
    const label = document.createElement("label");
    label.className = "field";
    const span = document.createElement("span");
    span.textContent = key;
    label.append(span);
    const value = config[key];
    if (typeof value === "boolean") {
      const input = document.createElement("input");
      input.type = "checkbox";
      input.checked = value;
      inputs[key] = input;
      label.append(input);
    } else {
      const input = document.createElement("input");
      input.value = value === null || value === undefined ? "" : String(value);
      inputs[key] = input;
      label.append(input);
    }
    form.append(label);
  }
  details.append(form);
  const actions = document.createElement("div");
  actions.className = "asr-actions";
  const save = document.createElement("button");
  save.type = "button";
  save.className = "button button-secondary";
  save.textContent = "保存配置";
  save.addEventListener("click", async () => {
    const updates = {};
    for (const [key, input] of Object.entries(inputs)) {
      updates[key] = input.type === "checkbox" ? input.checked : input.value;
    }
    try {
      await api(fetch(`/api/plugins/${plugin.name}/config`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ config: updates }),
      }));
      await renderPluginList();
    } catch (reason) {
      setText("plugins-status", reason.message || reason);
    }
  });
  actions.append(save);
  details.append(actions);
  return details;
}

async function setPluginEnabled(name, enabled) {
  try {
    await api(fetch(`/api/plugins/${encodeURIComponent(name)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    }));
    await renderPluginList();
  } catch (reason) {
    setText("plugins-status", reason.message || reason);
    await renderPluginList();
  }
}
```

注意：`empty(text)` 来自 common.js（返回空状态 `<p>`）。

- [ ] **Step 5: `static/styles.css` 追加**

文件末尾追加（复用现有变量与按钮样式）：

```css
/* 接入页与插件页 */
.integrations-panel .settings-grid { max-width: 720px; }
.plugin-list { display: grid; gap: 12px; }
.plugin-card { border: 1px solid #bfc6cc; border-radius: 10px; padding: 14px 16px; background: #fff; }
.plugin-card-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.plugin-card-head > div { display: flex; align-items: baseline; gap: 10px; min-width: 0; }
.plugin-card-head strong { font-size: 15px; }
.plugin-card-head span { color: #6b7280; font-size: 12px; }
.plugin-description { margin: 8px 0 0; color: #374151; font-size: 13px; }
.plugin-config { margin-top: 10px; }
.plugin-config summary { cursor: pointer; font-size: 13px; color: #1f6feb; }
.plugin-config .settings-grid { margin-top: 8px; }
.status-pill-ok { background: #e6f4ea; color: #137333; }
.status-pill-warn { background: #fef7e0; color: #b06000; }
```

- [ ] **Step 6: 更新 `tests/api/test_web.py` 的 views 断言**

在 Task 10 的 views 字典中追加两个条目（其余不动）：

```python
        "integrations": (
            "integrations-view",
            "onebot-enabled",
            "onebot-ws-path",
            "onebot-access-token",
            "onebot-group-trigger",
            "onebot-default-persona",
            "save-onebot",
        ),
        "plugins": (
            "plugins-view",
            "plugin-list",
            "plugins-count",
        ),
```

- [ ] **Step 7: 新增 `tests/unit/test_frontend_modules.py`**

```python
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read_script(name: str) -> str:
    return (ROOT / "static" / "js" / f"{name}.js").read_text(encoding="utf-8")


def test_integrations_module_registers_and_uses_api():
    script = read_script("integrations")
    assert "window.PL.modules.integrations" in script
    assert 'fetch("/api/integrations")' in script
    assert 'fetch("/api/integrations/onebot11",' in script
    assert "renderIntegrationStatus" in script


def test_plugins_module_registers_and_uses_api():
    script = read_script("plugins")
    assert "window.PL.modules.plugins" in script
    assert 'fetch("/api/plugins")' in script
    assert "setPluginEnabled" in script
    assert "renderPluginList" in script


def test_shell_registers_new_module_entries():
    script = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
    assert "integrations: { view: \"integrations\"" in script
    assert "plugins: { view: \"plugins\"" in script
```

- [ ] **Step 8: 运行测试**

Run: `.\.venv\Scripts\python.exe -m pytest tests/api/test_web.py tests/unit/test_frontend_modules.py -q`
Expected: PASS。

- [ ] **Step 9: 浏览器验证**

启动服务后打开 `http://127.0.0.1:8001/static/index.html`，确认侧边栏出现“接入”“插件”，两页可打开、可保存配置，样式与其他页面一致。

- [ ] **Step 10: 提交**

```bash
git add static tests/api/test_web.py tests/unit/test_frontend_modules.py
git commit -m "feat: integrations and plugins pages"
```

---

### Task 12: 全量验证与收尾

**Files:**
- Modify: 视验证结果（一般为空）

- [ ] **Step 1: 全量后端测试**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit tests/api -q`
Expected: 全部通过（原 115 个 + 新增约 30 个）。

- [ ] **Step 2: 启动冒烟**

```powershell
docker compose up -d
.\.venv\Scripts\python.exe -B main.py
```

检查：

```powershell
Get-NetTCPConnection -LocalPort 8001 -State Listen
Invoke-RestMethod http://127.0.0.1:8001/api/integrations
Invoke-RestMethod http://127.0.0.1:8001/api/plugins
```

Expected: 8001 在监听；两个 API 返回 JSON（onebot11 默认关闭、plugins 为空数组或含已放置插件）。

- [ ] **Step 3: 端到端冒烟（可选，需 NapCat）**

有 NapCat 时：设置页“接入”启用 QQ 接入 → NapCat 配置 `ws://127.0.0.1:8001/api/onebot/ws` → 私聊发送消息 → 收到角色回复；群聊 @ 机器人触发。

- [ ] **Step 4: 浏览器截图对比**

用 Microsoft Edge 打开 `http://127.0.0.1:8001/static/index.html`，对对话/资料/设置/接入/插件五页截图，与拆分前记录对比，确认观感一致；发现差异仅修样式或片段，不改结构。

- [ ] **Step 5: 停止服务并提交收尾**

```powershell
$conn = Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue
if ($conn) { Stop-Process -Id $conn.OwningProcess }
```

```bash
git status --short
git add -A
git commit -m "chore: final verification for plugin framework and OneBot integration"
```

若无改动则跳过提交。
