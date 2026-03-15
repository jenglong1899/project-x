# 背景
本产品面向普通用户
llm是锯齿状智能，应给监督 LLM 的工作过程提供便利。用户如果决定不进行监督，用户自己承担风险。

可以考虑：
- 在不给普通用户添加负担的情况下开放高级功能给技术用户使用。

# 总体应该有的功能
有网页端，仅支持流式。
支持重置记忆
支持steer conversation
支持定时任务
接入im
支持multiagent

# 基础工具

## bash
先做最简单的

后续：
1. 支持后台运行。用steer conversation来做通知。运行超过5秒自动转入后台。
2. 在后台持久化？比如记录路径，通过终端设置的环境变量，不用每次都要 cd。需要考虑这个会不会影响沙盒，还没有研究过沙盒

## read
filepath:str #绝对或相对路径。支持普通文本文件或图片文件。
context_percentage_limit:float=5 #默认一次性最多读取5%的上下文的内容，如果超过了，就返回空内容并报错，提示总共有多少百分比的内容。
line_display:bool # 是否显示行号。用sed的那种风格来显示行号

->str
类似：
```
some_filepath:1-200
```

为什么超过了百分比就直接返回空内容，而不是返回5%的内容？我潜意识中有这个答案，但是我一时想不起来了。一种情况好像是，不完整的答案有时候还不如不要
line_display 之前吃过亏，就是 DS 3.2或者 Minimax 这种算是比较智能的模型，如果你用自己的自定义的行号分隔符，它有时候还是会误解，就以为那个行号分隔符是文件内容的一部分。

## replace
(
    file_path: str = Field(description="绝对或相对路径")
    needle: str = Field(
        description="要搜索的字符串或正则表达式模式。"
                    "如果mode是\"literal\"，将精确匹配此字符串。"
                    "如果mode是\"regex\"，此字符串将被视为正则表达式（使用 Python re 模块的语法，"
                    "并启用 DOTALL 和 MULTILINE 标志）。")
    repl: str = Field(
        description="用于替换的字符串。"
                    "如果模式是\"regex\"，该字符串可以包含对 needle 正则表达式中匹配组的反向引用，"
                    "使用语法 $!1、$!2 等指定第 1、2 等组。")
    mode: Literal["literal", "regex"] = Field(description="指定如何解释needle参数。")
    allow_multiple_occurrences: bool = (
        Field(False, description=
        "如果为 True，正则表达式可能匹配文件中的多个出现项，所有出现项都将被替换。"
        "如果设置为 False 且正则表达式匹配了多个出现项，将返回错误（您可以用修订后、更具体的正则表达式重试）。"))
)
```
使用形式为"beginning.*?end-of-text-to-be-replaced"的正则表达式能让你引用一大段文字而无需完整输入它们
如果是要编辑json文件，你自己现场写个python脚本，用它的json库来编辑可能会更方便。
```

这里虽然是用了 Pydantic 的那个 description，但是到时候用的时候还是不要用这个
一个函数的整体用途和它的各个参数的含义应该放在一块，这样子才好。如果用pydantic的话，就会割裂开来。
要有一个专门的字段来存储给 AI 看的tool description，函数自己的 docstring 是用来给开发者看的，也就是在那个 AI 看的说明之外，再额外加一个给开发者看的注释，有时候会需要用到。

有了 replace 其实是不需要 insert before/after 了。

由于正则表达式替换容易出问题，应该在返回结果中自动显示修改的地方附近的修改情况

## undo
正则表达式还是挺容易出问题的，所以有必要写一个撤销工具，人类在用各种编辑器的时候也是有撤销工具的
用git来做
利用之前的replace的tool_call_id来引用某一个编辑

## write
filepath:Path
text:str
mode:Literal['w','a']
)
如果文件不存在，会自动创建，哪怕在路径上有多个文件夹都没有创建（只靠命令行就做不到这一点）。
有时候你就是想追加写，那么直接用这个就可以了
用replace来做追加写的话，你还要读一下最后的内容。

# session store
用jsonl存储，路径在~/.bionic-claw/memory/originals/中
分为两大部分，一个是 meta 元数据，一个是 messages 数组
名字用coolname+时间戳（coolname要用第三方库，不要自己手搓）

meta包括：
- display-name：用户发的第一句话。这个是给前端展示用的，前端不展示这个jsonl的名字，而是展示这个。

messages中，每个message还要有个meta字段，包括
- timestamp

# 记忆
发现messages中是第一次调用reset context，那么第一次调用的时候先不执行，而是返回提示，类似于“请再检查一下你的记忆文档写的怎么样了”

将来直接把记忆写进 AGENTS.md 里面，系统自动加载进上下文，不用每次都要agent手动读了

# 自动提醒功能
不要“隔一定的上下文阈值提醒，比如在上下文达到85%、70%、65%的时候，提醒 AI 检查是否要重置上下文。”
而是“隔一定的消息数量就自动提醒一次 AI 检查是否要重置上下文。”
因为：每一个消息（ai msg with tool call 或者 tool msg）就基本代表了 AI 正在做一个事情，人类在做事情的时候，就是时刻都在记录东西。
另外可以再提醒一下当前还剩下多少上下文

# 中断后继续
假设 AI 正在运行，然后你不小心中断了。技术上来说，确实是可以再调用一次stream(messages)，可以额外加一个功能：如果检测到 user message 是 `/resume_break`，那么就调用一次 stream。
但问题是讲解这玩意就是一个成本，用户看到了很懵逼。
其实用户完全可以说“我刚才不小心中断了，请继续。”
所以目前的决定是不做这个功能。

# 暂停
理论上实现steer conversation后，用户发一句“你先停一下”也可以做到

# 中断
之前，Meta 的对齐主管告诉 OpenClaw 停下来，结果 OpenClaw 还是继续，那么根据这个情况，确实有必要加个中断按钮。
但那个报道并没有说用的是什么模型。

# 接入qq等im平台
假设你在一个工作群里面艾特一个真人，按照常理，你要的其实只是他最终的结果，如果他全程在群里面说自己的工作过程，挺挤占空间的。
所以实现方式是：

tool: send_msg_to_im_platform(platform_name:str,msg:str)

用户在群里艾特 AI 后，编排器发送一个 steer msg:
```text
(...自从上一次艾特以来的所有消息...)
@bot 请去做xxx
```
边界情况：“自从上一次艾特以来的所有消息”可能会超过上下文长度。这种可能性比较低，先不做。

ai在完成工作后调用send_msg_to_im_platform来发消息给用户。

如果用户想看工作过程，那么应该在 Web 端查看。

# 上下文百分比相关工具
一开始是想搞个工具叫count_token_percentage，后面想着做：
readfile 超过预设的默认的上下文百分比阈值，失败的时候还会顺带告诉这个文件到底是占多少百分比
这个比“先统计一下有多少百分比再去读”要好
