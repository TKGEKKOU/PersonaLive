# YUMENO

[![Release](https://img.shields.io/github/v/release/TKGEKKOU/yumeno)](https://github.com/TKGEKKOU/yumeno/releases)
[![Platform](https://img.shields.io/badge/platform-Windows-0078d6)](https://github.com/TKGEKKOU/yumeno/releases)

> 曾用名 PersonaLive；代码、容器名、API 校验头等内部标识仍沿用 `personalive` 前缀，作为工程别名保留。

YUMENO 是一个**本地优先、生产级**的角色化多 Agent RAG 平台。它以 LangGraph 1.2.9 为底层运行时，将
**人设驱动的多 Agent 编排**与**自适应纠错式 RAG** 深度耦合：每个角色拥有独立的身份设定、知识空间、
会话状态与记忆，通过 Supervisor 多 Agent 架构统一调度知识检索、联网查询、长期记忆与角色管理四类
专业 Worker，通过质量门与有界纠错机制抑制幻觉，最终以角色口吻生成接地、可信、可追溯的回复。

平台强调**本地优先与离线可用**：LLM 与 Embedding 通过任意 OpenAI-compatible 接口接入，语音识别
（Qwen3-ASR）、语音合成（Qwen3-TTS）与向量化（Qwen3-Embedding）均可本地部署、按需安装；
角色、对话与记忆等应用数据存本地 SQLite，向量知识由 Docker 托管的 Milvus 持久化，无需注册与登录。

---

## 核心架构

### 设计取舍与工程实践

平台在架构设计上围绕**可控性、成本与安全**三个维度做取舍：

- **可控性优先于自动化**：Agent 并非"全自主黑盒"，而是确定性图结构（workflow）与 LLM 决策
  （agent）的混合编排——所有循环受计数器硬约束，质量门负责最终放行，必要时以保守拒答收尾，
  杜绝无限循环与不可控输出；
- **上下文膨胀与幻觉抑制**：多 Agent 分工将单一上下文窗口的占用控制在可预测范围——每个
  Worker 只携带与其领域相关的工具与状态，避免"上下文膨胀（Context Bloat）"稀释模型对关键
  信息的注意力，从而抬升幻觉风险与 Token 成本；
- **成本与延迟的显式权衡**：批量证据评分一次 LLM 调用覆盖全部候选（避免逐块调用放大成本）；
  高置信度结果直通质量门（省一次校验调用）；评测题集按内容指纹缓存（资料不变不重复生成）。
  共同原则是"可省则省、该查必查"；
- **安全边界前置**：角色作用域由服务端权威解析（检索前 `expr` 过滤），Agent 无法越权访问其他
  角色知识；变更类工具一律经 Human-in-the-loop 确认；工具异常由 LangGraph `ToolNode` 捕获并
  以 `ToolMessage(status=error)` 反馈模型自修正；结构化解析失败统一 fail-closed（宁可拒答，
  不输出无依据内容）。

### 1. Agent 系统：人设主 Agent + 四类专业 Worker

对话层采用 **LangGraph Supervisor 集中式监督架构**（星形拓扑）：仅 `persona_supervisor` 对用户可见，
四个 Worker 负责执行受限领域的子任务，最终答复统一由 Supervisor 结合完整人设资料整合生成。

**构建方式**：基于 LangChain 1.3.14 的 `create_agent()` 标准入口，底层封装 LangGraph 执行机制
（模型调用 → 工具决策 → 执行 → 结果整合的闭环）；通过**中间件（Middleware）**的 `dynamic_prompt`
钩子在每次模型调用前动态注入角色人设、持久记忆与回复约束，无需为每个角色维护静态提示词模板；
通过 `context_schema` 将角色/会话上下文（`PersonaAgentContext`）作为不可变上下文注入工具运行时，
实现严格的**作用域过滤**。

工具调用协议建立在模型原生 **Function Calling** 能力之上：LLM 负责意图识别并生成结构化的工具
调用指令，框架负责执行、状态管理与结果反馈；本项目在此基础上叠加 Supervisor 路由、作用域过滤
与人工确认，将"单次函数调用"扩展为"可审计的多轮 Agent 协作"。

**Worker 分工与最小权限**：

| Worker | 职责 | 工具集 |
|---|---|---|
| knowledge | 检索当前角色知识空间，返回可验证的结构化证据 | RAG 检索工具 |
| web | 查询实时公开信息，区分联网事实与角色知识 | Tavily / 免 key 联网搜索 |
| memory | 读取与维护角色的长期记忆（`persona_memories`） | 记忆读写工具 |
| management | 管理角色资料与文档（变更类操作需人工确认） | 文档/资料管理工具 |

**Agent 间通信（Handoff）**：为每个 Worker 动态创建 `delegate_to_*` handoff 工具，通过
`Command(PARENT, goto=worker_node, update=...)` 将控制权从 Supervisor 子图交还父图对应节点；
Worker 执行完毕后由 `finalize` 节点将结果封装为**结构化交接合同**（knowledge 走 JSON 协议，
其余走文本摘要），以 `ToolMessage` 按 `tool_call_id` 回填，保证工具调用协议闭环。

**Human-in-the-loop（人机协同）**：变更类工具（新增资料、修改人设、删除文档）在执行前调用
LangGraph `interrupt()` 触发中断，返回待审批的操作详情（工具名 + 参数）；前端弹出确认框，
用户批准或拒绝后通过 `Command(resume=...)` 从检查点恢复，未完成的 Worker 步骤不会重跑。

**上下文工程：记忆分层**（短期记忆 + 长期记忆）：

- **短期记忆（线程范围）**：`langgraph-checkpoint-sqlite` 将会话状态持久化到本地 SQLite，按
  `thread_id = persona_id:conversation_id` 隔离不同会话；无数据库时自动回退 `MemorySaver`。
- **长期记忆（跨会话）**：`persona_memories` 表按角色持久化用户偏好等事实，由 memory Worker
  写入，Supervisor 每次请求经中间件注入，实现跨会话的个性化响应。

### 2. RAG 系统：自适应纠错式检索增强生成

#### 文档处理管线

文档经 **MarkItDown** 解析为 Markdown，由**结构感知分块器**（标题感知 + 递归字符切分，
`chunk_size=1000 / overlap=150`）切成语义连贯的块，并为每个块写入元数据
（`workspace_id`、`knowledge_space_id`、`category`、来源路径、`chunk_id`），以**内容哈希**实现
增量入库，重复上传自动去重。

#### 两阶段检索：候选召回 + 精化排序

检索层采用**两阶段策略**。第一阶段**候选召回（Recall）**——双路混合检索 + 排名融合：

- **Dense 路**：本地/远程 Embedding 生成稠密向量，Milvus `HNSW` 索引（IP 内积）负责语义召回；
- **Sparse 路**：Milvus 内置 **BM25 函数**将原文转为稀疏向量（`SPARSE_INVERTED_INDEX`），
  负责关键词、专有名词的精确匹配（中文经 jieba 分词）；
- **融合**：两路候选经 **RRF（Reciprocal Rank Fusion）** 融合排序，兼顾语义与词汇信号；
- **强制作用域过滤**：`expr` 在检索阶段即按角色知识空间过滤，先过滤、再排序，杜绝跨角色数据串扰。

第二阶段**精化排序（Ranking）**由执行流中的"批量证据评分"完成：一次 LLM 调用对召回候选做
相关性精化并过滤干扰块（详见下方执行流）。该设计以轻量的 LLM 评分替代独立 Reranker 模型，
在保持排序质量的同时避免额外引入重推理模型。

#### Adaptive / Corrective 执行流

默认 `RAG_PIPELINE=default` 走**自适应纠错图**（LangGraph `StateGraph` 手工编排，全部循环有硬边界）：

```text
路由 -> 检索 -> 批量证据评分 -> 生成 -> 质量门 -> （通过 / 改写重试 / 联网回退 / 纠错重生成 / 拒答）
```

- **路由**：意图识别区分知识问答 / 闲聊 / 能力询问 / 联网需求，支持强制走知识链路；
- **批量证据评分**：一次 LLM 调用对全部候选块打标（`relevant_ids` + 整体置信度），
  过滤仅词面重合的干扰块，避免逐块调用放大成本；
- **查询改写**：置信度不足时把口语化问题改写成适合向量检索的陈述式查询，次数受
  `MAX_REWRITE_COUNT` 限制；
- **质量门**：高置信度直接放行（省一次 LLM 调用）；否则 LLM 校验 **grounded（事实接地）** 与
  **useful（问题解决）**，并给出纠错动作（重新生成 / 再检索 / 联网 / 拒答）；
- **有界纠错**：`missing_points` / `unsupported_claims` 反馈给下一轮生成，所有循环受计数器
  与联网开关约束，杜绝无限循环；最终兜底为保守拒答；
- **流式与可观测**：`graph.stream(stream_mode="values")` 逐节点回调，前端实时展示
  "当前环节 / 问题" 进度；`trace` 仅记录可公开摘要（节点、片段数、置信度、是否有答案）。

也可通过 `RAG_PIPELINE=simple` 切换到轻量 `retrieve -> generate` 模式。

#### 评测体系（离线、免标注、可缓存）

内置 RAG 离线评测（`rag/eval/`），一键对任意角色运行：

- **题集档位**：`fast`（5 题）/ `standard`（10 题）/ `thorough`（15 题），由 LLM 基于角色资料
  免标注生成，并内置无关问题探针；按内容指纹 + 档位缓存，资料不变不重复调用 LLM；
- **指标**：检索质量（`recall@k` / `precision@k` / `MRR` / `hit@1`）、生成质量
  （`grounded` / `useful`）、拒答率、正常作答率、通过率、置信度、检索与整链路延迟（均值 / P95）、
  跨角色隔离校验；逐条详情含问题、回答、判定与证据；
- **AI 分析**：`POST /api/eval/analyze` 对结果做异常 / 性能 / 功能归因分析（强制 ≤200 字）。

### 3. 语音与角色表现

- **本地 TTS**：Qwen3-TTS（GGUF + qwen3tts.cpp，Vulkan/CPU），每角色可上传独立参考音色做
  **声线克隆**；回复按句流式分段合成（`/synthesize/stream`），异常采样自动重试；
- **口型同步**：文本 → 音素（viseme）序列与实时音频能量混合驱动 Live2D
  `ParamMouthOpenY / ParamMouthForm`，支持中文 / 日文 / 英文音素映射，也可经 WebSocket 驱动
  VTube Studio；
- **本地 ASR**：Qwen3-ASR-0.6B（CUDA 12.8）+ Silero VAD，自动识别中 / 英 / 日，离线可用；
- **Live2D 面板**：支持 Cubism 2 / 3 / 4 模型，自动眨眼、呼吸、拖拽缩放，聆听 / 思考 / 应答状态
  与语音链路联动。

### 4. 集成与扩展

- **Agent Skills 动态技能包**：技能 = 提示词包 + 可选工具集（`agents/skills/` 内置、
  `data/skills/` 自定义），由 `load_skill` 按需加载，加载后提示词注入 Supervisor
  system prompt、对应工具才对模型可见，从源头缓解工具过载；支持上传标准
  `SKILL.md` 技能包（zip，≤25MB、可含多个技能，frontmatter 用 `tool-names`
  声明工具），与既有 JSON 技能并存；
- **MCP 工具接入**：通过 `MultiServerMCPClient`（`langchain-mcp-adapters`）连接外部
  MCP 服务器（stdio / streamable_http / SSE），工具自动注册进 `ToolSpec` 表并被技能引用。
  插件页支持**运行时启停与热重连**（无需重启应用）、30 秒轮询刷新连接状态；
  stdio 启动命令受**白名单 / 黑名单 / 内联代码与危险参数拦截**保护
  （`MCP_ALLOW_ARBITRARY_STDIO=true` 仅跳过白名单）；按角色在**角色管理页**按服务器
  粒度授权，未授权角色即使技能引用该服务器工具也不可见（fail-closed），授权变更即时生效；
- **QQ 官方机器人（WebSocket）**：开放平台 AppID / AppSecret 直连官方 WebSocket 网关，
  支持群聊（@机器人或关键词前缀触发）与单聊；自动换取并缓存 access_token、心跳保活、
  断线自动重连（Resume 补发），消息经 EventBus 路由到 Agent，回复走官方消息接口；
- **OneBot 11（QQ）**：通过 OneBot 11 正向 WebSocket 连接 NapCat 等转发端，外部渠道
  消息经 EventBus 广播路由到 Agent，渠道扩展不触碰 Agent 逻辑；
- **插件系统**：插件通过受限 `agent_runner` 与 EventBus 安全接入，不直接持有数据库或图对象；
- **实时会话**：WebSocket 带轮次 ID、确认事件与停止旧轮次能力，REST 与 WS 共用同一 LangGraph 会话；
- **桌面壳**：PyWebView 桌面模式自动检查 Docker、启动 Compose 与 FastAPI，并统一托管
  ASR / TTS / Embedding 运行时资源。

---

## 技术栈

| 领域 | 选型 |
|---|---|
| 编排框架 | LangGraph 1.2.9（`StateGraph` / `create_agent` / Middleware / Checkpoint） |
| Agent 入口 | LangChain 1.3.14 `create_agent()` + `dynamic_prompt` 中间件 |
| 状态持久化 | `langgraph-checkpoint-sqlite` / `MemorySaver` |
| 向量数据库 | Milvus（Dense HNSW-IP + BM25 sparse + RRF） |
| 文档解析 | MarkItDown → 结构感知分块（jieba 中文分词） |
| 联网搜索 | Tavily / 博查 / 自定义协议，或内置免 key 搜索（free-search-mcp，本地优先、无需 API key） |
| 后端 | FastAPI / Uvicorn / SQLAlchemy / SQLite |
| 基础设施 | Docker Compose（etcd / MinIO / Milvus / Attu）+ 本地 SQLite |
| 语音 | Qwen3-TTS（本地）/ Qwen3-ASR（本地）/ Silero VAD / Web Audio |
| 角色渲染 | PIXI.js + Live2D Cubism 2/3/4 + VTube Studio |

## 环境要求

- Python 3.11
- Docker Desktop（运行 Milvus、etcd、MinIO 与 Attu）
- OpenAI-compatible Chat 与 Embedding 接口（LLM / Embedding 供应商无关）

## 快速开始（桌面端）

1. 从 [GitHub Releases](https://github.com/TKGEKKOU/yumeno/releases) 下载安装程序
   `YUMENO-Setup-0.1.1.exe` 并安装（或下载便携版 zip，解压后双击 `YUMENO.exe`）；
2. 首次使用前请安装并启动 [Docker Desktop](https://www.docker.com/products/docker-desktop/)；
3. 双击桌面快捷方式启动 YUMENO——启动页会自动检查 Docker、拉起 Milvus 与本地服务；
4. 在"设置"页配置 LLM API Key（OpenAI 兼容接口），然后创建角色、上传资料、开始对话。

## 从源码运行（开发）

以下命令均在项目根目录执行。

### 一键启动

首次运行会自动创建 `.venv`、安装依赖、生成 `.env`，并拉起 Docker 基础设施；
之后重复运行直接复用已有环境。

```powershell
.\scripts\start.ps1              # 桌面端（默认，自动拉起 Docker 与本地服务）
.\scripts\start.ps1 -Server      # 仅启动 FastAPI 服务端，浏览器访问 http://127.0.0.1:17000
.\scripts\start.ps1 -NoInstall   # 跳过依赖安装（环境已就绪时更快）
```

前置条件：安装 [Python 3.11](https://www.python.org/downloads/)（勾选 Add to PATH）
与 [Docker Desktop](https://www.docker.com/products/docker-desktop/)。

### 手动启动

1. 创建 Python 3.11 虚拟环境并安装依赖：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e . -r requirements-dev.txt
```

应用数据存储于本地 SQLite 文件（`data/yumeno.db`，随 `data/` 目录被 Git 忽略），
无需额外初始化；首次启动时自动建库建表。

2. 启动基础设施并等待健康：

```powershell
docker compose up -d
docker compose ps
```

等待 `etcd`、`standalone` 显示为 `healthy` 后再启动应用。

3. 启动服务端或桌面端：

```powershell
# 服务端（浏览器访问 http://127.0.0.1:17000）
.\.venv\Scripts\python.exe -B main.py

# 或桌面端（先安装桌面依赖）
.\.venv\Scripts\python.exe -m pip install -r requirements-desktop.txt
.\.venv\Scripts\python.exe -B desktop_main.py
```

桌面窗口关闭时停止 FastAPI 与 ASR Worker，Docker 容器默认继续运行。生成 Windows onedir 包与安装程序：

```powershell
.\scripts\build_windows.ps1
```

## 配置说明

LLM、Embedding 与联网搜索配置在前端"设置"页完成，保存到 `data/local_settings.json`。

关键 `.env` 项：

```env
DB_PATH=data/yumeno.db                 # SQLite 应用数据库（角色/对话/记忆/会话状态）
RAG_PIPELINE=default                 # default(自适应纠错) | simple(检索直出)
MAX_REWRITE_COUNT=2                  # 查询改写次数上限
MAX_GENERATION_RETRY=2               # 生成纠错次数上限
DEFAULT_CONFIDENCE_THRESHOLD=0.75    # 高置信度直通阈值
COLLECTION_NAME=yumeno_knowledge_v1  # Milvus 集合名（更换 Embedding 维度时需换新集合）
```

> **多副本注意**：`docker-compose.yml` 使用固定容器名与绑定数据目录，
> 同一台机器运行第二个副本时请按 README 末尾的
> "下载副本与 Docker 重名冲突"一节调整容器名与宿主机端口，不要删除数据目录。

## 主要接口

- `POST /api/personas`：创建角色及其独立知识空间。
- `GET /api/personas`：列出本地角色。
- `POST /api/knowledge-spaces/{space_id}/documents/upload`：批量上传并生成 Markdown 预览。
- `POST /api/documents/{job_id}/confirm`：确认并异步入库（Milvus）。
- `POST /api/documents/{job_id}/retry-index`：重试失败的入库任务。
- `GET /api/documents/{job_id}`：查询任务状态。
- `POST /api/personas/{persona_id}/rag/query`：执行角色隔离的完整 RAG 查询。
- `POST /api/personas/{persona_id}/agent/query`：人设主 Agent 对话与 Worker 委派。
- `POST /api/personas/{persona_id}/agent/resume`：确认 / 拒绝已暂停的管理操作（HITL 恢复）。
- `POST /api/eval/run`：启动离线 RAG 评测（`tier` / `max_cases` / `web_fallback`）。
- `GET /api/eval/status`、`GET /api/eval/results`：评测进度与结果。
- `POST /api/eval/analyze`：AI 归因分析（≤200 字）。
- `POST /api/tts/personas/{persona_id}/conversations/{conversation_id}/synthesize/stream`：
  按句流式 TTS 合成。
- `POST /api/personas/{persona_id}/conversations/{conversation_id}/voice-messages`：保存语音消息。
- `POST /api/voice-messages/{message_id}/transcribe`：本地转写语音并交给 Agent。
- `WS /ws/personas/{persona_id}/conversations/{conversation_id}`：实时会话
  （轮次 ID、确认事件、停止旧轮次）。

请求不能提交 `workspace_id` 或 `knowledge_space_id`：服务端始终从路径中的角色解析作用域，
Milvus 写入、删除与查询均携带工作空间与知识空间过滤条件，杜绝跨角色数据串扰。

## 访问入口

- Web 工作台：<http://127.0.0.1:17000/static/index.html>
- API 文档：<http://127.0.0.1:17000/docs>
- 组件状态：<http://127.0.0.1:17000/api/status>
- SQLite 管理（datasette，只读）：<http://127.0.0.1:17000/sqlite/>
- Attu（Milvus 控制台）：<http://127.0.0.1:17003>

## 进程管理

```powershell
Get-NetTCPConnection -LocalPort 17000 -State Listen |
    Select-Object LocalAddress, LocalPort, OwningProcess
```

停止 FastAPI：

```powershell
$conn = Get-NetTCPConnection -LocalPort 17000 -State Listen -ErrorAction SilentlyContinue
if ($conn) {
    Stop-Process -Id $conn.OwningProcess
    Write-Host "Stopped process PID:" $conn.OwningProcess
} else {
    Write-Host "No service is listening on port 17000"
}
```

停止 Docker 基础设施并保留数据：

```powershell
docker compose down
```

## 许可证与合规

本地语音合成基于 [Lunar Astral Agents](https://gitee.com/TayunStarry/Lunar-Astral-Agents) 的
Qwen3-TTS 子系统构建，受其 **Non-Commercial License** 约束，仅可用于非商业用途；完整许可证位于
`third_party/lunar_tts/LICENSE`，Windows 发行包会附带该文件。TTS 模型文件不随项目分发，
首次启用时从 ModelScope 下载。

Live2D 官方示例模型仅用于本地评估，正式分发前请替换为拥有分发许可的模型。

## 后续计划

- 会话摘要中间件（短期记忆超长压缩）与记忆向量化检索；
- 独立 Reranker 精排（BGE / Qwen3-Reranker）与多模态（图片）向量化；
- **A2A / DeepAgents 生态评估**：验证跨框架智能体通信协议（A2A）与分层规划框架
  （DeepAgents）的接入可行性，为多智能体协作与任务规划能力预留扩展空间；
- GPT-SoVITS / 在线 TTS、数字人事件、OBS 与直播平台适配器。
