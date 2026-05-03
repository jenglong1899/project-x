# App.tsx 聊天区布局

## 两层容器的分工

聊天区这里需要拆成两层，是因为“谁负责滚动”和“谁负责内容宽度”不是同一个问题。

```tsx
<div ref={scrollRef} className="h-full overflow-auto px-4 py-6">
  <div className="flex flex-col gap-6 mx-auto w-full max-w-3xl">
    ...
  </div>
</div>
```

整体关系可以理解为：

```text
main 剩余高度
└── scrollRef 外层：占满高度 + 产生滚动条 + 提供页面边距
    └── 内层内容：限制最大宽度 + 居中 + 垂直排列消息
        ├── user bubble
        ├── assistant bubble
        └── tool card
```

外层 `div` 的职责是成为滚动视口：

- `h-full`：高度等于父容器给它的高度。
- `overflow-auto`：内容超过高度时，在这一层出现滚动条。
- `px-4 py-6`：给滚动内容留出四周边距，避免消息贴边。
- `ref={scrollRef}`：让代码可以读写 `scrollTop`、`scrollHeight`、`clientHeight`，实现自动滚到底和“跳到最新”。

内层 `div` 的职责是控制聊天内容本身的排版：

- `flex flex-col`：让消息按纵向一条一条排列。
- `gap-6`：消息之间留固定间距。
- `mx-auto`：在外层可用宽度里水平居中。
- `w-full`：小屏时占满可用宽度。
- `max-w-3xl`：大屏时不要无限拉宽，保持聊天气泡可读。

## `max-w-3xl` 的拼写

注意 Tailwind 里是 `max-w-3xl`，不是 `max-w-3x`。

如果写成 `max-w-3x`，Tailwind 默认不会生成对应 CSS，这个 class 等于无效。结果通常是内层只剩下 `w-full mx-auto`，聊天内容会在大屏上铺满整行，可读性变差。

## `3xl` 的底层单位

在当前项目使用的 Tailwind 4.2.1 默认主题里，`max-w-3xl` 对应的是容器尺寸变量：

```css
--container-3xl: 48rem;
```

所以它底层写的是 `rem`，不是直接写死的 `px`。

在浏览器默认字号 `html { font-size: 16px; }` 没有被改掉时：

```text
48rem = 48 * 16px = 768px
```

但关键点是：`rem` 会跟随根元素 `html` 的字体大小变化。如果以后把根字号改成 `10px`，那 `48rem` 就会变成 `480px`；如果根字号是 `18px`，那它就是 `864px`。

可以把它理解成：

```text
max-w-3xl
└── max-width: var(--container-3xl)
    └── --container-3xl: 48rem
        └── 默认浏览器环境下约等于 768px
```
