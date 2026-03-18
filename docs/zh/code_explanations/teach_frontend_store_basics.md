# 前端 Store / TypeScript / WebSocket 入门讲义

这份讲义的目标不是一次讲完所有细节，而是先帮你建立一个稳定的心智模型。后续如果你继续追问，我们就在对应小节补充，而不是在文末不断追加新内容。

## 0. 前置阅读

如果你现在最大的困惑是这些问题：

- 浏览器到底是什么
- 页面为什么会滚动
- DOM 元素到底是什么
- React 代码到底是在控制什么

那建议先读 [浏览器 / DOM / 滚动 入门讲义](teach_browser_basics.md)。

这份文档主要讲的是：

- 前端 store 是什么
- 协议事件怎么投影成界面
- `App.tsx` / `store.ts` / `protocol.ts` 分别承担什么职责

## 1. 先看 App，再回头看 Store

如果你一开始对协议、store、WebSocket 都不熟，从 `frontend/src/App.tsx` 入手是合理的。

因为 `App.tsx` 会直接暴露这个页面真正依赖哪些状态：

- `connectionStatus`
- `errorMessage`
- `sessionId`
- `items`
- `pendingUserMessages`
- `activeAssistantTurnId`

换句话说，`App.tsx` 告诉你“界面到底需要什么”，而 `store.ts` 回答的是“这些状态是怎么被维护出来的”。

## 2. `App.tsx` 里的本地 UI 状态

在 `App.tsx` 里，除了从 store 取出来的全局状态，还有几个只服务于当前组件自己的本地状态：

- `draft`
- `composerError`
- `mobileSidebarOpen`

这些状态没有放进 store，是因为它们不属于“整个聊天会话的数据模型”，而更像是当前页面自己的 UI 细节。

### 2.1 `draft`

它表示输入框里当前正在编辑但还没发送的文本。

这属于典型的组件本地状态，因为它只跟当前输入框有关，不需要共享给别的模块。

### 2.2 `composerError`

它表示输入区局部需要展示的错误。

例如用户点击发送时，如果当前 WebSocket 还没连好，`handleSubmit` 会捕获异常并把错误写到 `composerError`，这样页面就能在输入区附近给出反馈。

### 2.3 `mobileSidebarOpen`

它表示“移动端侧栏现在是否展开”。

这个状态只跟页面布局交互有关，和聊天业务数据没有关系，所以不应该放进聊天 store。

你可以把它理解成：

- `false`：移动端侧栏收起
- `true`：移动端侧栏展开

它在 `App.tsx` 里主要配合这两个地方使用：

1. 点顶部的“面板”按钮时，把它设为 `true`
2. 把它作为 `mobileVisible` 传给 `ChatSidebar`

所以它本质上只是一个“控制移动端抽屉开关”的布尔值。

## 3. `App.tsx` 里 38 到 157 行在做什么

这一段的主题可以先粗暴概括成一句话：

“控制聊天时间线的滚动行为，让界面在合适的时候自动滚到底部，同时避免用户正在往上翻历史消息时被强行拽回底部。”

如果你对“滚动容器”“视口”“`scrollTop` / `scrollHeight`”这些词没有感觉，建议先回头看 [浏览器 / DOM / 滚动 入门讲义](teach_browser_basics.md) 的第 5 到第 12 节。

如果你一上来就逐行看，会很痛苦。更好的方式是先把它拆成几个小目标。

### 3.1 这段代码想解决什么问题

聊天界面通常有两个相反的需求：

1. 新消息来了，最好自动滚到底部，用户才能看到最新内容
2. 但如果用户正在往上看旧消息，就不应该强行把页面拉回底部

所以这段代码的目标不是“永远自动滚动”，而是：

- 用户本来就在底部时，继续跟随输出
- 用户已经离开底部时，不打断他的阅读
- 但要提示“有新内容来了”

### 3.2 这几个 `ref` / `state` 分别是什么

这一段会同时用到 `useRef` 和 `useState`，它们职责不同。

#### 3.2.1 先理解 `ref` 是什么

你可以先把 `ref` 理解成：

“一个可以长期保存东西的小盒子，React 不会因为它变了就自动重新渲染页面。”

它常见有两种用途：

1. 存 DOM 元素
2. 存不需要触发渲染的内部变量

例如：

```ts
const inputRef = useRef<HTMLInputElement>(null)
```

这里的意思不是“现在已经拿到 input 了”，而是：

“我先准备一个盒子，之后 React 把真实 DOM 节点塞进来。”

于是以后你就可以写：

```ts
inputRef.current
```

来拿到那个 DOM 元素。

#### 3.2.2 `scrollRef` 为什么是“指向 DOM 元素”

这一行：

```ts
const scrollRef = useRef<HTMLDivElement>(null)
```

意思是：

- 这是一个 ref
- 它将来会指向一个 `div`
- 初始值先是 `null`

然后在 JSX 里会写成类似这样：

```tsx
<div ref={scrollRef} ...>
```

这时 React 会在页面真正渲染出这个 `div` 后，把那个 DOM 节点放进：

```ts
scrollRef.current
```

所以：

- `scrollRef` 不是 DOM 元素本身
- `scrollRef.current` 才是当前拿到的 DOM 元素

也就是说，这里的 `scrollRef` 只是一个“用来找到滚动容器 DOM 的手柄”。

#### 3.2.3 先做一次自测：你能不能回答这 5 个问题

如果你已经读到这里，可以先试着回答下面 5 个问题：

1. 哪个 DOM 元素是滚动容器？
2. 代码是怎么拿到这个 DOM 元素的？
3. 它怎么判断“用户还在不在底部”？
4. 它为什么不总是立刻滚动，而要等下一帧？
5. 为什么用户离开底部后，不应该被强行拉回去？

##### 问题 1：哪个 DOM 元素是滚动容器

学习者回答：

```tsx
<div className="relative min-h-0 flex-1">
  <div ref={scrollRef} className="h-full overflow-auto p-4">
    <div className="mx-auto flex w-full max-w-4xl flex-col gap-4">
      {items.length > 0 ? (
        items.map((item) => {
          if (item.kind === 'user') {
            return <UserTurnBubble key={item.id} item={item} />
          }

          if (item.kind === 'assistant') {
            return <AssistantTurnBubble key={item.id} item={item} />
          }

          return <ToolCallCard key={item.id} item={item} />
        })
      ) : (
        <EmptyChatState />
      )}
    </div>
  </div>
</div>
```

当时的理解是：

- “聊天框有 `items`，所以滚动区域应该就是这一块”
- “第二个 `div` 绑定了 `scrollRef`，我猜这就是滚动容器”
- “但我不懂为什么这个绑定了 `scrollRef` 的 `div` 最外面又包了一个 `div`，里面还有一个 `div`”

教师回应：

你抓到的重点是对的，真正的滚动容器就是这一层：

```tsx
<div ref={scrollRef} className="h-full overflow-auto p-4">
```

关键不是因为它里面渲染了 `items`，而是因为它同时具备：

- `h-full`
  它自己的高度被限制住了

- `overflow-auto`
  内容超出时，浏览器允许它滚动

所以真正发生变化的滚动位置，是这个元素的：

```ts
element.scrollTop
```

你后面顺手问出来的那个问题也很关键：

“为什么滚动容器的上面和下面还各有一个 `div`？为什么不能只留一个？”

这个问题不该被跳过。答案是：这 3 层 `div` 的职责不同，不是随便多包了两层。后面 [3.2.4](#324-这三个-div-各自负责什么) 会专门把这三层拆开讲。

###### 这三个 `div` 各自负责什么

对应这一段结构：

```tsx
<div className="relative min-h-0 flex-1">
  <div ref={scrollRef} className="h-full overflow-auto p-4">
    <div className="mx-auto flex w-full max-w-4xl flex-col gap-4">
      ...
    </div>
  </div>
</div>
```

可以这样分工理解：

- 最外层 `div className="relative min-h-0 flex-1"`
  它不是滚动容器，而是聊天主体区域的外壳。
  `flex-1` 让它吃掉剩余空间，`min-h-0` 允许内部真的收缩，`relative` 则是为了让“跳到最新”按钮能用绝对定位贴在这块区域里。

- 中间层 `div ref={scrollRef} className="h-full overflow-auto p-4"`
  这才是滚动容器。
  它负责真正的滚动行为。

- 最里面 `div className="mx-auto flex w-full max-w-4xl flex-col gap-4"`
  它主要负责内容排版。
  比如限制最大宽度、水平居中、让消息卡片竖着排列、并且卡片之间留出间距。

所以如果你当时心里冒出来的问题是：

- “最外层为什么还要包一层？”
- “最里面为什么还要再包一层？”

可以直接翻译成：

- 外层负责区域和定位
- 中层负责滚动
- 内层负责排版

这三件事不是一回事，所以拆成三层会更清楚。

- `scrollRef`
  指向真正的滚动容器 DOM 元素

- `scrollToBottomRafIdRef`
  记录当前是否已经安排过一次 `requestAnimationFrame`
  目的：避免一帧里重复安排很多次滚动到底部

- `shouldFollowOutputRef`
  表示“当前是否应该跟随最新输出自动滚动”

- `shouldForceScrollOnNextUpdateRef`
  表示“下一次内容更新时，要不要强制滚到底部”
  典型场景是切换到新 session 后，希望先滚到正确位置

- `lastContentFingerprintRef`
  保存上一次内容快照指纹
  目的是判断“这次 store 变化到底是不是用户可见内容真的变了”

- `isAtBottom`
  React state，表示当前视觉上是否在底部

- `hasNewContent`
  React state，表示“用户不在底部时，下面是不是出现了新内容”

可以先记住一个简化版：

- `ref` 更像内部控制变量
- `state` 更像要拿去驱动界面的变量


##### 问题 2：代码是怎么拿到这个 DOM 元素的

学习者回答：

- “先声明了一个空的 `scrollRef`，然后在滚动容器上绑定这玩意”

教师回应：

这个理解已经是对的，只是还不够细。先声明一个 ref：

```ts
const scrollRef = useRef<HTMLDivElement>(null)
```

再把它绑定到 JSX 上：

```tsx
<div ref={scrollRef} ...>
```

React 在这个 `div` 真正渲染到页面后，会把对应的 DOM 节点塞进：

```ts
scrollRef.current
```

所以这里的理解可以先简化成：

- `scrollRef`
  是一个盒子

- `scrollRef.current`
  是这个盒子里当前装着的值

更准确一点说：

- 先准备一个 ref 盒子
- 初始时盒子里是 `null`
- React 渲染出对应的 `div` 后，把那个 DOM 节点塞进 `scrollRef.current`

###### `.current` 到底是什么

很多人第一次看到：

```ts
shouldFollowOutputRef.current = true
```

都会觉得奇怪，像是在问：

“为什么这里突然多了个 `.current`？它怎么什么都能装？”

这里最重要的一点是：

`.current` 不是这份业务代码自己起的名字，而是 React `ref` 这套 API 固定的字段名。

你可以先把：

```ts
const shouldFollowOutputRef = useRef(true)
```

粗暴理解成：

```ts
const shouldFollowOutputRef = {
  current: true,
}
```

所以后面写：

```ts
shouldFollowOutputRef.current = false
```

本质上只是：

“把这个盒子里当前装着的值，从 `true` 改成 `false`。”

要点是：

- `.current` 不是天生就该放布尔值
- 它只是“这个 ref 盒子里当前的值”
- 这个值是什么类型，取决于你想让这个 ref 存什么

例如：

```ts
const countRef = useRef(0)
```

这里 `.current` 是数字。

```ts
const nameRef = useRef('bruce')
```

这里 `.current` 是字符串。

```ts
const scrollRef = useRef<HTMLDivElement | null>(null)
```

这里 `.current` 是 DOM 元素或者 `null`。

而这句：

```ts
const shouldFollowOutputRef = useRef(true)
```

表示的是：

- 准备一个不会触发重新渲染的小盒子
- 里面装一个布尔值
- 这个布尔值表示“后面要不要跟随输出自动滚动”

所以：

```ts
shouldFollowOutputRef.current = atBottom
```

翻译成人话就是：

“把‘是否继续自动滚动’这个内部开关，更新成当前最新判断结果。”

学习者当时的真实困惑是：

- “我知道判断在不在底部是靠那两行公式”
- “但是 `shouldFollowOutputRef.current` 这个名字很怪”
- “`.current` 为什么能被赋值成 `true` / `false`？”

这个困惑非常正常，因为这里卡住的不是业务逻辑，而是 `ref` 这层 API 的抽象。

在 TypeScript 里，`.current` 也通常会受类型约束。

比如：

```ts
const shouldFollowOutputRef = useRef(true)
```

这里通常就意味着你会把它当成布尔值来用，而不是乱塞别的类型。

##### 问题 3：它怎么判断“用户还在不在底部”

学习者回答：

```ts
const distance = element.scrollHeight - element.scrollTop - element.clientHeight
const atBottom = distance <= SCROLL_BOTTOM_THRESHOLD_PX
```

当时真正想表达的是：

- “判断是否在底部，核心就是靠这两行代码”
- “但后面的 `shouldFollowOutputRef` 我没看懂”

教师回应：

你前半句答对了，判断是否在底部，核心确实就是这两行：

```ts
const distance = element.scrollHeight - element.scrollTop - element.clientHeight
const atBottom = distance <= SCROLL_BOTTOM_THRESHOLD_PX
```

它们的意思分别是：

- `scrollHeight`
  整个可滚动内容的总高度

- `scrollTop`
  当前已经向下滚了多少

- `clientHeight`
  当前可视区域本身有多高

所以：

```ts
scrollHeight - scrollTop - clientHeight
```

表示的是：

“从当前看到的位置，到最底部还剩多少距离。”

如果这个距离已经很小，就认为用户仍然在底部附近。

真正还没看懂的，其实不是“怎么判断”，而是：

“这个判断结果后面被谁拿来用？又会影响什么行为？”

##### 问题 4：它为什么不总是立刻滚动，而要等下一帧

学习者回答：

- “计算需要时间，所以不立刻滚动”

教师回应：

因为这段代码想做的是：

“等当前这轮界面更新先真正落到 DOM 和布局上，再滚到底部。”

如果太早去读写滚动位置，常见问题是：

- DOM 还没更新完整
- `scrollHeight` 还不是最终值
- 一轮更新里可能重复安排多次滚动

所以这里才会用 `requestAnimationFrame`，把真正的滚动动作推迟到下一帧。

##### 问题 5：为什么用户离开底部后，不应该被强行拉回去

学习者回答：

- “这是产品体验问题，不该打断用户看历史消息”

教师回应：

这是用户体验问题。

如果用户正在往上翻旧消息，说明他此刻更想看历史内容。

这时新内容继续流进来，如果界面每次都强行把他拽回底部，阅读体验会非常差。

所以更合理的策略是：

- 用户本来就在底部附近
  那就继续自动跟随输出

- 用户已经离开底部
  那就不要强滚，只提示“有新内容”


#### 3.2.6 `requestAnimationFrame` 是什么

你可以先把 `requestAnimationFrame` 理解成：

“浏览器，麻烦你在下一次准备重绘页面之前，帮我执行这个函数。”

它常用于：

- 视觉更新前再改 DOM
- 动画
- 把频繁的 DOM 操作推迟到下一帧

一个极简例子：

```ts
const id = window.requestAnimationFrame(() => {
  console.log('下一帧再执行')
})
```

它会返回一个数字 ID，所以你之后可以取消它：

```ts
window.cancelAnimationFrame(id)
```

#### 3.2.7 为什么这里不用“立刻滚动”，而要用 `requestAnimationFrame`

因为这段代码想做的是：

“等这次界面更新先落到 DOM 上，再滚到底部。”

如果太早读写滚动位置，常见问题是：

- DOM 还没更新完整
- `scrollHeight` 还不是最终值
- 一次更新里可能重复滚很多次

所以这里更稳妥的做法是：

1. 先把“要滚到底部”这件事登记下来
2. 到下一帧再真正执行

#### 3.2.8 `scrollToBottomRafIdRef` 为什么要存 ID

因为 `requestAnimationFrame` 会返回一个 ID。

这段代码把这个 ID 存在 `scrollToBottomRafIdRef.current` 里，是为了做两件事：

1. 防止重复安排
   如果已经安排过一次，就先别再安排

2. 组件卸载时可以取消
   免得页面都没了，回调还想执行

所以它不是在存“滚动位置”，而是在存“那次已预约的动画帧任务的编号”。

### 3.3 `syncAtBottomState` 是什么

它会根据滚动容器的当前位置，计算“离底部还有多远”。

如果距离很小，就认为用户还在底部附近：

- 更新 `shouldFollowOutputRef.current`
- 更新 `isAtBottom`
- 如果已经在底部，就顺手把 `hasNewContent` 清掉

所以这个函数本质上是在回答：

“用户现在算不算还在底部？”

### 3.4 `requestScrollToBottom` 是什么

它不是立刻滚动，而是安排在下一帧执行滚动。

这么做的原因通常是：

- 避免在一次状态变化里频繁直接读写 DOM
- 等浏览器把当前这轮渲染和布局先处理掉，再滚动到底部

而 `scrollToBottomRafIdRef` 的作用是防抖：

如果已经安排过一次，就不要重复安排。

### 3.5 这里几个 `useEffect` 分别干什么

这一段里有 4 个 effect，可以分别理解。

#### Effect 1：挂载时连接 WebSocket，卸载时断开

```ts
useEffect(() => {
  chatClient.connect()
  return () => {
    chatClient.disconnect()
  }
}, [])
```

这里依赖数组是空数组 `[]`，意思通常是：

- 组件首次挂载后执行一次
- 组件卸载时执行清理函数

所以它相当于：

- 进入页面时连上 WebSocket
- 离开页面时主动断开

#### Effect 2：监听滚动事件

```ts
useEffect(() => {
  const element = scrollRef.current
  if (!element) {
    return
  }

  const onScroll = () => syncAtBottomState(element)
  element.addEventListener('scroll', onScroll, { passive: true })
  syncAtBottomState(element)

  return () => {
    element.removeEventListener('scroll', onScroll)
  }
}, [syncAtBottomState])
```

它的作用是：

- 给滚动容器绑定 `scroll` 事件
- 每次滚动都重新判断“用户还在不在底部”
- effect 清理时移除事件监听

#### Effect 3：组件卸载时取消未执行的 `requestAnimationFrame`

```ts
useEffect(() => {
  return () => {
    const rafId = scrollToBottomRafIdRef.current
    if (rafId !== null) {
      window.cancelAnimationFrame(rafId)
    }
    scrollToBottomRafIdRef.current = null
  }
}, [])
```

它的目的很单纯：

“如果组件要卸载了，而之前安排过滚动动画帧，就把它取消掉，避免留下脏回调。”

#### Effect 4：订阅 store，决定何时自动滚动

这是最复杂的一个。

它做的事可以概括成：

1. 监听 store 里和内容展示有关的变化
2. 判断这次变化是不是用户看得见的变化
3. 如果应该跟随输出，就自动滚到底
4. 如果用户已经不在底部，就不要强滚，而是标记 `hasNewContent = true`

所以它本质上是在做“内容变化 -> 滚动策略”的转换。

### 3.6 `contentFingerprint` 是什么

这一长串拼接字符串的代码，本质上是在做一个轻量级“内容签名”。

目的不是做安全校验，而是做一个便宜的判断：

“和上次相比，真正影响界面内容的东西有没有变？”

如果签名没变，就不做后续滚动逻辑。

可以把它理解成一个很土但很好用的去重办法。

### 3.7 为什么这段代码会让人难读

因为它混在了一起处理三类东西：

- DOM 滚动
- React effect 生命周期
- store 内容变化后的滚动策略

所以第一次读时，不要逐行抠。

更好的方式是按下面顺序理解：

1. 先明白目标：自动滚底，但不要打断用户翻历史消息
2. 再看有哪些状态在支撑这个目标
3. 最后再看 4 个 effect 分别负责哪一件事

### 3.8 `useEffect` 最基础的记法

可以先只记这三种：

#### 没有依赖数组

```ts
useEffect(() => {
  ...
})
```

通常表示：

“每次渲染后都执行。”

#### 空依赖数组

```ts
useEffect(() => {
  ...
}, [])
```

通常表示：

“挂载后执行一次，卸载时执行清理函数。”

#### 有依赖数组

```ts
useEffect(() => {
  ...
}, [a, b])
```

通常表示：

“首次挂载后执行一次；以后只要 `a` 或 `b` 变化，就再执行。”

如果 effect 返回了函数：

```ts
useEffect(() => {
  ...
  return () => {
    ...
  }
}, [a])
```

那这个返回函数就是清理函数。它通常会在：

- 下一次 effect 重新执行前先跑一次
- 组件卸载时再跑一次

## 4. 这 6 个状态分别是什么

### 4.1 `connectionStatus`

它描述的是 WebSocket 连接层的状态，不是聊天内容本身。

可选值：

- `idle`
- `connecting`
- `open`
- `closed`
- `error`

它主要用来驱动顶部状态展示、连接提示之类的 UI。

### 4.2 `errorMessage`

它是当前需要展示给用户的错误文本。

错误既可能来自前端本地逻辑，也可能来自后端通过 WebSocket 发来的 `error` 事件。

### 4.3 `sessionId`

它表示“后端已经成功创建的聊天会话 ID”。

之所以需要 `session.started` 这个协议事件，是因为：

- 会话 ID 的权威来源在后端
- WebSocket 已连接，不代表聊天会话一定初始化成功
- 前端界面确实要显示这个会话 ID

所以 `session.started` 的意思不是“连接建立了”，而是“后端业务会话建立好了，而且它的 ID 是这个”。

### 4.4 `items`

它是时间线卡片数组，也是最核心的状态。

注意：它不是后端直接返回的一整个“聊天记录数组”，而是前端根据一连串协议事件投影出来的界面模型。

这就是为什么 store 里会有很多“根据事件更新状态”的逻辑。

### 4.5 `pendingUserMessages`

它表示“用户已经点发送，但后端还没正式确认提交进会话”的消息。

之所以单独保留它，是为了区分两个阶段：

1. 前端本地先暂存
2. 后端确认后，才真正进入时间线 `items`

### 4.6 `activeAssistantTurnId`

它表示“当前是否有一轮 assistant 正在生成”。

它主要是给 UI 做“生成中”提示，而不是正文内容本身。

## 5. 为什么会有 `protocol.ts`

可以先把协议理解成“前后端之间约定好的事件字典”。

前端不是直接拿到一整块最终结果，而是持续收到很多事件，例如：

- `session.started`
- `user.turn.enqueued`
- `user.turn.committed`
- `assistant.content.delta`
- `assistant.reasoning.delta`
- `assistant.tool.started`
- `assistant.tool.completed`
- `tool.result`

这些事件的目的，是让前端能以流式方式更新界面，而不是等后端一次性算完再整包返回。

## 6. `useChatStore` 到底是什么

`useChatStore` 是一个函数，更准确地说，它是 zustand 生成出来的一个 hook 函数。

你可以把它理解成：

```ts
const useChatStore = create(...)
```

也就是：

- `create` 是 zustand 提供的“造 store 的函数”
- `useChatStore` 是 `create(...)` 造出来的 store hook

在组件里这样用：

```ts
const connectionStatus = useChatStore((state) => state.connectionStatus)
```

意思是：

“从整个 store 里，只订阅 `connectionStatus` 这一小块。”

## 7. `create<ChatStore>()((set) => ({ ... }))` 是什么

这是最容易把人看晕的一行，但拆开后其实不复杂。

### 7.1 `<>` 是什么

`<ChatStore>` 是 TypeScript 的泛型参数。

它只是告诉 TypeScript：

“这个 store 的整体类型按 `ChatStore` 来理解。”

它不是运行时参数，不会传给浏览器，也不是 HTML 标签。

### 7.2 为什么有两层括号

因为这里是“函数返回函数”的写法。

可以把它脑补成：

```ts
const step1 = create<ChatStore>()
const useChatStore = step1((set) => ({ ... }))
```

也就是说：

- 第一次 `()`：先得到一个中间函数
- 第二次 `()`：再把 store 的定义传进去

### 7.3 `(set) => ({ ... })` 是什么

这是一个箭头函数。

它的意思是：

“zustand，你给我一个 `set` 函数，我来告诉你这个 store 初始长什么样，以及有哪些操作方法。”

所以返回对象里通常包含两类内容：

- 状态字段
- 修改状态的方法

例如：

```ts
{
  ...initialChatState,
  setConnectionStatus: (status) => { ... },
  applyServerEvent: (event) => { ... },
}
```

### 7.4 为什么这里是 `({ ... })`，不是直接 `{ ... }`

这个点非常关键，因为它是 JavaScript / TypeScript 箭头函数的语法规则，不是 zustand 的特殊写法。

先看两种写法：

```ts
const f1 = (x) => x + 1
const f2 = (x) => ({ value: x + 1 })
```

这里：

- `f1` 直接返回表达式 `x + 1`
- `f2` 直接返回一个对象

如果箭头函数想直接返回一个对象，外面必须包一层小括号：

```ts
(x) => ({ value: x + 1 })
```

因为如果你写成：

```ts
(x) => { value: x + 1 }
```

JavaScript 会把这对 `{}` 当成“函数体”，而不是“对象字面量”。

也就是说，上面这句会被理解成：

```ts
(x) => {
  value: x + 1
}
```

这不是在返回对象，而更像是函数体里出现了一个 label，结果通常不是你想要的。

所以可以先记一个简单规则：

- 箭头函数如果直接返回一个普通表达式，可以不写函数体花括号
- 箭头函数如果直接返回对象，要写成 `({ ... })`
- 箭头函数如果用了函数体花括号 `{ ... }`，那通常就要自己写 `return`

例如：

```ts
const f3 = (x) => {
  return { value: x + 1 }
}
```

这和前面的 `f2` 是一个意思。

### 7.5 表达式和对象是什么关系

这里最容易混淆的点是：

- 表达式
- 对象

它们不是同一层概念。

可以先这样理解：

- 表达式：一段代码，执行后会得到一个值
- 对象：一种值的类型

例如：

```ts
1 + 2
'hello'
true
{ name: 'bruce' }
```

这些都可以看作表达式，因为它们都能算出一个值。

而它们算出来的值又分别属于不同类型：

- `1 + 2` 算出来是数字
- `'hello'` 是字符串
- `true` 是布尔值
- `{ name: 'bruce' }` 是对象

所以“对象”和“表达式”的关系是：

“对象是值的一种；对象字面量 `{ ... }` 可以作为一种表达式来使用。”

这也是为什么前面会说：

```ts
(x) => ({ value: x + 1 })
```

这里箭头函数右边返回的是一个表达式，而这个表达式求值后得到的是一个对象。

### 5.6 在 zustand 这里为什么要返回对象

在这段代码里：

```ts
create<ChatStore>()((set) => ({
  ...initialChatState,
  setConnectionStatus: (status) => { ... },
}))
```

`(set) => ({ ... })` 这个函数的任务，就是把“store 的内容”交给 zustand。

而 store 的内容天然就是一个对象，因为它里面要同时放：

- 状态字段
- action 函数

也就是说，这里不是 JavaScript 规定“箭头函数必须返回对象”，而是 zustand 这次调用约定你要返回一个“描述 store 内容的对象”。

如果你换成别的函数，未必一定要返回对象；但在这个 store 初始化位置，返回对象才符合它的用途。

## 8. `...initialChatState` 是什么

它是对象展开语法。

意思是把 `initialChatState` 里的字段平铺到当前对象里。

例如：

```ts
const a = { x: 1, y: 2 }
const b = { ...a, z: 3 }
```

那么 `b` 就是：

```ts
{ x: 1, y: 2, z: 3 }
```

放到 store 里，就是：

“先把初始状态字段都放进去，再额外补上 action 函数。”

这是一种很常见的写法，但不是强制规则。

## 9. 为什么 `useChatStore((state) => state.connectionStatus)` 要传函数

关键点是：这里的 `state` 不是你当前作用域里已经有的变量。

它是 `useChatStore` 在内部拿到完整 store 后，再传给你的参数。

这里的 `state` 只是这个函数的一个形参名，不是某个提前定义好的特殊变量。

所以它完全可以改名，例如下面这两种写法是等价的：

```ts
useChatStore((state) => state.connectionStatus)
```

```ts
useChatStore((storeState) => storeState.connectionStatus)
```

之所以通常写成 `state`，只是因为可读性最好，大家一看就知道“这里传进来的是完整 store 状态”。

所以：

```ts
state.connectionStatus
```

表示：

“我手里已经有一个 `state` 对象了，现在直接取字段。”

而：

```ts
useChatStore((state) => state.connectionStatus)
```

表示：

“我把一个取值规则交给 `useChatStore`，等它内部拿到完整 state 后，只把 `connectionStatus` 那一小块给我。”

这个取值规则就叫 selector。

## 10. selector 是什么

selector 本质上就是：

“从完整 state 里挑出你真正关心的那一小块数据的函数。”

例如：

```ts
(state) => state.connectionStatus
```

输入是完整 state，输出是 `connectionStatus`。

如果你把它换成更直白但更啰嗦的名字，其实更容易看清本质：

```ts
(wholeStoreState) => wholeStoreState.connectionStatus
```

也就是说，selector 并不神秘，它只是一个普通函数：

- 参数：完整 store 状态
- 返回值：你想订阅的那一小块数据

### 10.1 为什么不直接拿整个 store

因为如果你写：

```ts
const store = useChatStore()
const connectionStatus = store.connectionStatus
```

那你通常就是在订阅整个 store。

而如果你写：

```ts
const connectionStatus = useChatStore((state) => state.connectionStatus)
```

你表达的是：

“我只关心这一个字段。”

这样通常更清晰，也更利于减少不必要的重渲染。

## 11. `set({ ... })` 和 `set((state) => ({ ... }))` 的区别

这也是 zustand 里非常常见的两种写法。

### 11.1 直接传对象

适合“不依赖旧状态”的更新：

```ts
set({
  connectionStatus: status,
})
```

意思是：

“把这些字段直接更新成新值。”

### 11.2 传函数

适合“要基于旧状态算新状态”的更新：

```ts
set((state) => ({
  pendingUserMessages: upsertPendingMessage(state.pendingUserMessages, userTurnId, content),
}))
```

这里先要读旧的 `state.pendingUserMessages`，再计算新的值，所以更适合函数写法。

## 12. 现在应该怎么继续看 `store.ts`

最推荐的顺序是：

### 12.1 先看类型定义

先搞清楚 store 里到底有哪些状态字段、有哪些 action。

### 12.2 再看辅助函数

像这些函数：

- `createAssistantItem`
- `createToolItem`
- `upsertPendingMessage`
- `ensureAssistantSegment`
- `ensureToolItem`

它们不是框架魔法，基本都是“帮助 reducer 计算新状态”的普通函数。

### 12.3 最后看 `reduceServerEvent`

这里才是整个 store 的核心。

你可以把它理解成：

“后端每发来一个协议事件，前端就根据事件类型，把状态推进一步。”

## 13. 当前最重要的心智模型

先记住这三句话就够了：

1. `App.tsx` 负责消费状态，不负责维护状态
2. `store.ts` 负责把协议事件变成前端状态
3. `protocol.ts` 负责定义前后端到底传什么
