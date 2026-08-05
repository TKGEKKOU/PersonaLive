# YUMENO v0.1.1

## 新增

- **内置免 key 联网搜索**：默认启用 free-search（百度引擎，国内开箱即用），支持 B 站视频检索，可自动附带高相关视频链接
- **MCP 客户端接入**：MCP 服务器管理、角色授权、按服务器配置，可扩展任意 MCP 工具
- **技能系统**：内置 web-research 等技能，支持自定义技能包与统一管理
- **一键启动脚本** `scripts/start.ps1`：自动创建虚拟环境、安装依赖、拉起 Docker 与服务
- **本地语音能力**：ASR / TTS / Embedding 可选本地部署
- **离线 RAG 评测**：可答召回率、事实接地率、查询改写率等指标与 AI 分析报告

## 改进

- 本地 SQLite 全面替代 MySQL，无需额外数据库服务
- 退出确认弹窗统一重设计：主界面卡片式三选项（停止 / 保持 / 删除服务），桌面启动页为"中止启动"
- 对话页优化：气泡 hover 操作（时间 / 复制 / 编辑），移除悬浮过程面板
- 桌面端启动流程与图标优化
- 各类页面风格统一（技能与 MCP 管理、设置、评测）

## 运行要求

- Windows 10/11
- [Docker Desktop](https://www.docker.com/products/docker-desktop/)（Milvus 向量库）
- 配置 LLM API Key（OpenAI 兼容接口，支持 DeepSeek / 通义等）

## 快速开始

```powershell
.\scripts\start.ps1
```

首次运行自动准备环境并启动；浏览器访问 `http://127.0.0.1:17000`。
