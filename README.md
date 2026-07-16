# astrbot_plugin_vrm_avatar

为 AstrBot 提供 VRM 虚拟形象能力的插件。通过 WebSocket 驱动前端 VRM 模型，实现表情切换、肢体动作、语音合成与口型同步的实时表演。

> **注意**：本插件仅提供后端能力，需要配合前端应用 [astrbot_vrm_avatar_client](https://github.com/Crispburgerde/astrbot_vrm_avatar_client) 一起使用，由前端负责 VRM 模型的渲染与表演呈现。

## 功能特性

- **VRM 模型加载**：上传 VRM 模型文件，客户端连接后自动推送并加载角色。
- **表情系统**：自定义可用表情列表，LLM 将根据对话语义自动选择合适的表情。
- **VRMA 动画**：上传 VRMA 动画文件，支持循环播放与待机（`idle`）动作，LLM 可按剧情需要触发对应肢体动作。
- **背景图片**：自定义虚拟形象展示背景。
- **TTS 语音合成**：对接 AstrBot 已配置的 TTS 提供商，为每段对话生成语音，前端基于 Web Audio API 实现口型同步。
- **LLM 驱动表演**：在 LLM 请求阶段注入 System Prompt，引导 LLM 以 JSON 分段格式输出对话、表情与动作，实现自然的多段表演。

## 工作原理

```
用户消息 → AstrBot → LLM（注入表演格式 Prompt）
                        ↓
                   JSON 分段输出（dialogue / expression / action）
                        ↓
              解析分段 → 逐段生成 TTS 音频 → 构建 performance 消息
                        ↓
                 WebSocket 推送至 VRM 前端客户端
                        ↓
          前端播放动画 + 切换表情 + 播放语音（口型同步）
```

插件在 AstrBot 生命周期中注册了以下钩子：

| 钩子 | 说明 |
|------|------|
| `initialize` | 启动 WebSocket 服务器 |
| `on_llm_request` | 向 System Prompt 注入表演格式要求，告知 LLM 可用的表情与动作 |
| `on_decorating_result` | 解析 LLM 输出的 JSON 分段，生成 TTS 音频，通过 WebSocket 推送表演数据 |
| 客户端连接回调 | 自动向新连接的客户端推送 VRM 模型、背景图与动画列表 |

## 配置说明

在 AstrBot 管理面板的插件配置页中进行设置：

| 配置项 | 类型 | 说明 |
|--------|------|------|
| `expressions` | 列表 | 可用表情名称，LLM 将从中选择。默认：`neutral`、`happy`、`sad`、`angry`、`surprised` |
| `tts_provider_id` | 字符串 | 用于语音合成的 TTS 提供商 ID，需提前在 AstrBot 中配置对应的 TTS Provider |
| `vrm_file` | 文件 | VRM 模型文件（`.vrm`），客户端连接后自动推送 |
| `background_file` | 文件 | 背景图片文件（`.png` / `.jpg` / `.jpeg` / `.webp`） |
| `vrma_animations` | 模板列表 | VRMA 动画配置，每项包含动画名、VRMA 文件和是否循环播放。命名为 `idle` 的动画将作为待机动作 |

## 依赖

- [json-repair](https://pypi.org/project/json-repair/) >= 0.30.0

## 相关链接

- [AstrBot](https://github.com/AstrBotDevs/AstrBot)
- [AstrBot 插件开发文档](https://docs.astrbot.app/dev/star/plugin-new.html)
- [astrbot_vrm_avatar_client](https://github.com/Crispburgerde/astrbot_vrm_avatar_client)：配套的前端应用，负责 VRM 模型渲染与表演呈现
