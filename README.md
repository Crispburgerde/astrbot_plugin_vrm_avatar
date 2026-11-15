# astrbot_plugin_vrm_avatar

为 AstrBot 提供 VRM 虚拟形象能力的插件。通过 WebSocket 驱动前端 VRM 模型，实现情绪表演、语音合成与口型同步的实时演出。

> **注意**：本插件负责后端能力，客户端 [astrbot_vrm_avatar_client](https://github.com/Crispburgerde/astrbot_vrm_avatar_client) 负责 VRM 模型的渲染与表演呈现。客户端支持两种使用方式：
>
> 1. **内嵌预览**：直接使用嵌入在插件管理页面中的客户端进行预览，无需额外部署；但此方式下会失去浏览器持久化保存前端配置信息的功能。
> 2. **Nginx 独立部署**：将客户端作为静态站点用 Nginx 托管，功能完整，支持持久化保存前端配置。

## 功能特性

- **VRM 模型加载**：上传 VRM 模型文件，客户端连接后自动推送并加载角色。
- **情绪表演**：导演模型（插件配置的小模型）在原始回复的情绪变化点内联插入情绪标签（固定 24 词表，与前端引擎一致），解析器按标签边界拆分台词并解析情绪与强度，驱动前端表情演出。
- **VRMA 动画**：上传 VRMA 动画文件。当前表演流程不再直接触发肢体动作；`layer=idle` 的动画作为待机姿态变体，由 LLM 通过 `set_idle` 工具切换，不占用台词流。动画配置保留，以备后续使用。
- **背景图片**：自定义虚拟形象展示背景。
- **TTS 语音合成**：对接 AstrBot 已配置的 TTS 提供商（推荐 Fish Audio，支持行内 `[emotion]` 情绪标签），将整段回复合成单条语音；音频以 base64 内嵌在表演消息中推送，前端基于 wlipsync 实现口型同步。
- **LLM 驱动表演**：主 LLM 自然生成回复，不注入任何格式控制提示词；回复生成后由插件配置的小模型（导演模型，输入中附带本轮用户消息以理解对话场景）在原文中内联插入 `[情绪]` 标签，拆分与情绪解析由解析器按标签边界完成（强度为固定值 1），实现「原始回复」与「格式控制」的完全解耦。

## 工作原理

```
用户消息 → AstrBot → 主 LLM（自然生成原始回复，不注入格式约束）
                        ↓
              原始回复文本
                        ↓
      导演模型（插件配置的小模型，在原文中内联插入 [情绪] 标签）
                        ↓
              解析器按标签边界拆分 → 表演序列（[{text, emotion, intensity, start, end}, ...]，
              情绪校验至 24 词表，强度现阶段为固定值 1）→ 整段回复（含行内 [emotion] 标签）合成单条 TTS 音频 → 对齐填充各分段时间窗口 → 构建 performance 消息（音频内嵌）
                        ↓
                 WebSocket 推送至 VRM 前端客户端
                        ↓
          前端播放语音（口型同步）+ 表演对应情绪表情
```

插件在 AstrBot 生命周期中注册了以下钩子：

| 钩子 | 说明 |
|------|------|
| `initialize` | 启动 WebSocket 服务器（`ws_port`）与 HTTP 文件服务器（`http_port`，路径前缀 `/files/`） |
| `on_llm_request` | 仅当配置了待机动画时，向 System Prompt 注入 `set_idle` 工具使用说明 |
| `on_decorating_result` | 调用导演模型在原始回复中内联插入情绪标签，再由解析器按标签边界拆分台词并解析情绪与强度（聊天平台仍显示原始回复），逐段生成 TTS 音频并通过 WebSocket 推送表演数据；导演模型未配置或调用失败时回退为整段回复作为单个 neutral 表演。本轮 `set_idle` 工具缓冲的待机姿态切换也在此处随表演数据一并发送，使姿态变化与台词同步落地 |

此外，插件在 WebSocket 客户端连接回调中，自动向新连接的客户端推送 VRM 模型、背景图与动画列表（以 URL 引用，见下节）。

## 大文件分发（URL + HTTP）

VRM 模型、背景图与 VRMA 动画等大文件通过 HTTP 分发，WebSocket 消息中仅携带文件 URL（避免大文件阻塞服务端事件循环与前端主线程）：

- 插件在可配置的 `http_port` 端口（默认 8766）启动一个轻量 HTTP 文件服务器，`GET /files/<插件数据目录相对路径>` 返回文件本体，带 `Access-Control-Allow-Origin: *`（跨源可访问）、`Cache-Control: no-cache` 与 `ETag`（支持 304 协商缓存，页面刷新不会重复下载）。
- `update_character` / `update_background` / `update_animations` 消息中的 `FilePayload` 仅携带 `url` 字段（如 `/files/files/vrm_file/MANUKA.vrm`），不包含文件数据。TTS 音频体积较小，以 `data`（base64）内嵌在 `performance` 消息中。

**前端对接约定**：用 WebSocket 连接地址中的主机名 + `http_port` 端口拼出 HTTP 基址（如 `ws://192.168.1.5:8765`、`http_port=8766` → `http://192.168.1.5:8766`），`fetch(base + url)` 后加载。注意：前端页面若通过 HTTPS 打开，其向 `http://` / `ws://` 发出的请求会被浏览器混合内容策略（Mixed Content）直接拦截，导致模型加载与 WebSocket 连接全部失败（`http://localhost` 除外）；若需要 HTTPS，请通过反向代理把 `https://` / `wss://` 统一代理到后端的 `http://` / `ws://`。

## 表演数据格式

推送给前端的 `performance` 消息：`performances` 为解析器输出的表演序列，`audio` 为整段回复共用的单条 TTS 音频（由插件填充，与列表同级而非逐段携带）：

```json
{
  "type": "performance",
  "performances": [
    {
      "text": "哼，我才不在意呢",
      "emotion": "frustrated",
      "intensity": 1,
      "start": 0.0,
      "end": 2.4
    },
    {
      "text": "……好吧，其实有一点难过",
      "emotion": "sad",
      "intensity": 1,
      "start": 2.4,
      "end": 5.1
    }
  ],
  "audio": { "filename": "xxx.wav", "data": "<base64>" }
}
```

- 拆分以导演模型插入的情绪标签为边界，同一情绪的连续台词并入同一个对象。
- `emotion` 必须在固定 24 词表内（与前端引擎一致，不可增删）：`neutral`（平静）、`happy`（开心）、`excited`（兴奋）、`amused`（愉悦）、`proud`（自豪）、`confident`（自信）、`calm`（冷静）、`relieved`（安心）、`affectionate`（温柔）、`shy`（害羞）、`embarrassed`（尴尬）、`curious`（好奇）、`concerned`（担心）、`confused`（困惑）、`tired`（疲惫）、`sad`（难过）、`lonely`（孤独）、`disappointed`（失望）、`anxiety`（焦虑）、`fear`（恐惧）、`anger`（愤怒）、`frustrated`（恼火）、`disgust`（厌恶）、`surprised`（惊讶）；未知值由插件降级为 `neutral`。
- `intensity` 为 0~1 的情绪强度；现阶段为固定值 1.
- `start` / `end` 为该分段在共用音频内的时间窗口（秒），由强制对齐结果填充；对齐失败时按音频时长比例估算，无音频时为 `null`。
- `audio` 为整段回复共用的 TTS 音频（base64 内嵌，无可用 TTS 时为 `null`）；合成时将各分段文本连同行内 `[emotion]` 标签拼为一段文本传给 TTS 引擎（Fish Audio 支持），前端播放一次音频并在各分段时间窗口的边界切换情绪。

## 配置说明

在 AstrBot 管理面板的插件配置页中进行设置：

| 配置项 | 类型 | 说明 |
|--------|------|------|
| `ws_host` | 字符串 | WebSocket 与文件服务监听地址（`0.0.0.0` 所有网卡 / `127.0.0.1` 仅本机） |
| `ws_port` | 整数 | WebSocket 端口（默认 8765） |
| `http_port` | 整数 | 文件服务端口（默认 8766；修改 `ws_port` 时请一并调整） |
| `director_provider_id` | 字符串 | 导演模型提供商 ID（建议选用轻量小模型），在主 LLM 生成原始回复后在原文中内联插入 `[情绪]` 标记（拆分由解析器按标记边界完成，强度为固定值 1）；留空则回退为整段回复作为单个 neutral 表演 |
| `tts_provider_id` | 字符串 | 用于语音合成的 TTS 提供商 ID，需提前在 AstrBot 中配置对应的 TTS Provider（推荐 Fish Audio，可识别文本前的 `[emotion]` 情绪标签） |
| `forced_alignment` | 对象 | 强制对齐（FA）服务配置：将整段 TTS 音频与台词文本对齐，得到每个分段的时间窗口，供前端在完整音频内按分段切换情绪。目前仅支持 [audio.cpp](https://github.com/0xShug0/audio.cpp) 项目的对齐接口；关闭开关或服务不可用时降级为按字数估计 |
| `forced_alignment.enabled` | 布尔 | 启用强制对齐（默认关闭）。关闭时不请求对齐服务，直接按字数估计各分段的时间窗口 |
| `forced_alignment.api_url` | 字符串 | 对齐服务接口的完整 URL（含路径），如 `http://127.0.0.1:8080/v1/audio/alignments` |
| `forced_alignment.model` | 字符串 | 请求中的 `model` 表单字段，需与服务端已加载的对齐模型名一致 |
| `forced_alignment.language` | 字符串 | 对齐语言，可选 `zh`/`yue`/`en`/`de`/`es`/`fr`/`it`/`pt`/`ru`/`ko`/`ja`，需与 TTS 实际播报语言一致 |
| `vrm_file` | 文件 | VRM 模型文件（`.vrm`），客户端连接后自动推送 |
| `background_file` | 文件 | 背景图片文件（`.png` / `.jpg` / `.jpeg` / `.webp`） |
| `vrma_animations` | 模板列表 | VRMA 动画配置，每项包含动画名与 VRMA 文件。第一个 `layer=idle` 的动画作为前端默认待机动作（若未配置 idle 层动画，则使用名为 `idle` 的动画），其余 idle 动画可通过 `set_idle` 工具切换 |

## 依赖

- 无第三方依赖。

## 相关链接

- [AstrBot](https://github.com/AstrBotDevs/AstrBot)
- [AstrBot 插件开发文档](https://docs.astrbot.app/dev/star/plugin-new.html)
- [astrbot_vrm_avatar_client](https://github.com/Crispburgerde/astrbot_vrm_avatar_client)：配套的前端应用，负责 VRM 模型渲染与表演呈现
