# 聊天页侧栏与输入区向 ChatGPT 风格收敛计划

## 1. 这个需求是否值得做？可能的雷点

**值得做**。当前网页端的主要问题不是“少一个按钮”，而是聊天页存在多处无效或噪声交互：
- 侧栏同时放了“新会话 / 刷新列表 / 工作目录切换”，主次关系混乱。
- 会话切换入口已经是“点击列表项”，但 UI 上又额外保留“切换”语义和刷新动作，和 ChatGPT 的习惯模型冲突。
- 顶部状态条、底部 mock 控件、过多说明文案会分散用户注意力，让核心路径“选会话 -> 聊天”不够直接。

**主要雷点**
- **列表拉取时机变化**：去掉“刷新列表”后，前端不能再依赖用户手动刷新；必须把“首屏加载”和“首条消息持久化后补入侧栏”这两条路径处理好。
- **新会话的 active 态**：新建对话时，还没有 `conversationId`；如果 UI 想像 ChatGPT 一样立即切到“新对话”状态，需要在前端明确空态高亮规则，否则侧栏会继续高亮旧会话。
- **移动端侧栏体验**：ChatGPT 式侧栏在桌面和移动端的行为不同；如果只改桌面视觉，不处理移动端抽屉开关，体验会割裂。
- **不要误做成像素级复刻**：这次应模仿信息架构和交互心智，不是照搬所有视觉细节；否则会在无关样式上浪费时间。

## 2. 推荐方案

### 2.1 范围与假设

**本次范围**
- 保留左侧会话列表 + 右侧聊天主区的大框架。
- 侧栏只保留“新建对话”和“会话列表”两个核心入口。
- 去掉“刷新列表”按钮。
- 去掉未接入的“工作目录 / 切换”区块。
- 头部收敛为轻量栏，只保留移动端打开侧栏入口与必要状态信息。
- 输入区收敛为更接近 ChatGPT 的单输入容器，移除明显的 mock/调试感控件。

**明确假设**
- 会话列表仍然只在页面初始加载时主动请求 `GET /conversations`。
- 新会话首条消息持久化后，前端通过现有 `conversation.persisted` 事件把该会话插入或更新到侧栏，无需重新全量刷新列表。
- 不追求 ChatGPT 完整视觉复刻；目标是达到“布局与交互习惯明显接近”的程度。

### 2.2 交互重组

```text
页面加载
  |
  +-- connect /ws                -> 建立当前会话连接
  |
  +-- fetch /conversations       -> 初始化侧栏会话列表
  |
  v
用户点击侧栏某个会话
  |
  +-- GET /conversations/{id}    -> 拉历史
  +-- loadConversation()         -> 一次性投影到 items[]
  +-- reconnect /ws?id=...       -> 后续消息接到该会话
  |
  v
用户点击“新建对话”
  |
  +-- resetChatStore()
  +-- reconnect /ws              -> 不带 conversationId
  +-- active 会话高亮清空
  |
  v
首条消息持久化
  |
  +-- conversation.persisted
  +-- 把新会话写入侧栏并设为 active
```

### 2.3 前端改动点

1. `frontend/src/App.tsx`
- 把“会话列表加载”从“首屏 + activeConversationId 变化后反复刷新”改成“仅首屏加载一次”。
- 删除 `isRefreshingSessions` 及 `refreshSessionList()` 暴露给侧栏按钮的那部分职责；保留一个仅供初始化调用的列表加载函数。
- 新建对话时不再触发列表全量刷新，只做：
  - 清空 store
  - 断开并重连默认 WebSocket
  - 清空当前 active 高亮
- 收到 `conversation.persisted` 后，应能把新会话补到本地 `sessionEntries`，避免用户刷新页面前看不到刚创建的会话。
- 头部改成更弱化的存在：移动端只保留侧栏开关，状态信息降为次级文案或更紧凑展示。

2. `frontend/src/features/chat/components/chat-sidebar.tsx`
- 删除 `refreshing`、`onRefreshList` 两个 props。
- 删除“刷新列表”按钮。
- 删除整个“工作目录 / 切换”区块。
- 侧栏结构调整为：
  - 顶部品牌/标题区
  - `新建对话` 主按钮
  - `最近会话` 列表
- 会话项交互改得更像 ChatGPT：
  - 整行点击切换
  - active 项更清晰但不过度突出
  - 文案优先显示 `displayName`
  - 时间信息弱化，避免一条会话卡占两行太多高度；必要时只在 hover/副标题保留

3. `frontend/src/features/chat/components/chat-composer.tsx`
- 去掉 `暂停（mock）` 按钮。
- `清空错误` 不再与发送主路径并列摆放；改成更弱的次级入口，或并入反馈区。
- 输入容器改成单卡片式布局：文本框与发送按钮在同一视觉容器内，减少“表单感”。
- 保留现有 `Enter 发送 / Shift+Enter 换行` 行为。

4. `frontend/src/features/chat/store.ts`
- 确认已有 `conversation.persisted -> activeConversationId` 的逻辑足够支撑新会话高亮。
- 如当前 `reset()` 不会清掉 `activeConversationId`，实现时需要补上；否则点击“新建对话”后侧栏会错误高亮旧会话。

### 2.4 不做的事

- 不改后端 API。
- 不新增“刷新列表”替代入口。
- 不引入复杂筛选、搜索、分组、无限滚动等会话管理能力。
- 不在本次顺手重做整套消息卡视觉体系；先把框架和核心交互校正。

## 3. 测试与验收思路

### 3.1 手工验收

1. 首次打开页面：
- 自动请求一次 `GET /conversations`。
- 侧栏没有“刷新列表”按钮。
- 侧栏没有“工作目录 / 切换”区块。

2. 点击“新建对话”：
- 主区清空到新会话空态。
- 侧栏不再高亮旧会话。
- WebSocket 重新连接到默认新会话。

3. 点击历史会话：
- 不需要额外“切换会话”按钮。
- 历史消息正确加载。
- 侧栏 active 项切换正确。
- 移动端点击后侧栏自动收起。

4. 新会话发送首条消息后：
- 收到 `conversation.persisted` 后，侧栏出现新会话项。
- 新会话自动变为 active。
- 不依赖手动刷新也能看到它。

5. 输入区：
- 没有 `暂停（mock）` 按钮。
- 发送入口明显，错误信息不会抢占主路径。

### 3.2 自动化测试

**组件/状态测试**
- `App` 首次挂载时只触发一次 `fetchConversationList()`。
- `activeConversationId` 变化不会再次触发全量列表请求。
- `conversation.persisted` 事件能把新会话合并进本地 `sessionEntries`。
- “新建对话”后 active 会话状态被清空。

**回归测试**
- 仍然保留“生成中或有 pending 时禁止切换会话”的约束。
- 现有会话切换流程：`fetch detail -> loadConversation -> reconnect ws` 不被破坏。

**可选 e2e**
- 首屏加载后，侧栏只出现“新建对话 + 最近会话”。
- 点击历史会话即可切换，不存在刷新依赖。
- 新建对话发送首条消息后，侧栏自动出现新条目。

## 4. 实施顺序

1. 先收敛 `App.tsx` 的会话列表加载策略与本地侧栏状态维护。
2. 再重写 `ChatSidebar` 的结构，删除多余控件。
3. 然后收敛 `ChatComposer` 与头部，消除 mock/调试感。
4. 最后补测试，重点守住“新会话 active 态”和“列表只首屏加载一次”。

## 5. Conventional Commit Message

`feat(frontend): simplify chat layout to match ChatGPT sidebar flow`
