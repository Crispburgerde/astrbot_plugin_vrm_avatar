# astrbot_plugin_vrm_avatar

为 AstrBot 提供 VRM 虚拟形象能力的插件。通过 WebSocket 驱动前端 VRM 模型，实现情绪表演、语音合成与口型同步的实时演出。

> **注意**：本插件仅提供后端能力，需要配合前端应用 [astrbot_vrm_avatar_client](https://github.com/Crispburgerde/astrbot_vrm_avatar_client) 一起使用，由前端负责 VRM 模型的渲染与表演呈现。

## 功能特性

- **VRM 模型加载**：上传 VRM 模型文件，客户端连接后自动推送并加载角色。
- **情绪表演**：LLM 按情绪变化点拆分台词，为每句标注情绪（固定 24 词表，与前端引擎一致）及其强度（0~1），驱动前端表情演出。
- **VRMA 动画**：上传 VRMA 动画文件。当前表演流程不再直接触发肢体动作；`layer=idle` 的动画作为待机姿态变体，由 LLM 通过 `set_idle` 工具（function calling）在情绪或状态持久变化时切换，不占用台词流。动画配置保留，以备后续使用。
- **背景图片**：自定义虚拟形象展示背景。
- **TTS 语音合成**：对接 AstrBot 已配置的 TTS 提供商（推荐 Fish Audio，支持在文本前附加 `[emotion]` 情绪标签），为每段台词生成语音；音频以 base64 内嵌在表演消息中推送，前端基于 Web Audio API 实现口型同步。
- **LLM 驱动表演**：主 LLM 自然生成回复，不注入任何格式控制提示词；回复生成后由插件配置的小模型（表演提取模型，输入中附带本轮用户消息以理解对话场景）将回复按情绪变化点拆分为台词，并为每句标注情绪与强度，实现「原始回复」与「格式控制」的完全解耦。

## 工作原理

```
用户消息 → AstrBot → 主 LLM（自然生成原始回复，不注入格式约束）
                        ↓
              原始回复文本
                        ↓
      表演提取模型（插件配置的小模型，按情绪变化点拆句 + 标注情绪/强度）
                        ↓
              JSON 数组输出（[{text, emotion, intensity}, ...]）
                        ↓
              解析为表演序列（情绪校验至 24 词表、强度 clamp 到 [0,1]）→ 逐段生成 TTS 音频（文本前附加 [emotion] 标签）→ 构建 performance 消息（含内嵌音频）
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
| `on_decorating_result` | 调用表演提取模型将原始回复按情绪变化点拆句并标注情绪与强度（聊天平台仍显示原始回复），逐段生成 TTS 音频并通过 WebSocket 推送表演数据；提取失败时回退为整段回复作为单个 neutral 表演 |
| 客户端连接回调 | 自动向新连接的客户端推送 VRM 模型、背景图与动画列表（以 URL 引用，见下节） |

## 大文件分发（URL + HTTP）

VRM 模型、背景图与 VRMA 动画不再以 base64 内嵌在 WebSocket JSON 消息中推送（大型模型如 150MB 的 MANUKA 会阻塞服务端事件循环与前端主线程）。改为：

- 插件在可配置的 `http_port` 端口（默认 8766）启动一个轻量 HTTP 文件服务器，`GET /files/<插件数据目录相对路径>` 返回文件本体，带 `Access-Control-Allow-Origin: *`（跨源可访问）、`Cache-Control: no-cache` 与 `ETag`（支持 304 协商缓存，页面刷新不重复下载）。
- `update_character` / `update_background` / `update_animations` 消息中的 `FilePayload` 改为携带 `url` 字段（如 `/files/files/vrm_file/MANUKA.vrm`），不再携带 `data`。TTS 音频较小，仍以 `data`（base64）内嵌在 `performance` 消息中。

**前端对接约定**：用 WebSocket 连接地址中的主机名 + `http_port` 端口拼出 HTTP 基址（如 `ws://192.168.1.5:8765`、`http_port=8766` → `http://192.168.1.5:8766`），`fetch(base + url)` 取 `arrayBuffer()` 后交给 VRM 加载器。注意前端页面不能通过 HTTPS 打开，否则浏览器会因混合内容拦截对 `http://` 资源的请求（`ws://` 同理）；若需要 HTTPS，请通过反向代理统一入口（见下节）。

### 通过 Nginx 反向代理提供 HTTPS

若前端页面部署在 HTTPS 下（如公网域名 + 证书），可将 WebSocket 与文件服务统一反代到同一域名，插件无需任何改动：

```nginx
server {
    listen 443 ssl;
    server_name avatar.example.com;
    # ssl_certificate / ssl_certificate_key ...

    location /ws {
        proxy_pass http://127.0.0.1:8765;          # ws_port
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 3600s;
    }

    location /files/ {
        proxy_pass http://127.0.0.1:8766;          # http_port
    }
}
```

前端此时连接 `wss://avatar.example.com/ws`，文件基址为 `https://avatar.example.com`（URL 路径保持不变）。

## 表演数据格式

提取模型输出（也是推送给前端的 `performance` 消息中每个元素的字段，`audio` 由插件填充）：

```json
[
  {
    "text": "哼，我才不在意呢",
    "emotion": "frustrated",
    "intensity": 0.8,
    "audio": { "filename": "xxx.wav", "data": "<base64>" }
  },
  {
    "text": "……好吧，其实有一点难过",
    "emotion": "sad",
    "intensity": 0.6
  }
]
```

- 拆分单位是**情绪/表现变化点**，同一情绪的连续台词并入同一个对象。
- `emotion` 必须在固定 24 词表内（与前端引擎一致，不可增删）：`neutral`、`happy`、`excited`、`amused`、`proud`、`confident`、`calm`、`relieved`、`affectionate`、`shy`、`embarrassed`、`curious`、`concerned`、`confused`、`tired`、`sad`、`lonely`、`disappointed`、`anxiety`、`fear`、`anger`、`frustrated`、`disgust`、`surprised`；未知值由插件降级为 `neutral`。
- `intensity` 为 0~1 的情绪强度；缺失或越界由插件 clamp 到 `[0,1]`，缺失取 `0.85`。
- `audio` 为该句台词的 TTS 音频（base64 内嵌）；合成时会在文本前附加 `[emotion]` 情绪标签传给 TTS 引擎（Fish Audio 支持）。

## 配置说明

在 AstrBot 管理面板的插件配置页中进行设置：

| 配置项 | 类型 | 说明 |
|--------|------|------|
| `ws_host` | 字符串 | WebSocket 与文件服务监听地址（`0.0.0.0` 所有网卡 / `127.0.0.1` 仅本机） |
| `ws_port` | 整数 | WebSocket 端口（默认 8765） |
| `http_port` | 整数 | 文件服务端口（默认 8766；修改 `ws_port` 时请一并调整） |
| `director_provider_id` | 字符串 | 导演模型提供商 ID（建议选用轻量小模型），在主 LLM 生成原始回复后在原文中内联插入 `[情绪]` 标记（拆分由解析器按标记边界完成，强度为固定默认值）；留空则回退为整段回复作为单个 neutral 表演 |
| `tts_provider_id` | 字符串 | 用于语音合成的 TTS 提供商 ID，需提前在 AstrBot 中配置对应的 TTS Provider（推荐 Fish Audio，可识别文本前的 `[emotion]` 情绪标签） |
| `vrm_file` | 文件 | VRM 模型文件（`.vrm`），客户端连接后自动推送 |
| `background_file` | 文件 | 背景图片文件（`.png` / `.jpg` / `.jpeg` / `.webp`） |
| `vrma_animations` | 模板列表 | VRMA 动画配置，每项包含动画名与 VRMA 文件。`layer=idle` 中命名为 `idle` 的动画作为前端默认待机动作，其余 idle 动画可通过 `set_idle` 工具切换 |

## 依赖

- 无第三方依赖。

## 相关链接

- [AstrBot](https://github.com/AstrBotDevs/AstrBot)
- [AstrBot 插件开发文档](https://docs.astrbot.app/dev/star/plugin-new.html)
- [astrbot_vrm_avatar_client](https://github.com/Crispburgerde/astrbot_vrm_avatar_client)：配套的前端应用，负责 VRM 模型渲染与表演呈现
