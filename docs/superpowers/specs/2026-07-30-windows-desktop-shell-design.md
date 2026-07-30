# Windows 桌面壳与 ASR 整合设计

## 目标

使用 PyWebView 将现有 FastAPI 和 Web 前端包装成单入口 Windows 应用。保留 Docker、MySQL、Milvus、etcd 和 MinIO；用户不再手动启动 FastAPI、浏览器或 ASR Worker。

## 启动流程

1. 启动器检查 Docker CLI 与 Docker Desktop 状态。
2. Docker 未运行时启动 Docker Desktop，并等待 Engine 就绪。
3. 执行 `docker compose up -d`，等待基础服务健康。
4. 在进程内启动 FastAPI，并等待 `/api/health`。
5. 打开 PyWebView 窗口加载 `http://127.0.0.1:8001/static/index.html`。
6. ASR Worker 在首次转写时按需启动；关闭窗口时停止 FastAPI 和由本应用创建的 Worker。

## ASR 目录

发行版优先使用应用目录内的资源：

- `runtime/asr/python.exe`
- `runtime/ffmpeg/ffmpeg.exe`
- `models/Qwen3-ASR-0.6B/`

源码仓库只保存 `voice/asr` 代码、依赖清单和资源清单。上述运行环境、FFmpeg 与模型目录保持 Git 忽略。开发环境继续兼容 `D:\Qwen3_ASR`，但它不是发行版依赖。

## 桌面模块

- `desktop/launcher.py`：应用生命周期与 PyWebView 窗口。
- `desktop/docker_manager.py`：Docker 检查、启动和 Compose 编排。
- `desktop/server_manager.py`：FastAPI 线程和健康等待。
- `desktop_main.py`：PyInstaller 入口。
- `requirements-desktop.txt`：PyWebView 与打包依赖。

## 错误处理

Docker 缺失、Docker Engine 启动超时、Compose 失败、FastAPI 启动失败均在原生错误窗口中显示，不打开空白主窗口。应用关闭默认不停止 Docker 容器，以提高下次启动速度。

## 验证

- 单元测试覆盖 Docker 命令、服务生命周期与 ASR 相对目录优先级。
- FastAPI 与 ASR 现有测试继续通过。
- Windows 手工验证 PyWebView 页面、麦克风权限、WebM 转写和窗口关闭清理。
