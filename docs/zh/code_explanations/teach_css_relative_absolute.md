# CSS 的 `relative` 和 `absolute`

这篇只讲读 `frontend/src/App.tsx` 时最容易卡住的一组概念：为什么外层要写 `relative`，里面的提示框和按钮为什么写 `absolute`。

先给结论：

```text
relative
└── 让一个元素成为 absolute 子元素的定位参照物

absolute
└── 让一个元素脱离普通布局，贴到某个参照物的指定位置
```

在当前代码里，它们配合起来做的是：

```text
聊天区外壳：relative
├── 右上角连接异常提示：absolute right-4 top-4
├── 中间滚动消息列表：普通布局
└── 右下角跳到最新按钮：absolute bottom-4 right-4
```

# 普通布局流

理解 `absolute` 之前，先要理解“不 absolute”时浏览器默认怎么摆元素。

普通布局流的核心规则是：元素会占据自己的位置，并且影响后面的元素。

一个很粗略的模型是：

```text
父元素
├── 子元素 A：占一块空间
├── 子元素 B：排在 A 后面
└── 子元素 C：排在 B 后面
```

如果 A 变高，B 和 C 通常会被往后推。

这就是普通布局流的价值：大部分内容页面都应该这样排。聊天消息列表、输入框、普通按钮、普通段落，都应该让浏览器按正常顺序排版。

但是有些 UI 不想参与这种排队。

比如 `App.tsx` 里的连接异常提示：

```tsx
<div className="pointer-events-none absolute right-4 top-4 z-20 ...">
  ...
</div>
```

它不是聊天消息的一部分，也不应该把消息列表往下推。它只是一个临时浮在右上角的提示。

所以这里需要 `absolute`。

# `absolute`

`absolute` 的关键不是“绝对在屏幕上”，而是“脱离普通布局流，然后按参照物定位”。

它做两件事：

```text
absolute
├── 脱离普通布局流
│   └── 不再给自己保留原来的排队位置
└── 根据最近的定位参照物计算位置
    └── top / right / bottom / left 才知道从哪里量
```

所以这段：

```tsx
<div className="absolute right-4 top-4">
```

大致意思是：

```text
把这个元素从普通布局里拿出来
└── 贴到定位参照物的右上角
    ├── 距离右边 1rem
    └── 距离上边 1rem
```

Tailwind 里的 `right-4` 和 `top-4` 对应的通常是：

```css
right: 1rem;
top: 1rem;
```

这里容易误会：`top-4` 里的 `4` 不是 `4rem`，而是 Tailwind spacing scale 里的第 4 档。

Tailwind 默认 spacing scale 里常见的对应关系是：

```text
0   -> 0px
1   -> 0.25rem
2   -> 0.5rem
3   -> 0.75rem
4   -> 1rem
5   -> 1.25rem
6   -> 1.5rem
8   -> 2rem
```

所以：

```text
top-4
└── top: 1rem

right-4
└── right: 1rem
```

这个 `4` 更像是“设计系统里的间距编号”，不是 CSS 单位本身。

为什么 Tailwind 不让 `4` 直接等于 `4rem`？因为界面里最常用的是小间距。按钮内边距、卡片间距、浮层边距经常是 `4px`、`8px`、`12px`、`16px` 这种节奏，而不是一下子跳到 `64px`。

可以粗略理解成：

```text
Tailwind spacing 数字
└── 默认每加 1，大约增加 0.25rem，也就是 4px
```

所以：

```text
4 * 0.25rem = 1rem
```

这里的 `rem` 是 CSS 里的长度单位。它的意思是：

```text
1rem = 根元素 html 的 font-size
```

`rem` 是 `root em` 的缩写。

这里的 `root` 指页面根元素，通常就是：

```html
<html>
```

`em` 是 CSS 里另一个相对长度单位。`em` 会参考当前元素自己的字体大小，而 `rem` 固定参考根元素字体大小，所以 `rem` 更稳定。

可以先这样记：

```text
em
└── 相对当前元素的 font-size

rem = root em
└── 相对根元素 html 的 font-size
```

浏览器默认情况下，`html` 的字体大小通常是 `16px`，所以大多数时候可以先粗略记成：

```text
1rem ≈ 16px
```

于是：

```text
right: 1rem
└── 距离右边大约 16px

top: 1rem
└── 距离上边大约 16px
```

为什么不用直接写 `16px`？因为 `rem` 会跟随全局字号设置变化。

如果用户或应用把根字号调大，比如：

```css
html {
  font-size: 20px;
}
```

那么：

```text
1rem = 20px
```

这会让页面间距、字号等一起按比例变大，更适合做整体缩放和无障碍适配。

顺便说一下 `px`。

你可以先把 `px` 理解成 CSS 里的固定长度单位，但不要把它理解成固定的物理长度，比如“固定等于多少厘米”。

更准确地说：

```text
CSS px
├── 是浏览器排版用的逻辑像素
├── 在同一个页面布局里通常表现得像固定单位
└── 不保证等于屏幕上的一个硬件发光点，也不保证等于固定厘米数
```

这里容易混淆两个东西：

```text
CSS px
└── 写 CSS 时用的逻辑单位，比如 width: 100px

设备物理像素
└── 屏幕面板上真实的像素点
```

在普通屏幕上，它们可能接近一一对应。但在 Retina / 高分屏上，常见情况是：

```text
1 个 CSS px
└── 可能由 2x2 个甚至更多物理像素来显示
```

所以读前端布局时，先记这个实用版本就够了：

```text
px
└── 页面布局里的固定逻辑长度

rem
└── 跟根字号相关的相对长度
```

那是不是应该总是用 `rem`，不用 `px`？

不是。`rem` 很有用，但不是所有地方都比 `px` 好。

更实用的判断是：

```text
希望跟随整体字号/缩放一起变化
└── 优先用 rem

希望表达非常具体的细线、边框、阴影偏移、像素级修正
└── 可以用 px
```

比如这些地方用 `rem` 很自然：

```css
font-size: 1rem;
padding: 1rem;
gap: 0.75rem;
```

因为字号、内边距、元素间距通常应该跟整体 UI 尺寸一起变化。

这些地方用 `px` 也很正常：

```css
border-width: 1px;
box-shadow: 0 1px 2px rgb(0 0 0 / 0.2);
```

因为 `1px` 边框通常就是想表达一条很细的线。如果根字号变大，不一定希望边框也跟着从 `1px` 变成更粗。

在当前项目里，很多 class 来自 Tailwind。Tailwind 已经帮你把常见间距做成了 scale：

```text
top-4 / right-4 / px-4 / py-6 / gap-3
└── 这些通常会落到 rem

border / ring-1
└── 这些通常更接近 px 级别的视觉线条
```

所以读代码时不必先纠结“到底该用 px 还是 rem”。更重要的是看这个值在表达什么：

```text
表达布局节奏
└── 通常是 Tailwind spacing，也就是 rem 系统

表达细节线条
└── 通常是 px 级别
```

同理，`App.tsx` 里的“跳到最新”按钮：

```tsx
<div className="pointer-events-none absolute bottom-4 right-4">
```

意思是：

```text
把这个元素从普通布局里拿出来
└── 贴到定位参照物的右下角
    ├── 距离右边 1rem
    └── 距离下边 1rem
```

# `relative`

`relative` 在当前代码里的主要作用是：提供 `absolute` 的定位参照物。

来看 `App.tsx` 的结构：

```tsx
<main className="flex flex-col min-h-0 min-w-0 flex-1 bg-zinc-950">
  <div className="relative min-h-0 flex-1">
    <div className="absolute right-4 top-4">连接异常提示</div>

    <div ref={scrollRef} className="h-full overflow-auto px-4 py-6">
      消息列表
    </div>

    <div className="absolute bottom-4 right-4">跳到最新</div>
  </div>

  <footer>输入框</footer>
</main>
```

这里的外层 `relative` 可以理解成给聊天区画了一个“定位坐标系”：

```text
main
├── 聊天区外壳：relative
│   ├── top/right/bottom/left 都以聊天区外壳为参照
│   ├── 连接异常提示贴在聊天区右上角
│   ├── 消息列表在聊天区内部滚动
│   └── 跳到最新按钮贴在聊天区右下角
└── footer：输入框
```

如果去掉这个 `relative`，里面的 `absolute` 元素会继续向祖先元素里找“定位参照物”。

查找规则可以先记成：

```text
absolute 元素
└── 向父级、祖父级、曾祖父级一路找
    └── 找最近的 position 不是 static 的祖先
        └── 用它作为定位参照物
```

CSS 里大多数元素默认是：

```css
position: static;
```

而 `relative`、`absolute`、`fixed`、`sticky` 都属于“position 不是 static”。

所以写：

```tsx
<div className="relative min-h-0 flex-1">
```

就是明确告诉浏览器：

```text
这个聊天区外壳就是里面浮层的参照物
```

# 为什么不是直接放在页面右上角

当前代码不希望连接异常提示和“跳到最新”按钮跟整个浏览器窗口绑定，而是希望它们跟聊天主区绑定。

原因是页面结构大致是：

```text
整页
└── main
    ├── 聊天区
    │   ├── 浮层提示
    │   └── 消息滚动区
    └── footer 输入框
```

右下角按钮如果相对整个窗口定位，就可能压到 footer 输入框。

当前写法更符合 UI 语义：

```text
跳到最新按钮属于聊天滚动区
└── 所以它应该贴在聊天区右下角，而不是整页右下角
```

这也是外层 `relative` 放在聊天区外壳上，而不是随便放到更外层的原因。

# `relative` 自己会不会移动元素

只写 `relative`，不写 `top/right/bottom/left`，元素不会从视觉上移动。

例如：

```tsx
<div className="relative min-h-0 flex-1">
```

这里的 `relative` 基本不改变这个 `div` 在普通布局里的位置。

它仍然作为 `main` 的 flex 子元素参与布局：

```text
main：flex flex-col
├── 聊天区外壳：relative min-h-0 flex-1
└── footer：输入框
```

`relative` 只是额外提供一个定位参照物。

如果写成下面这样，才会让元素相对自己原本位置偏移：

```html
<div class="relative top-4">
```

这个意思是：

```text
先按普通布局算出原本位置
└── 再从原本位置向下偏移 1rem
```

但当前 `App.tsx` 没有这样用。

# `absolute` 会不会影响父元素高度

通常不会。

因为 `absolute` 元素脱离了普通布局流，它不会像普通子元素那样把父元素撑高。

对比一下：

```text
普通子元素
└── 会占空间，可能撑高父元素

absolute 子元素
└── 浮在上面，通常不参与父元素高度计算
```

所以当前聊天区的高度不是由这两个浮层决定的：

```text
聊天区高度
├── 主要由 main 的 flex 布局决定
├── min-h-0 允许它在 flex 布局里收缩
└── h-full overflow-auto 让消息列表在内部滚动
```

`relative/absolute` 只是在这个已经确定大小的区域里放浮层。

如果你正在读 `min-h-0`，可以配合看另一篇：`docs/zh/code_explanations/teach_flex_min_h_0.md`。

# `z-20`

`z-20` 处理的是“谁盖在谁上面”，不是“放在哪里”。

它不是 CSS 原生属性名，而是 Tailwind 的简写 class。

Tailwind 里的：

```html
<div class="z-20">
```

大致会生成：

```css
.z-20 {
  z-index: 20;
}
```

所以 `z-20` 可以拆成：

```text
z
└── 对应 CSS 的 z-index

20
└── z-index 的数值
```

为什么叫 `z-index`？可以把屏幕想成一个二维平面：

```text
x 轴：左右
y 轴：上下
z 轴：前后
```

普通布局里的 `top/right/bottom/left` 主要是在二维平面里放位置：

```text
x / y
└── 元素在屏幕上的哪个位置
```

而 `z-index` 处理的是第三个方向：

```text
z
└── 多个元素重叠时，谁离用户更近、谁盖在谁上面
```

一个粗略模型是：

```text
z-index: 20  更靠前
z-index: 10  在中间
z-index: 0   更靠后
```

你可能会以为层级应该写成 `0 / 1 / 2 / 3`。CSS 确实可以这么写。

也就是说，这些都是合法的：

```css
z-index: 0;
z-index: 1;
z-index: 2;
z-index: 3;
```

Tailwind 默认常用 `0 / 10 / 20 / 30 / 40 / 50`，不是因为 CSS 只能这么写，而是因为设计系统通常会故意留出空档。

留空档的好处是：以后中间要插一个层级时，不需要改一堆已有数字。

比如一开始是：

```text
z-index: 10  普通浮层
z-index: 20  重要提示
```

后来发现中间还需要一层：

```text
z-index: 10  普通浮层
z-index: 15  中间浮层
z-index: 20  重要提示
```

如果一开始写的是：

```text
z-index: 1  普通浮层
z-index: 2  重要提示
```

中间当然也可以插 `1.5` 吗？不行，`z-index` 只能用整数。你可以改成 `1 / 2 / 3`，但那就可能牵连已有代码。

所以很多系统会选择：

```text
0, 10, 20, 30, 40, 50
```

这跟楼层编号有点像：有些系统会故意用 `100 / 200 / 300`，方便以后在中间插 `150`。

Tailwind 也支持任意值。如果真的需要一个不在默认档位里的值，可以写：

```html
<div class="z-[15]">
```

这大致就是：

```css
z-index: 15;
```

但一般不建议一上来就到处写任意值。优先用项目设计系统里的固定档位，代码更容易读：

```text
z-10
└── 普通浮层

z-20
└── 更高一层的提示

z-50
└── 很靠上的弹窗或全局浮层
```

注意，这不是 3D 效果。它只是决定重叠时的绘制顺序。

连接异常提示写了：

```tsx
<div className="pointer-events-none absolute right-4 top-4 z-20 ...">
```

这里可以拆开看：

```text
absolute right-4 top-4
└── 决定它在右上角

z-20
└── 决定它在重叠时更靠上
```

如果没有 `z-20`，它仍然可能在右上角；只是当它和别的元素重叠时，层叠顺序可能不是我们想要的。

为什么这里需要它？因为连接异常提示是浮层 UI，它的语义是“盖在聊天内容上提醒用户”。如果消息内容、滚动区域或其他浮层刚好和它重叠，我们希望提示仍然可见。

所以这里的组合可以理解成：

```text
absolute right-4 top-4
└── 放到聊天区右上角
    └── z-20
        └── 重叠时画在更上层
```

# `pointer-events-none` 和 `pointer-events-auto`

这两个 class 不是定位本身，但它们经常和浮层一起出现。

当前连接异常提示外层是：

```tsx
<div className="pointer-events-none absolute right-4 top-4 z-20 ...">
  <div className="pointer-events-auto ...">
    ...
  </div>
</div>
```

这表达的是：

```text
浮层外壳
├── pointer-events-none：透明区域不拦截鼠标
└── 实际提示卡片
    └── pointer-events-auto：卡片本身仍然可以接收鼠标事件
```

在这个例子里，提示卡片只是展示状态，没有按钮，所以差异不明显。

但这种写法在浮层 UI 里很常见：外层负责定位和占一片浮层区域，内层负责真正可交互的内容。

“跳到最新”按钮也是同一个模式：

```tsx
<div className="pointer-events-none absolute bottom-4 right-4">
  <Button className="pointer-events-auto">
    跳到最新
  </Button>
</div>
```

这样按钮周围的透明区域不会挡住下面的聊天区滚动或选择文本，只有按钮本身能点击。

# 最小记忆法

读这类代码时，按下面顺序拆：

```text
1. 先找 absolute
   └── 这个元素是不是一个浮层？

2. 再向外找 relative
   └── 它相对哪个盒子定位？

3. 再看 top/right/bottom/left
   └── 它贴在参照物的哪个角或哪条边？

4. 最后看 z-index 和 pointer-events
   └── 它怎么覆盖别人？透明区域要不要挡鼠标？
```

套回 `App.tsx`：

```text
absolute right-4 top-4
└── 连接异常提示浮在聊天区右上角

absolute bottom-4 right-4
└── 跳到最新按钮浮在聊天区右下角

relative min-h-0 flex-1
└── 聊天区外壳既参与 flex 布局，又作为两个浮层的定位参照物
```
