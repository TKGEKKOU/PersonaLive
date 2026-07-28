# PersonaLive

PersonaLive 是一个本地优先、无登录的角色化 RAG 后端。每个角色拥有独立知识空间；
文档经 MarkItDown 转为 Markdown 后，使用内容哈希增量写入 Milvus，并通过 Dense
Embedding 与 BM25 sparse 检索、RRF 融合完成问答。

默认 Adaptive/Corrective RAG 流程保留规则路由、证据评分、查询改写、可选 Tavily
搜索、答案接地与有效性检查、有限重试、引用和节点 trace。也可切换到简单的
`retrieve -> generate` 模式。

## 当前能力

- LangGraph 采用“人设主 Agent + 知识 / 联网 / 记忆 / 管理”四类 Worker；Worker
  负责检索或执行工具，人设主 Agent 结合完整角色资料统一生成最终答复。
- 天气、新闻等事实查询先给结论，再给符合人设的简短建议；Web Worker 会向主
  Agent 交接关键事实、来源及不确定性。
- 设置页在本地保存 LLM、Embedding 与联网搜索配置，不依赖 `.env` 中的模型或
  Key，并会随供应商变化显示对应填写指南。
- 联网搜索支持关闭、Tavily、博查和自定义来源；自定义来源采用博查/Bing 兼容协议，
  RAG 与 Agent 统一消费标准化的 `Document`。

## 环境要求

- Python 3.11
- Docker Desktop（运行 MySQL、Milvus、etcd、MinIO 和 Attu）
- OpenAI-compatible Chat 与 Embedding 接口

## 本地启动

以下命令均在项目根目录执行。项目需要 Python 3.11 和已启动的 Docker Desktop。

### 首次启动

1. 创建本地配置。不要将 `.env` 提交到 Git：

```powershell
Copy-Item .env.example .env
```

首次创建 MySQL 数据卷之前，应修改 `.env` 中的 `MYSQL_PASSWORD` 和
`MYSQL_ROOT_PASSWORD`。MySQL 只在空数据卷第一次初始化时读取这些账号和密码；
以后修改 `.env` 不会自动修改已有数据库中的密码。

2. 创建 Python 3.11 虚拟环境并安装依赖：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e . -r requirements-dev.txt
```

3. 启动 MySQL、Milvus、etcd、MinIO 和 Attu，并检查状态：

```powershell
docker compose up -d
docker compose ps
```

等待 `mysql`、`etcd` 和 `standalone` 显示为 `healthy` 后再启动应用。

4. 启动 FastAPI：

```powershell
.\.venv\Scripts\python.exe -B main.py
```

### 日常启动

已有 `.env`、`.venv` 和 Docker 数据时，不要重新复制 `.env.example`，直接执行：

```powershell
docker compose up -d
.\.venv\Scripts\python.exe -B main.py
```

### 下载副本与 Docker 重名冲突

当前 `docker-compose.yml` 使用固定容器名 `personalive-mysql`、`personalive-etcd`、
`personalive-minio`、`personalive-milvus` 和 `personalive-attu`，MySQL 还使用固定的
全局数据卷 `personalive_mysql_data`。因此，在同一台电脑上运行另一个下载副本时，
它可能连接到原项目已经创建的 MySQL，而不是使用新副本 `.env` 中的账号重新初始化。

如果启动时报以下错误：

```text
pymysql.err.OperationalError: (1045, "Access denied for user ...")
```

说明应用已经连接到 MySQL，但 `.env` 中的账号或密码与该数据卷首次初始化时保存的
凭据不一致。这不是 Python 依赖缺失。启动时出现的 protobuf 版本信息只是警告，
也不是该错误的原因。

处理方式一，复用原项目的 Docker 基础设施：

1. 将原项目 `.env` 中的 `MYSQL_HOST`、`MYSQL_PORT`、`MYSQL_DATABASE`、
   `MYSQL_USER` 和 `MYSQL_PASSWORD` 填入下载副本的 `.env`。
2. 不要尝试用下载副本重新初始化同名 MySQL 数据卷。
3. 如果原 FastAPI 仍占用 `8001`，停止原进程，或把下载副本的 `APP_PORT` 改为
   `8002` 等未占用端口。

处理方式二，让下载副本拥有独立的 Docker 数据：

1. 在下载副本的 `docker-compose.yml` 中为五个 `container_name` 添加唯一前缀。
2. 将 MySQL 卷名 `personalive_mysql_data` 改为新的唯一名称。
3. 修改宿主机端口，避免与原项目冲突，例如：
   - MySQL：`23306:3306`
   - Milvus：`29530:19530`
   - Milvus 健康端口：`29091:9091`
   - Attu：`28082:3000`
4. 同步修改下载副本 `.env`：

```env
MYSQL_PORT=23306
MILVUS_URI=http://127.0.0.1:29530
APP_PORT=8002
```

完成隔离后再执行 `docker compose up -d`。不要为了修复账号错误直接删除
`personalive_mysql_data`；删除该卷会永久丢失已有角色、对话和任务数据。

LLM、Embedding 与联网搜索配置在应用的“设置”页填写。Embedding 输出维度必须与
当前 Milvus collection 一致；更换维度时应使用新的 `COLLECTION_NAME`。

API 文档：<http://127.0.0.1:8001/docs>

Web 工作台：<http://127.0.0.1:8001/static/index.html>

工作台提供角色切换、资料 Markdown 预览与确认入库、RAG 问答、引用和 trace 展示。

组件状态：<http://127.0.0.1:8001/api/status>

Attu：<http://127.0.0.1:18082>

## 主要接口

- `POST /api/personas`：创建角色及其独立知识空间。
- `GET /api/personas`：列出本地角色。
- `POST /api/knowledge-spaces/{space_id}/documents/upload`：批量上传并生成 Markdown 预览。
- `POST /api/documents/{job_id}/confirm`：确认并异步入库。
- `POST /api/documents/{job_id}/retry-index`：重试失败的入库任务。
- `GET /api/documents/{job_id}`：查询任务状态。
- `POST /api/personas/{persona_id}/rag/query`：执行角色隔离的完整 RAG 查询。
- `POST /api/personas/{persona_id}/agent/query`：通过人设主 Agent 执行对话与 Worker 委派。
- `POST /api/personas/{persona_id}/agent/resume`：确认或拒绝已暂停的管理操作。
- `WS /ws/personas/{persona_id}/conversations/{conversation_id}`：建立带轮次 ID、确认事件和停止旧轮次输出能力的实时对话。

WebSocket 与 REST 共用同一个 LangGraph 会话和服务端角色作用域。当前实时通道输出
结构化文字事件；ASR、TTS 和 Live2D 将在后续阶段接入，不属于当前协议基础。

请求不能提交 `workspace_id` 或 `knowledge_space_id`。服务端始终从路径中的角色解析
作用域，Milvus 写入、删除和查询都携带工作空间与知识空间过滤条件。

## RAG 模式

完整模式：

```env
RAG_PIPELINE=default
MAX_REWRITE_COUNT=2
MAX_GENERATION_RETRY=2
DEFAULT_CONFIDENCE_THRESHOLD=0.75
```

简单模式：

```env
RAG_PIPELINE=simple
```

联网搜索、LLM 与 Embedding 配置均在前端“设置”页完成，保存到
`data/local_settings.json`，不再从 `.env` 读取 Provider Key 或模型参数。
联网搜索支持关闭、Tavily、博查，以及博查/Bing 兼容的自定义接口。

## 验证

- 博查 API 实测返回 HTTP 200 和 1 条结果，表明测试时使用的接口与 Key 可用；
  Key 不保存到仓库。
- 此前的 Web Search、设置页与配置专项验证共 13 项通过。

## 进程管理

查看当前监听 PID：

```powershell
Get-NetTCPConnection -LocalPort 8001 -State Listen |
    Select-Object LocalAddress, LocalPort, OwningProcess
```

停止 FastAPI：

```powershell
$conn = Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue
if ($conn) {
    Stop-Process -Id $conn.OwningProcess
    Write-Host "Stopped process PID:" $conn.OwningProcess
} else {
    Write-Host "No service is listening on port 8001"
}
```

停止 Docker 基础设施并保留数据：

```powershell
docker compose down
```

## 后续计划

1. 资料批量导入、直接文本和图片输入、人物候选识别、领域专家回退与角色版本确认。
2. faster-whisper、GPT-SoVITS/在线 TTS、数字人事件、OBS 与直播平台适配器。
