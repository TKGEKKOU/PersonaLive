# Product

<!-- impeccable:product-schema 1 -->

## Platform

web（本地桌面应用：FastAPI 服务 + pywebview/WebView2 外壳，`http://127.0.0.1:17000`）

## Users

本地单人用户：搭建并长期使用本地 AI 角色对话，需要快速把自备视频/音频素材变成可绑定角色的合成音色。

## Product Purpose

YUMENO 是本地优先的角色对话应用。声音工坊把"素材 → 参考音色"的全流程可视化：从视频/音频提取人声、切片、生成参考音色、命名保存，并供角色编辑页绑定。

## Positioning

全程本地推理（人声分离、TTS、音色相似度均不依赖云端），素材到音色的流程像一张可随时插入入口的有向图，而非隐藏的黑盒向导。

## Operating Context

Windows 桌面；流程包含大文件上传（视频 ≤400MB、音频 ≤200MB）、长耗时后台任务（提取/分离/切片，轮询进度）、可中断可恢复的草稿会话；成品音色在角色编辑页绑定。

## Capabilities and Constraints

- 声音工坊主链：视频 → 提取/转换 → 人声分离 → 切片 → 选段 → 参考音色 → 试听/命名保存。
- 中途插入（用户确认的语义）：任意节点可直接投放"已处理到这一步"的文件——视频节点传视频、音频节点传音频、片段节点传干净片段、参考节点传参考音频，均跳过前置步骤。
- 音色库：试听、删除、时长/片段数元信息；角色编辑页从音色库选择绑定。
- TTS 引擎：Lunar（本地 Vulkan/CPU）；相似度自评走 qwen3tts.dll。
- 约束：依赖本地 ffmpeg；草稿存 `data/voice_studio/sessions`；音色存 `data/tts/voices`。

## Brand Commitments

- 产品名 YUMENO。
- 用户指令（2026-08-06）：整体视觉模仿《明日方舟》官网风格——浅色纸面底、黑墨线条、红色点缀、直角硬边、技术标注感（推断：以官网浅色版为主，深色段用于强调）。

## Evidence on Hand

- `data/tts/voices` 已有 3 个保存音色；`audios/lunar-template.wav` 为默认音色。
- 声音工坊后端 API：`/api/voice-studio/*`（会话、上传、分离、片段、参考、试听、保存、音色库）。

## Product Principles

1. 本地优先：推理与数据都在本机。
2. 全流程可见：任何时候都知道当前在哪一步、下一步能做什么。
3. 中途可插入：每个节点都接受"已处理到这一步"的文件。
4. 破坏性动作必须确认：重置草稿、删除片段、删除音色。

## Accessibility & Inclusion

桌面端可键盘操作（节点 tabindex + Enter/Space）；主题对比度由全局令牌统一控制（推断，未做专项验证）。
