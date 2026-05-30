Q: backend/src/pkg/handrolled_codex 当前我们这个实现是模仿 hermes agent的，你看一下我们这个实现和他的实现有什么区别？

我对照了两边代码后，结论是：我们这里的 `handrolled_codex` 只“借鉴了 Hermes 的 Codex Responses 直连思路”，但在 **认证/能力范围/事件解析健壮性/多提供商兼容** 上做了大量裁剪，同时也加了一些更贴近 project-x 运行环境的“网络代理排障”逻辑。

下面按模块拆开说差异（都给到起始行，方便你点进去看）。

- **认证与 token 生命周期**
  - project-x：`backend/src/pkg/handrolled_codex/auth.py:1` 只有“读 `~/.project-x/auth.json`，缺了就从 `~/.codex/auth.json` 导入一次”，不做 refresh、不做 expiry 判断（`refresh_token` 只是存着）。这意味着 token 过期后更可能直接 401，需要人工处理。
  - hermes：认证体系在 `hermes_cli/auth.py:1`，是完整的多 provider auth store + 锁 + refresh/renew 逻辑（Codex 只是其中一个 provider），而且会把“运行时凭据解析”当成一等能力来维护。

- **请求/流式实现方式（手写 SSE vs SDK 事件流）**
  - project-x：`backend/src/pkg/handrolled_codex/client.py:1` 用 `httpx.AsyncClient().stream()` 自己解析 `text/event-stream`（`_sse_events` 里专门处理 CRLF/帧分隔），然后在 `stream_assistant_message()` 里按 event.type 手工路由。
  - hermes：`agent/codex_runtime.py:180` 走 OpenAI Python SDK 的 `responses.create(stream=True)`，直接消费“已解析好的事件对象/字典”，核心是 `_consume_codex_event_stream()` 的事件驱动聚合逻辑。

- **对 Codex 后端漂移/异常事件的防御面**(done)
  - 两边共同点：都规避了“终止事件里 response.output 变成 null 导致 SDK 重建失败”的坑，改用 `response.output_item.done` 来组装最终 output（hermes 明确写在 `agent/codex_runtime.py:180` 的长注释里；我们对应逻辑在 `backend/src/pkg/handrolled_codex/client.py:341` 往后）。
  - hermes 更强的点：
    - 处理 `type=error` 事件并抛出结构化异常（`agent/codex_runtime.py:214` 起）。
    - event.type 的匹配更宽松（比如 `"output_text.delta" in event_type`），兼容不同实现的 event type 细微差异（`agent/codex_runtime.py:309` 附近）。
    - 一旦识别到 tool call，会抑制后续 text delta，避免“工具回合夹杂文本”污染对话（`agent/codex_runtime.py:263` 注释 + 代码逻辑）。
  - project-x 的特点/风险：
    - event.type 匹配更“精确字符串”，遇到提供方/版本变体更可能漏事件（例如只认 `response.output_text.delta`、`response.function_call_arguments.delta` 等，见 `backend/src/pkg/handrolled_codex/client.py:300` 附近）。
    - 不显式处理 `type=error` 帧：如果服务端发 error 事件但仍是 200+SSE，我们更可能表现为“解析不到/静默跳过”，最终变成奇怪的空输出或卡住（取决于对端行为）。

- **消息/工具格式转换能力范围**
  - project-x：转换非常“最小子集”
    - 只把字符串 `content` 转成 input/output_text；不支持 chat 格式的多模态 content parts（见 `backend/src/pkg/handrolled_codex/client.py:218` 起的 `_chat_messages_to_responses_input`）。
    - tools 直接用我们内部 `Tool.parameters_json_schema` 填 `parameters`，没有额外的 schema 清洗/strict 字段（`backend/src/pkg/handrolled_codex/client.py:204`）。
  - hermes：把 Responses 适配单独抽成纯函数模块，覆盖更多边界
    - 多模态 content parts（图片/文本）转换（`agent/codex_responses_adapter.py:79`）。
    - 工具 schema 转换会填 `strict: False` 等，并且在其他路径会做 schema sanitizer（xAI 等）（`agent/codex_responses_adapter.py:237` 起，以及你在 `agent/transports/codex.py` 里能看到一堆 provider 分支逻辑）。
    - 加了“工具调用序列泄漏到文本里”的检测正则（`agent/codex_responses_adapter.py:69`），这是 Hermes 长期线上对抗退化输出的经验累积，我们这边没有。

- **tool_call id 的处理策略**
  - project-x：为了避免前端出现重复 tool 卡片，我们在流式阶段刻意“优先用 item_id”，并在最终 output_item 上保持一致（这段写得很明确，`backend/src/pkg/handrolled_codex/client.py:420` 起）。
  - hermes：在 adapter 里把 call_id / response_item_id 的组合、fc_ 规范化等都系统化了（`agent/codex_responses_adapter.py:187` 起），并且强调“确定性 id 影响 prompt cache”（`agent/codex_responses_adapter.py:175`）。

- **运行时/产品形态差异（这点是“模仿不可能 1:1”的根源）**
  - hermes 还有一整条 “codex_app_server subprocess” 运行时路径（`agent/codex_runtime.py:1` 起），本质是把整回合交给 codex 子进程再投影回 Hermes 消息体系；project-x 完全没有这条路径，只实现了直连 `.../responses` 的 HTTP 流式。

- **我们额外做的“工程化排障”**
  - project-x 在 HTTP 代理/trust_env 上做了很多现实环境兜底（IDE 环境变量不展开 `$http_proxy`、禁用 trust_env、失败时打印提示等），这些在 `backend/src/pkg/handrolled_codex/client.py:54` 到重试分支里很集中；Hermes 也有代理相关测试/处理，但不在同一个“手写 SSE client”层次上。

如果你问“哪些差异最可能导致线上/集成问题”，我会优先盯这三类：
- 我们不处理 `type=error` SSE 帧（可能导致难定位的空结果/假成功）。
- event.type 精确匹配导致“后端轻微改名就漏事件”（尤其是 reasoning/tool delta）。
- token 不刷新，跑久了更容易 401（`backend/src/pkg/handrolled_codex/auth.py:77` 只负责导入/读取）。

你希望我把这些差异进一步归因成“哪些是刻意裁剪、哪些是欠账、哪些建议补齐”的改进清单吗？

