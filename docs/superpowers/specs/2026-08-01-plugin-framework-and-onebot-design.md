# 插件框架 + QQ（OneBot 11）接入 + 前端模块化 设计

日期：2026-08-01

## 1. 背景与目标

PersonaLive 目前是单机角色化 RAG 语音助手，只有浏览器/桌面窗口一个对话入口。
本迭代借鉴 AstrBot 的架构思路（仅借鉴设计，不复制任何代码），补上两块能力：

1. **插件框架（最小可用）**：本地 `plugins/` 目录扫描、插件清单、生命周期钩子、
   进程内事件总线，为后续扩展提供统一底座。
2. **QQ 平台接入（OneBot 11）**：通过正向 WebSocket 接收 NapCat 等转发端的
   消息，把 QQ 私聊/群聊接到现有角色 Agent，实现“在 QQ 里和角色对话”。

同时按用户要求把前端拆成模块化结构：新增“接入”“插件”两个与“对话”“资料”
“设置”并列的导航模块；为保证可维护性，前端拆为单页外壳 + 模块视图/脚本，
观感与拆分前一致。

约束：全部自研，只使用 OneBot 11 开放协议标准，不引入 AstrBot 代码，不引入
现成 OneBot SDK。插件为本地可信代码，不做沙箱。

## 2. 范围界定

### 本迭代做

- 插件框架：`plugins/` 扫描、manifest 解析、加载/卸载、启用/禁用、事件总线、
  插件配置持久化、设置页插件列表。
- OneBot 11 正向 WebSocket 接入：私聊与群聊消息、@ 触发、角色绑定与切换命令、
  确认操作命令、连接状态展示。
- 前端模块化：`index.html` 外壳 + `views/*.html` 视图片段 + `js/*.js` 模块脚本，
  新增“接入”“插件”两个页面。
- 新增后端 API：接入配置、插件列表/启用/配置。
- 测试：插件框架单测、OneBot 消息解析单测、接入/插件 API 测试、模拟 WS 客户端
  集成测试；同步更新受前端拆分影响的现有断言。

### 本迭代不做

- 插件市场、远程下载安装、运行时热加载插件代码。
- Telegram/Discord/微信等其他平台。
- QQ 图片、语音、文件消息的收发（第一版只回文本）。
- 插件沙箱/权限隔离。
- 群聊关键词前缀以外的复杂触发规则。

## 3. 架构总览

```
NapCat(QQ) ──WS──> OneBot 适配器(integrations/onebot11)
                        │ 解析为标准消息
                        ▼
                  EventBus(extensions/events.py)
                        │
          ┌─────────────┴──────────────┐
          ▼                            ▼
  IM 内置处理器(路由到角色Agent)   用户插件监听器(可选)
          │
          ▼
   PersonaAgentService.query()/resume()
          │
          ▼
   回复文本 → OneBot 适配器 → WS action → QQ
```

新增两个后端包：

- `extensions/`：插件框架核心，与应用无关，可独立测试。
- `integrations/`：平台接入层；第一版只含 OneBot 11。

前端保持单页外壳，模块视图与脚本按模块拆分。

## 4. 插件框架（extensions/）

### 4.1 插件目录与清单

项目根目录 `plugins/` 下每个子目录是一个插件，目录内必须包含
`plugin.json`（清单）和 Python 入口文件。

`plugin.json` 字段：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `name` | 是 | 唯一名称，`[a-z0-9_-]`，与目录名一致 |
| `version` | 是 | 语义化版本，如 `0.1.0` |
| `description` | 否 | 一句话描述 |
| `author` | 否 | 作者 |
| `entry` | 是 | 入口文件名，如 `main.py` |
| `config_schema` | 否 | 配置项说明（名称、默认值、标题），用于设置页展示 |

### 4.2 文件与职责

- `extensions/manifest.py`：`PluginManifest` 数据类；校验并解析 `plugin.json`
  （第一版仅支持 JSON）。
- `extensions/loader.py`：扫描 `plugins/`，按 manifest 导入入口模块；任何单个
  插件加载失败只标记该插件错误，不影响其他插件与主服务。
- `extensions/events.py`：`EventBus` 与 `MessageEvent`。
- `extensions/api.py`：`PluginContext`，插件运行时 API。
- `extensions/manager.py`：`PluginManager`，统筹加载/卸载/启用/禁用/配置读写。

### 4.3 事件总线

```python
class EventBus:
    def subscribe(self, event: str, handler) -> Callable[[], None]: ...
    async def publish(self, event: str, payload) -> None: ...
```

- handler 支持同步函数与 `async` 函数，`publish` 自动区分执行。
- 订阅返回取消函数，便于插件卸载时自动清理。
- 第一版事件常量：`message`（平台消息）。消息事件定义：

```python
@dataclass(frozen=True)
class MessageEvent:
    platform: str          # "onebot11"
    chat_type: str         # "private" | "group"
    chat_id: str           # 私聊为 user_id，群聊为 group_id
    user_id: str
    content: str           # 纯文本内容（已去掉 @ 与触发前缀）
    raw_content: str       # 原始文本
    reply: Callable[[str], None]  # 发送文本回复
```

### 4.4 插件生命周期与 API

入口模块约定：

```python
async def on_load(ctx: PluginContext) -> None: ...
async def on_unload() -> None: ...

@ctx.on_event("message")
async def handle_message(event: MessageEvent) -> None: ...
```

`PluginContext` 提供：

- `config`：当前配置字典（默认值合并用户配置）。
- `save_config(updates)`：保存并持久化。
- `query_agent(question, persona_id, conversation_id)`：调用角色 Agent。
- `log`：带插件名前缀的日志。

### 4.5 配置与状态持久化

- `data/plugin_configs.json`：`{插件名: {配置键: 值}}`。
- `data/plugin_state.json`：`{插件名: true|false}`（启用状态）。
- 启用/禁用即时生效（加载/卸载插件对象）；修改插件代码需重启应用。
- 插件加载失败时在列表中显示错误信息，可重试加载。

## 5. OneBot 11 接入（integrations/）

### 5.1 连接方式

正向 WebSocket：NapCat 等 OneBot 实现作为客户端连接
`ws://127.0.0.1:<APP_PORT>/api/onebot/ws`。端口沿用 FastAPI 的 `APP_PORT`
（默认 8001），不新增端口。连接地址在设置页展示，端口跟随配置。

鉴权：配置 `access_token` 后，要求连接请求头携带
`Authorization: Bearer <token>`，不匹配则用 WebSocket 1008 关闭。未配置 token
时允许匿名连接（仅限本机场景，文档注明风险）。

### 5.2 文件与职责

- `integrations/config.py`：读写 `data/integrations.json`。
- `integrations/onebot11/parser.py`：OneBot 11 消息事件 JSON → `MessageEvent`
  （纯函数，重点单测）。
- `integrations/onebot11/ws_server.py`：WebSocket 端点、连接管理、发送 action、
  token 校验。
- `integrations/onebot11/router.py`：消息路由（命令识别、角色绑定、调用 Agent、
  处理确认、回复）。
- `integrations/onebot11/service.py`：状态汇总与协调。

### 5.3 消息解析

解析 `post_type == "message"` 的事件：

- 遍历 `message` 段数组：`text` 段拼接纯文本；`at` 段若 `data.qq == self_id`
  记为“@ 了机器人”。
- 私聊事件直接触发；群聊事件默认要求 @ 机器人，可配置为关键词前缀触发
  （`group_trigger = at | prefix`，prefix 内容可配置）。
- 触发时从 `content` 中去掉 @ 文本或前缀，避免把触发语喂给 Agent。

### 5.4 角色绑定与会话

- 每个 IM 会话（私聊 `user_id` / 群聊 `group_id`）独立绑定一个角色，绑定存
  `data/im_bindings.json`。
- 会话的 `conversation_id` 固定为 `im:onebot11:<chat_type>:<chat_id>`，保证
  每个会话有独立对话线程。
- 未绑定角色时使用配置的默认角色；仍无默认角色则回复提示语。

### 5.5 聊天内命令

| 命令 | 行为 |
| --- | --- |
| `/角色 <名称>` | 按名称精确匹配角色并绑定当前会话，回复确认 |
| `/同意` | 通过当前会话待确认的写操作 |
| `/拒绝` | 拒绝当前会话待确认的写操作 |
| `/帮助` | 列出可用命令 |

- 命令优先于普通消息处理。
- Agent 返回 `pending_confirmation` 时，回复操作描述并提示 `/同意`、`/拒绝`；
  后续直接调用 `PersonaAgentService.resume()`（现有实现忽略 specialist 参数，
  只需会话上下文与 approved）。

### 5.6 回复与并发

- 回复通过 WS 发送 `send_private_msg` / `send_group_msg` action，消息为纯文本。
- 每个会话一把 `asyncio.Lock`：同一会话消息串行处理，不同会话可并行。
- `PersonaAgentService.query()` 是同步调用，在 `asyncio.to_thread` 中执行，
  避免阻塞事件循环。

### 5.7 配置项

`data/integrations.json`：

```json
{
  "onebot11": {
    "enabled": false,
    "access_token": "",
    "group_trigger": "at",
    "prefix": "",
    "default_persona_id": ""
  }
}
```

`enabled = false` 时 WS 端点仍然注册但拒绝新连接；状态接口返回原因。

## 6. 后端 API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/integrations` | 接入列表与状态（启用、连接数、错误信息、绑定数） |
| PUT | `/api/integrations/onebot11` | 保存 QQ 接入配置 |
| GET | `/api/plugins` | 插件列表（名称、版本、描述、作者、启用状态、配置、错误） |
| PUT | `/api/plugins/{name}` | 启用/禁用插件 |
| PUT | `/api/plugins/{name}/config` | 保存插件配置并重载插件 |
| WS | `/api/onebot/ws` | OneBot 11 正向 WebSocket 端点 |

接入与插件 API 沿用现有 `require_local` 校验（仅本机可访问）。

## 7. 前端模块化拆分

### 7.1 目录结构

```
static/
  index.html              # 外壳：侧边栏、主容器、全局对话框、脚本引入
  views/
    chat.html             # 对话页
    personas.html         # 资料页（原 upload-view 内容）
    integrations.html     # 接入页（新增）
    plugins.html          # 插件页（新增）
    settings.html         # 设置页（原 settings-view 内容）
  js/
    common.js             # 共享：$、api、setText、icons、状态刷新等
    chat.js
    personas.js
    settings.js
    integrations.js       # 新增
    plugins.js            # 新增
    app.js                # 入口：侧边栏、路由、视图加载、模块注册
  styles.css              # 第一版保持单文件
```

### 7.2 视图加载与模块初始化

- `index.html` 只保留 `app-shell`、侧边栏（5 个导航项：对话/资料/接入/插件/
  设置）、`#view-root` 容器、全局对话框（资料预览、设置确认、删除确认）。
- `app.js` 定义模块注册表：

```js
window.PL = { modules: {} };
// 每个模块 js 调用 window.PL.modules["chat"] = { init: initChat };
```

- `switchView(name)`：`fetch("/static/views/" + name + ".html")` → 注入
  `#view-root` → 调用对应模块 `init()` → `lucide.createIcons()`。
- 脚本保持普通 `<script>` 顺序加载（非 ES module），避免桌面 `file://`
  场景的 CORS/导入限制；共享逻辑在 `common.js`，各模块挂全局注册表。
- 切换视图不做整页刷新，顶部服务状态与内存状态保持，观感与拆分前一致。

### 7.3 新增页面

- **接入页（integrations.html）**：QQ 接入卡片——启用开关、连接地址（只读，
  随 APP_PORT 显示）、access_token、群聊触发方式、前缀、默认角色选择、当前
  连接状态（未启用/未连接/已连接 N 个客户端）、错误信息、保存按钮。
- **插件页（plugins.html）**：已加载插件列表——名称、版本、作者、描述、
  启用开关、配置编辑（按 config_schema 生成表单）、加载错误提示。

### 7.4 受影响的现有测试

- `tests/api/test_web.py` 与 `tests/unit/test_static_voice_assets.py` 直接读取
  `static/index.html` 断言元素 id，拆分后改为读取对应 `views/*.html`（对话元素
  查 `views/chat.html`、设置元素查 `views/settings.html` 等），断言内容不变。
- 新增对 `views/integrations.html`、`views/plugins.html` 关键 id 的断言。

## 8. 数据流

QQ 私聊消息示例：

```
NapCat 收到私聊消息
  → WS 推送 message 事件
  → parser 解析为 MessageEvent(chat_type="private", content="你好")
  → EventBus.publish("message", event)
  → IM 内置处理器：
      1. 命中命令？→ 处理命令
      2. 查 im_bindings 找角色，无则用默认角色
      3. asyncio.to_thread(agent_service.query, 问题, 会话上下文)
      4. pending_confirmation → 提示 /同意 /拒绝
      5. 否则 reply(答案)
  → event.reply() → WS 发送 send_private_msg action → QQ
```

群聊消息额外要求 @ 机器人（或配置的前缀），解析时剥离触发语。

## 9. 错误处理

- 插件清单损坏/缺字段、入口导入失败：跳过该插件，列表显示错误，其余插件与
  主服务不受影响。
- WS 消息 JSON 非法或非 message 事件：记日志忽略。
- Agent 调用异常：回复“角色暂时无法回复，请稍后再试”，记录完整异常日志。
- WS 断连：移除连接记录；重连由 OneBot 客户端负责。
- 角色名称不存在：`/角色` 回复未找到，并给出“设置页可查看现有角色”提示。

## 10. 测试计划

### 单元测试

- manifest：合法/非法 JSON、缺必填字段、名称与目录不一致。
- loader：正常加载、单插件失败不影响整体。
- EventBus：订阅/发布/取消、async 与同步 handler、异常隔离。
- parser：私聊、群聊、@ 检测、纯文本拼接、CQ 码 at、前缀剥离。
- 角色绑定：绑定/读取/默认角色/无效角色。
- 命令解析：`/角色`、`/同意`、`/拒绝`、未知命令。

### API 测试

- GET/PUT `/api/integrations`：保存后文件内容、enabled 状态。
- GET `/api/plugins`、PUT 启用/禁用、PUT 配置保存。

### 集成测试

- 用 TestClient 的 WebSocket 连接模拟 NapCat：连接 → 发 message 事件 → 收到
  回复 action；测试中替换 `app.state.agent_service` 为固定答案的假服务，
  不依赖 MySQL/Milvus。

### 前端验证

- 更新现有 HTML 断言到 views 文件，全量 `pytest tests/unit -q` 与
  `pytest tests/api -q` 通过。
- 浏览器打开新旧两版页面截图对比（对话、资料、设置），确认观感一致。

## 11. 实施顺序

1. `extensions/` 插件框架（含单测）。
2. `integrations/` OneBot 11 接入（含解析与集成测试）。
3. 前端拆分（index.html 外壳 + views + js，更新现有断言）。
4. 前端新增接入页与插件页。
5. 全量测试 + 截图对比验证。

详细任务拆解见实施计划文档。
