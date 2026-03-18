# 总结

asyncio.create_task是用在“单线程交替执行任务（concurrent，并发）”场景，在线程中，在一个任务（通过create_task创建）的代码中调用await比如`await asyncio.sleep(0)`，就表示这个任务主动让出CPU，让这个线程内的其他任务去跑。
如果是
```
if var is none:
  await do_something()
  init_var(var)
```
这种就有共享变量的风险（即使是在单线程），因为await会让当前任务让出cpu，其他任务就可能会在这期间修改var

asyncio.to_thread是多线程
```
async def worker(queue:async.Queue):
  queue.put("worker has finish something")

async def main():
  queue=async.Queue()
  await async.to_thread(worker,queue)
  
asyncio.run(main)
```
这样是不行的，worker和main不在一个线程里面
queue是在main里面创建的，worker想要操控queue，要main把自己的事件循环作为一个类似回调函数的东西传入进worker

```
async def worker(loop:asyncio.AbstractEventLopp,queue:async.Queue):
  loop.call_soon(queue.put("worker has finish something"))

async def main():
  loop=asyncio.get_loop
  queue=async.Queue()
  await async.to_thread(worker,loop,queue)
  
asyncio.run(main)
```

# 后端异步基础

## 你的问题：什么是 `asyncio` 的事件循环？

- 先别把它想得太玄
  - 事件循环可以先粗暴理解成一个“单线程调度器”
  - 它自己通常就在一个线程里跑
  - 它手里维护着很多“还没做完的异步任务”

- 它到底在循环什么
  - 它一直在做类似这样的事：
    - 看看哪些任务现在可以继续执行
    - 让这些任务往下跑一点
    - 某个任务如果遇到 `await`，而等待的 IO 还没完成，就先把它挂起
    - 等 IO 完成后，再把这个任务叫回来继续跑

- 为什么叫“事件”循环
  - 因为它会不断等待各种“事件”发生
  - 比如：
    - socket 收到新数据了
    - 定时器时间到了
    - 某个后台任务完成了
    - 某个 Future/Queue 已经可读了
  - 哪个事件先到，它就恢复对应的协程

- 你可以把它想成一个服务员
  - 一个服务员同时服务很多桌
  - 他不会在一桌旁边傻等“厨房做菜”
  - 而是：
    - 这桌下单了，记一下
    - 厨房还没出菜，先去服务别桌
    - 哪桌菜好了，再回去上菜
  - `await` 就很像“这道菜还没好，我先去处理别的桌”

- 在我们这个项目里，它在调度什么
  - 一个 WebSocket 连接对应的 `websocket_endpoint()` 协程
  - 后台发送协程 `websocket_sender_loop(...)`
  - `ChatSession` 里等待队列事件的逻辑
  - 以及 Starlette/uvicorn 管理的其他连接

## 你的问题：有没有简单的例子？

- 可以，先看一个只有两个协程的最小例子
  - 一个协程每隔 1 秒打印一句话
  - 另一个协程每隔 0.5 秒打印一句话

```python
import asyncio


async def task_a() -> None:
    for i in range(3):
        print(f"A 第 {i} 次")
        await asyncio.sleep(1)


async def task_b() -> None:
    for i in range(6):
        print(f"B 第 {i} 次")
        await asyncio.sleep(0.5)


async def main() -> None:
    a = asyncio.create_task(task_a())
    b = asyncio.create_task(task_b())
    await a
    await b


asyncio.run(main())
```

- 你先不要管 `create_task()` 的全部细节，只看现象
  - `task_a()` 和 `task_b()` 都不是一口气跑完
  - 它们每次执行到 `await asyncio.sleep(...)`，都会先把控制权交回事件循环
  - 事件循环再决定下一个让谁继续跑

- 你可以把执行过程想成这样
  - `task_a()` 打印 `A 第 0 次`
  - 它遇到 `await asyncio.sleep(1)`，先暂停 1 秒
  - 事件循环转去跑 `task_b()`
  - `task_b()` 打印 `B 第 0 次`
  - 它遇到 `await asyncio.sleep(0.5)`，先暂停 0.5 秒
  - 0.5 秒后，`task_b()` 先恢复，打印 `B 第 1 次`
  - 1 秒后，`task_a()` 恢复，打印 `A 第 1 次`

- 这里最关键的一点
  - `await asyncio.sleep(1)` 不是说“整个程序睡 1 秒”
  - 它的真实意思更像是：
    - `task_a` 说：`我这 1 秒没事干，你先去忙别的，1 秒后再叫我`

- 这就是事件循环最核心的工作
  - 某个协程说“我现在要等一下”
  - 事件循环就先去推进别的协程
  - 等它该恢复了，再回来继续推进它

## 如果把上面的例子翻译成人话

- `task_a`
  - 我每隔 1 秒汇报一次进度

- `task_b`
  - 我每隔 0.5 秒汇报一次进度

- 事件循环
  - 谁现在能继续说话，我就让谁说
  - 谁说“我要等一下”，我就先去找别人

## 这个简单例子和同步写法的区别

- 如果你写成普通同步函数
  - 先跑完 A，再跑 B
  - 中间不会穿插

- 如果你写成 `asyncio` 协程
  - A 等待的时候，B 可以继续
  - B 等待的时候，A 也可以继续
  - 所以看起来像“同时进行”

## 对初学者最有用的一句话

- `asyncio` 不是魔法
  - 它不是让代码凭空变快
  - 它只是让“等待期间的空档”不被浪费

## 你的问题：能不能举个和我们项目背景类似的最小例子？

- 可以，先做一个“简化版 WebSocket 会话”
  - 一个协程负责“接收用户消息”
  - 一个协程负责“把后端事件发给前端”
  - 中间靠一个 `asyncio.Queue` 传消息

```python
import asyncio


async def fake_receive_user_messages(session_queue: asyncio.Queue[str]) -> None:
    print("收到用户消息：你好")
    await session_queue.put("session.started")
    await asyncio.sleep(1)
    print("收到用户消息：帮我执行任务")
    await session_queue.put("assistant.delta: 我开始处理了")
    await asyncio.sleep(1)
    await session_queue.put("assistant.delta: 处理完成")
    await session_queue.put("done")


async def fake_sender(session_queue: asyncio.Queue[str]) -> None:
    while True:
        event = await session_queue.get()
        if event == "done":
            print("sender 结束")
            return
        print(f"发给前端: {event}")


async def main() -> None:
    session_queue: asyncio.Queue[str] = asyncio.Queue()

    sender_task = asyncio.create_task(fake_sender(session_queue))
    await fake_receive_user_messages(session_queue)
    await sender_task


asyncio.run(main())
```

- 这个例子里，事件循环在做什么
  - 先跑 `main()`
  - `main()` 里创建了一个后台任务 `sender_task`
  - 于是事件循环现在手里有两件事：
    - `fake_receive_user_messages(...)`
    - `fake_sender(...)`

- 这两个协程是怎么“同时工作”的
  - `fake_sender()` 执行到 `await session_queue.get()` 时，如果队列没数据，就先挂起
  - 事件循环发现它现在没法继续，就去跑 `fake_receive_user_messages()`
  - `fake_receive_user_messages()` 往队列里 `put()` 一个事件
  - 事件循环发现 sender 等的东西到了，就恢复 `fake_sender()`
  - sender 打印完，再次 `await session_queue.get()`，又挂起
  - 整个过程没有创建第二个 Python 执行线程，但两个任务会被交替推进

- 这和我们项目的对应关系
  - `fake_receive_user_messages()` 类似 [web_app.py](/home/bruce/projects/bionic-claw/backend/src/web_app.py) 里的 `websocket_endpoint()`
  - `fake_sender()` 类似 [web_app.py](/home/bruce/projects/bionic-claw/backend/src/web_app.py) 里的 `websocket_sender_loop()`
  - `session_queue` 类似 [chat_session.py](/home/bruce/projects/bionic-claw/backend/src/chat_session.py) 里的 `self._outgoing_queue`

## 你的问题：为什么像项目的例子里，`fake_sender()` 用了 `create_task()`，但 `fake_receive_user_messages()` 没有？

- 先说结论
  - 不是因为 `fake_receive_user_messages()` 不能用 `create_task()`
  - 只是因为那个例子故意把它写成“主流程”
  - `fake_sender()` 被写成“后台长期运行的任务”

- 例子里这段代码：

```python
async def main() -> None:
    session_queue: asyncio.Queue[str] = asyncio.Queue()

    sender_task = asyncio.create_task(fake_sender(session_queue))
    await fake_receive_user_messages(session_queue)
    await sender_task
```

- 这里的思路是
  - `main()` 自己负责推进“接收消息”这条主线
  - sender 是一个一直在旁边待命的后台角色
  - 所以把 sender 挂成后台任务，而接收逻辑直接在主流程里 `await`

- 为什么这样写比较顺手
  - `fake_receive_user_messages()` 是一个“我现在就要做完它”的流程
  - `fake_sender()` 是一个“你在后台一直监听队列，有消息就发”的流程
  - 所以代码读起来会更像：
    - 先开一个后台发送员
    - 然后主线程继续处理接收逻辑

- 如果反过来行不行
  - 也行
  - 你完全可以把两边都写成后台任务

```python
async def main() -> None:
    session_queue: asyncio.Queue[str] = asyncio.Queue()

    receiver_task = asyncio.create_task(fake_receive_user_messages(session_queue))
    sender_task = asyncio.create_task(fake_sender(session_queue))

    await receiver_task
    await sender_task
```

- 这两种写法的本质区别不大
  - 都是在事件循环里并发推进两个协程
  - 区别主要是“谁被当成主流程，谁被当成后台任务”

- 什么时候必须用 `create_task()`
  - 当你希望“这个协程先自己在旁边跑起来，我当前这个函数继续往下走”
  - 在例子里，如果你不把 sender 放到后台，而是直接这样写：

```python
await fake_sender(session_queue)
await fake_receive_user_messages(session_queue)
```

  - 那程序会先卡在 `fake_sender()` 里等队列消息
  - 后面的 `fake_receive_user_messages()` 根本没机会运行
  - 因为没人往队列里放消息，sender 就会一直等下去

- 所以这里真正的关键不是“哪个函数天生该用 `create_task()`”
  - 而是：
    - 哪个协程需要在后台并发运行
    - 哪个协程由当前主流程直接 `await`

- 这和我们项目是一样的
  - [backend/src/web_app.py](/home/bruce/projects/bionic-claw/backend/src/web_app.py) 里当前主流程是 `websocket_endpoint()`
  - 它自己还要继续执行 `receive_text()` 去收前端消息
  - 所以 `websocket_sender_loop(...)` 必须被挂成后台任务
  - 不然主流程就会被 sender 占住，没法继续收消息

## 你可以把事件循环先记成这个工作模型

- 它不是“同时真并行执行很多 Python 代码”
  - 默认不是多线程并行
  - 而是一个线程里，把很多协程在合适的时机切来切去

- 它最擅长的场景
  - 大量 IO 等待
  - 网络连接
  - WebSocket
  - 定时器
  - 流式收发

- 它最不擅长的场景
  - 很重的纯 CPU 计算
  - 长时间不 `await` 的同步阻塞代码
- 这也是为什么我们项目里要用 `await asyncio.to_thread(self._agent.run)`

## 你的问题：`await asyncio.to_thread(self._agent.run)` 是不是就是多开一个线程，别的没区别？

- 先说结论
  - 可以先粗略理解成：`把这个同步函数丢到别的线程去跑`
  - 但不能简单理解成“只是多了个线程，别的完全没区别”

- `to_thread()` 到底做了什么
  - `self._agent.run` 本身是同步函数，不是协程
  - 如果你直接在事件循环线程里调用它，当前 WebSocket 连接会被堵住
  - `asyncio.to_thread(self._agent.run)` 会把这个同步函数放到线程池里的某个工作线程执行
  - 外面的 `await` 则是在事件循环里等待它的结果

- 所以它不是
  - 不是把 `self._agent.run` 变成了“新的异步函数”
  - 不是让 `self._agent.run` 自己进入事件循环里运行

- 它更像是
  - 事件循环说：`这个活太堵了，我自己不干了，派一个线程去做；做完再通知我`

- 和“直接在当前线程同步调用”相比，关键区别有这些
  - 事件循环不会被堵死
  - 这个等待期间，WebSocket 还能继续处理别的协程
  - `self._agent.run` 里的代码此时跑在另一个线程，所以它不能直接安全操作事件循环里的异步对象
  - 这也是为什么项目里还需要 `self._loop.call_soon_threadsafe(...)`

- 在我们项目里，如果不用 `to_thread()` 会怎样
  - `_run_agent_until_idle()` 里如果直接写 `self._agent.run()`
  - 那么只要模型请求、工具执行、流式处理有一点慢
  - 当前事件循环线程就会一直卡在那里
  - `websocket_sender_loop()`、`receive_text()` 这些依赖事件循环推进的逻辑都会受影响

- 还有一个很容易混淆的点
  - `create_task()` 是在事件循环里并发推进另一个协程
  - `to_thread()` 是把同步阻塞函数扔到线程里跑
  - 一个是“协程并发”
  - 一个是“线程池代跑”

- 你现在可以先记这个版本
  - `create_task()`
    - 让另一个协程在同一个事件循环里并发执行
  - `to_thread()`
    - 让一个同步函数去别的线程执行，别堵住事件循环

- 还有哪些“不完全一样”
  - 线程是有额外成本的
  - 跨线程通信会更麻烦
  - 共享数据要更小心
  - 取消 `await to_thread(...)`，通常也不等于强行杀掉那个线程里已经开始执行的函数

## 你的问题：那是不是把 `run()` 改成异步函数就更好？

- 先说结论
  - 只有“端到端都改成真正异步”时，通常才算更好
  - 只是把函数声明从 `def run(...)` 改成 `async def run(...)`，本身没有意义

- 为什么“只改声明”没意义
  - `async def` 不是性能开关
  - 一个函数就算声明成异步，如果里面仍然是同步阻塞代码，而且中间没有真正的 `await`
  - 那它运行起来还是会长时间占着事件循环线程

- 什么时候改成异步是更好的方向
  - 当这个函数的大部分耗时都在“等 IO”
  - 比如：
    - 等模型接口流式返回 chunk
    - 等网络响应
    - 等异步子进程完成
  - 这种场景下，如果底层 API 本身支持异步，那么改成真正 async 往往更自然
  - 因为等待期间它可以 `await`，把事件循环让出去

- 什么时候改成异步也没什么帮助
  - 当耗时主要是纯 CPU 计算
  - 或者底层依赖仍然是同步阻塞 API
  - 例如一个 `async def run()` 里面还是直接调用同步的模型 SDK、同步 `subprocess.run(...)`
  - 那它只是“看起来像异步”，本质上还是会堵住事件循环

- 回到我们项目当前代码
  - [backend/src/chat_session.py](/home/bruce/projects/bionic-claw/backend/src/chat_session.py) 里现在用的是 `await asyncio.to_thread(self._agent.run)`
  - [backend/src/core/agent.py](/home/bruce/projects/bionic-claw/backend/src/core/agent.py) 里的 `run()` 目前是同步函数
  - [backend/src/core/chat.py](/home/bruce/projects/bionic-claw/backend/src/core/chat.py) 里的 `stream()` 也是同步迭代模型流
  - [backend/src/tools/bash.py](/home/bruce/projects/bionic-claw/backend/src/tools/bash.py) 里的工具执行是同步 `subprocess.run(...)`
  - 所以当前这条链路属于“同步阻塞流程”，`to_thread()` 是一个合理的桥接办法

- 如果以后要做成真正更好的异步版本
  - 重点不是先把 `run()` 改名成 `async def`
  - 而是把它依赖的阻塞点逐个换成异步实现
  - 比如：
    - 模型流读取改成异步 client
    - 工具执行改成异步子进程
    - 回调和事件投影保持在事件循环线程内完成
  - 这样最后才会自然变成 `await agent.run()`

- 所以更准确的判断应该是
  - “把 `run()` 改成异步”不一定更好
  - “把整个调用链异步化”如果底层条件允许，通常会比 `to_thread()` 更整洁
  - 但在当前代码状态下，`to_thread()` 比“假异步”更正确

## 你的问题：`loop = asyncio.get_running_loop()` 是什么鬼？

- 你现在卡住的点，其实是“什么叫正在运行的 loop”

- 可以先把 `loop` 想成一个值班中的调度员
  - 它负责安排协程谁先跑、谁先暂停、谁该恢复
  - 但“调度员”这个角色，只有在它真的开始上班时，才叫“正在运行”

- “正在运行的 loop” 不是一个玄学状态
  - 它的意思很朴素：
    - 这个事件循环已经被启动了
    - 现在正接管着当前这段异步代码的调度

- 你可以类比成下面两种状态
  - 一个播放器对象已经创建出来了，但还没点播放
    - 这时它存在，但没在播放
  - 你点了播放，它开始一帧一帧往前走
    - 这时它就在“运行”

- 对 `asyncio` 来说也是一样
  - `loop` 不是只要有这个对象，就算“在运行”
  - 而是它真的已经开始执行那套“等待事件、恢复协程、继续调度”的循环了，才叫“running loop”

- 在最常见的写法里

```python
asyncio.run(main())
```

  - `asyncio.run(...)` 会帮你做两件事
    - 创建一个事件循环
    - 启动它，让它开始运行 `main()`

- 所以当 `main()` 里面执行到这里时

```python
loop = asyncio.get_running_loop()
```

  - 它的意思就是：
    - `把现在这个正在负责调度 main() 的事件循环拿给我`

- 你可以先死记这个判断
  - 在 `async def` 里面，代码已经被 `asyncio.run(...)` 或其他 async 框架真正跑起来时
  - 这时候通常就存在“正在运行的 loop”

- 先看代码位置
  - [backend/src/web_app.py](/home/bruce/projects/bionic-claw/backend/src/web_app.py) 里的 `websocket_endpoint()` 本身就是 `async def`
  - 这说明它不是普通函数，而是运行在 `asyncio` 的事件循环里

- 先看一个最简单的例子

```python
import asyncio


def say_hello() -> None:
    print("hello from loop")


async def main() -> None:
    loop = asyncio.get_running_loop()
    loop.call_soon(say_hello)
    await asyncio.sleep(0.1)


asyncio.run(main())
```

- 这个例子里发生了什么
  - `asyncio.run(main())` 会创建并运行一个事件循环
  - `main()` 跑起来之后，`asyncio.get_running_loop()` 取到的就是这个正在运行的 loop
  - `loop.call_soon(say_hello)` 的意思是：
    - `把 say_hello 安排到这个事件循环里，等会儿执行`
  - `await asyncio.sleep(0.1)` 把控制权交回事件循环
  - 事件循环就有机会执行刚才安排进去的 `say_hello`

- 这个例子想说明的核心只有一个
  - `asyncio.get_running_loop()` 不是在“创建 loop”
  - 它是在“拿到当前正在跑的那个 loop”

- 为什么这个例子里要有 `await asyncio.sleep(0.1)`
  - 因为 `call_soon()` 只是安排“稍后执行”
  - 你得把控制权还给事件循环，它才有机会真的去执行 `say_hello`
  - 这里 `sleep(0.1)` 不是重点，重点只是“让出执行权”

## 你的问题：前面的简单例子里怎么没看到 `loop`？

- 因为很多 `asyncio` 代码根本不需要你手动碰 `loop`
  - 像这些高层 API：
    - `await asyncio.sleep(...)`
    - `asyncio.create_task(...)`
    - `await asyncio.to_thread(...)`
  - 它们内部自己会去使用当前事件循环
  - 只是大多数时候，这个细节被库帮你藏起来了

- 所以前面的简单例子虽然没写 `loop`
  - 但底下仍然有事件循环在工作
  - 只是你没有显式把它拿出来而已

- 你可以先这么理解
  - `asyncio` 的高层写法
    - 平时直接用，不必手动拿 `loop`
  - `loop = asyncio.get_running_loop()`
    - 只有在你确实要调用 loop 的底层能力时，才需要显式拿出来

## 你的问题：什么时候用 `loop`，什么时候用 `create_task()`？

- 先说最实用的结论
  - 大多数业务代码里，优先用 `asyncio.create_task()`
  - 只有在你需要事件循环本身提供的底层能力时，才显式拿 `loop`

- 什么情况下用 `create_task()`
  - 你已经有一个协程对象
  - 你想让它在后台并发运行
  - 同时当前函数还要继续往下执行

```python
task = asyncio.create_task(do_something())
```

- 这句话的重点是
  - `do_something()` 是协程
  - 我想让它现在就开始在后台跑
  - 我自己不想卡在这里等它

- 什么情况下会显式用 `loop`
  - 你要调用事件循环对象的方法
  - 比如：
    - `loop.call_soon(...)`
    - `loop.call_soon_threadsafe(...)`
    - 某些更底层的调度、回调、跨线程投递能力

```python
loop = asyncio.get_running_loop()
loop.call_soon(say_hello)
```

- 这句话的重点是
  - 不是“开一个后台协程”
  - 而是“直接让事件循环帮我安排一个回调”

- 用一句话区分
  - `create_task()`
    - 我要并发跑一个协程
  - `loop.xxx(...)`
    - 我要直接使用事件循环这个调度器的能力

## 在我们项目里分别是哪种情况

- [web_app.py](/home/bruce/projects/bionic-claw/backend/src/web_app.py) 里的：

```python
sender_task = asyncio.create_task(websocket_sender_loop(websocket, session))
```

  - 这是典型的 `create_task()`
  - 因为 `websocket_sender_loop(...)` 是协程
  - 我们想让它后台运行，当前函数继续处理 `receive_text()`

- [chat_session.py](/home/bruce/projects/bionic-claw/backend/src/chat_session.py) 里的：

```python
self._loop.call_soon_threadsafe(self._outgoing_queue.put_nowait, event)
```

  - 这是典型的“显式使用 loop”
  - 因为这里要做的是跨线程把一个操作投递回事件循环
  - 这不是 `create_task()` 的职责

## 你的问题：为什么我们项目这里必须用事件循环的底层调度能力，不能直接用 `create_task()`？

- 先说结论
  - 因为这里要解决的问题不是“后台跑一个协程”
  - 而是“从另一个线程，安全地把一个动作投递回当前事件循环”
  - 这正是 `loop.call_soon_threadsafe(...)` 的职责，不是 `create_task()` 的职责

- 先看我们项目里的真实场景
  - [chat_session.py](/home/bruce/projects/bionic-claw/backend/src/chat_session.py) 里有：

```python
await asyncio.to_thread(self._agent.run)
```

  - 这意味着 `self._agent.run` 跑在线程池里的另一个线程
  - `agent.run()` 过程中会不断通过回调产生日志、正文增量、工具调用结果这些事件
  - 这些回调最后会走到：

```python
def _emit_from_any_thread(self, event: dict[str, Any]) -> None:
    if self._closed:
        return
    self._loop.call_soon_threadsafe(self._outgoing_queue.put_nowait, event)
```

- 这里为什么不能直接 `create_task()`
  - 第一层原因：当前代码跑在另一个线程
    - `asyncio.create_task(...)` 需要在“当前线程已经有正在运行的事件循环”时使用
    - 但这里的回调来自 `to_thread(...)` 开出来的工作线程
    - 那个线程里并没有这个 WebSocket 会话对应的 running loop

- 第二层原因：这里要安排的不是协程
  - `create_task(...)` 只能调度协程对象
  - 但这里要做的事只是：

```python
self._outgoing_queue.put_nowait(event)
```

  - 这是一个普通同步调用，不是协程
  - 所以它本来就不是 `create_task()` 的输入类型

- 第三层原因：这里真正需要的是“跨线程安全”
  - 工作线程不能直接乱碰事件循环线程里的 `asyncio.Queue`
  - 所以要用 `call_soon_threadsafe(...)`
  - 它的意思是：
    - `别在你那个线程里直接操作这个队列`
    - `把这个动作发回事件循环线程，让事件循环自己执行`

- 你可以把这两者的职责强行区分开
  - `create_task()`
    - 我已经身处事件循环线程里
    - 我现在想并发启动一个协程
  - `call_soon_threadsafe()`
    - 我现在不在事件循环线程里
    - 但我想让那个事件循环帮我执行一个动作

- 为什么这里选 `put_nowait` 而不是别的 async 写法
  - 因为这里要投递的是一个很小的动作：往队列里塞一个事件
  - 没必要专门为这件事再包一层协程
  - 直接让 loop 在线程安全的前提下执行这个同步回调，最直接

- 如果你硬要往 `create_task()` 上靠，会变成什么样
  - 你得先构造一个协程，比如：
    - `self._outgoing_queue.put(event)`
  - 但你又不能在工作线程里直接对当前 loop 调 `create_task()`
  - 最后还是得绕回“把调度动作投递回 loop”
  - 也就是类似：
    - `self._loop.call_soon_threadsafe(self._loop.create_task, self._outgoing_queue.put(event))`

- 这样虽然理论上也能做
  - 但更绕
  - 而且我们这里根本不需要一个协程任务，只需要一个线程安全的队列投递动作
  - 所以直接 `call_soon_threadsafe(... put_nowait ...)` 更合适

- 你可以把我们项目里的这个点记成一句话
  - `create_task()` 解决“协程并发”
  - `call_soon_threadsafe()` 解决“跨线程把动作投递回事件循环”

## 对初学者最够用的经验法则

- 如果你脑子里的需求是：
  - `我想让一个 async 函数在后台跑起来`
  - 优先想到 `create_task()`

- 如果你脑子里的需求是：
  - `我需要直接命令事件循环做调度`
  - 才想到先 `get_running_loop()` 再调 `loop.xxx(...)`

- 所以你看到某个例子里没出现 `loop`
  - 不代表没有事件循环
  - 往往只是因为作者只用了高层 API，没必要显式拿出来

- `loop` 是什么
  - `loop` 就是“事件循环对象”
  - 你可以把它理解成一个总调度器：谁该继续跑、谁该先暂停、哪个 IO 完成了、哪个任务该恢复，都是它在安排
  - `asyncio.get_running_loop()` 的意思不是“新建一个 loop”，而是“把当前这个协程正在使用的那个 loop 取出来”

- 这里为什么要拿 `loop`
  - 不是为了当前函数自己用
  - 是为了传给 [backend/src/chat_session.py](/home/bruce/projects/bionic-claw/backend/src/chat_session.py) 里的 `ChatSession(loop=loop)`
  - `ChatSession` 后面要把别的线程里产生的事件，安全地塞回这个 loop 管的异步队列里

- 对应到项目代码
  - `ChatSession` 保存了这个 loop：`self._loop = loop`
  - 真正用到它的地方在 `self._loop.call_soon_threadsafe(...)`
  - 位置见 [backend/src/chat_session.py](/home/bruce/projects/bionic-claw/backend/src/chat_session.py)

## 你的问题：给我一个最简单的 `loop` 使用例子

- 可以，先看一个比项目还小的例子
  - 主协程里有一个 `asyncio.Queue`
  - 另一个工作线程想往这个队列里塞一条消息
  - 这时它不能直接乱碰队列，而是要借助 `loop.call_soon_threadsafe(...)`

```python
import asyncio


def worker(loop: asyncio.AbstractEventLoop, queue: asyncio.Queue[str]) -> None:
    loop.call_soon_threadsafe(queue.put_nowait, "来自工作线程的消息")


async def main() -> None:
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[str] = asyncio.Queue()

    await asyncio.to_thread(worker, loop, queue)

    message = await queue.get()
    print(message)


asyncio.run(main())
```

- 这个例子里，`loop` 到底在干什么
  - `main()` 运行在事件循环线程里
  - `worker(...)` 运行在 `asyncio.to_thread(...)` 开出来的工作线程里
  - 工作线程自己不能直接调度这个事件循环
  - 所以它拿着主线程传进来的 `loop`，说一句：
    - `请你在你自己的线程里，帮我执行 queue.put_nowait(...)`

- 你可以把它类比成“前台和值班室”
  - `main()` 像前台
  - `worker(...)` 像仓库里的员工
  - `queue` 像前台登记簿
  - 仓库员工不能直接冲到前台改登记簿
  - 他要打值班电话给前台：
    - `麻烦你帮我把这条消息记上`
  - 这个“值班电话”就是 `loop.call_soon_threadsafe(...)`

- 你的问题：名字里为什么是 `call_soon`，不是 `call`
  - 因为它的语义不是：
    - `立刻打断事件循环，马上执行这个函数`
  - 而是：
    - `把这个回调安全地登记到事件循环里，让它尽快执行`

- 那它到底什么时候执行
  - 不是“此刻立刻执行”
  - 而是“等事件循环线程重新拿到执行机会时，尽快执行”
  - 更准确地说：
    - 工作线程调用 `loop.call_soon_threadsafe(...)`
    - 事件循环被唤醒，知道有个新回调到了
    - 当前这一步正在跑的代码告一段落后
    - 事件循环在下一轮调度里执行这个回调

- 所以它不是“很久以后”
  - 通常会很快
  - 只是不会粗暴地插进“当前正在执行的那行 Python 代码”中间

- 你可以把它想成“插队到待办列表前面”，但不是“直接抢方向盘”
  - 事件循环会尽快处理它
  - 但仍然要按事件循环自己的调度节奏来

- 用我们这个最小例子来想时序
  - `worker(...)` 在线程里调用 `loop.call_soon_threadsafe(queue.put_nowait, ...)`
  - 这一步只是把“往队列里塞消息”登记给主线程
  - 等主线程的事件循环回到自己的调度点
  - 它才会真的执行 `queue.put_nowait(...)`
  - 然后 `await queue.get()` 才会拿到这条消息

- 为什么这个“不是立刻执行”通常不影响理解
  - 因为我们通常关心的是：
    - `这个动作会不会被安全地交回主事件循环`
  - 而不是精确到 CPU 指令级别的“瞬时立刻”
  - 在业务语义上，你可以把它理解成：
    - `已经成功通知 loop 了，loop 很快会处理`

- 为什么这里必须先拿到 `loop`
  - 因为 `worker(...)` 里没有正在运行的事件循环
  - 它只是一个普通同步函数，而且跑在另一个线程
  - 你如果在里面硬写 `asyncio.get_running_loop()`，通常会报错

- 这个例子和我们项目的对应关系
  - `main()` 对应 WebSocket 所在的 asyncio 主线程
  - `worker(...)` 对应 [backend/src/chat_session.py](/home/bruce/projects/bionic-claw/backend/src/chat_session.py) 里 `asyncio.to_thread(self._agent.run)` 跑出去的那条工作线程
  - `queue` 对应 `ChatSession` 里的 `self._outgoing_queue`
  - `loop.call_soon_threadsafe(...)` 对应 `ChatSession._emit_from_any_thread(...)`

- 你可以先记一个最实用的判断标准
  - 如果你还在 `async def` 里正常写协程，通常不需要显式拿 `loop`
  - 如果你已经跑到“别的线程”或“普通同步回调”里，却还想通知 asyncio 世界，就常常需要先把 `loop` 保存下来

## 你的问题：_emit_from_any_thread用了loop，然后self._projector的初始化用了_emit_from_any_thread，`self._projector` 是不是就一定在另一个线程里被调用？

- 不是
  - `self._projector` 只是一个普通对象
  - 它没有“天然属于某个线程”
  - 关键不在于“它是谁”
  - 关键在于“是谁在调用它的哪个方法”

- 在 [backend/src/chat_session.py](/home/bruce/projects/bionic-claw/backend/src/chat_session.py) 里，确实有一批 projector 回调被注册给了 `Agent`

```python
self._projector = ChatEventProjector(emit=self._emit_from_any_thread)

callbacks = AgentCallbacks(
    on_ai_content_delta=self._projector.on_ai_content_delta,
    on_ai_reasoning_delta=self._projector.on_ai_reasoning_delta,
    on_ai_tool_call_started=self._projector.on_ai_tool_call_started,
    on_ai_tool_call_arguments_delta=self._projector.on_ai_tool_call_arguments_delta,
    on_ai_tool_call_finished=self._projector.on_ai_tool_call_finished,
    on_tool_result=self._projector.on_tool_result,
    on_queued_user_msg_committed=self._on_queued_user_msg_committed,
)
```

- 这批回调后面会传进 `Agent`
  - 然后 `Agent.run()` 再把它们继续传给 `stream(...)` 和 `execute_tool_and_append(...)`

- 真正在线程池工作线程里发生的调用链是这个
  - `ChatSession._run_agent_until_idle()` 里执行：
    - `await asyncio.to_thread(self._agent.run)`
  - 于是 `self._agent.run()` 跑到工作线程
  - `Agent.run()` 里调用 [backend/src/core/agent.py](/home/bruce/projects/bionic-claw/backend/src/core/agent.py#L137) 的 `_safe_stream(...)`
  - `_safe_stream(...)` 又调用 [backend/src/core/chat.py](/home/bruce/projects/bionic-claw/backend/src/core/chat.py#L123) 的 `stream(...)`
  - `stream(...)` 在收到模型流式 chunk 时，会直接调用：
    - `on_ai_content_delta(...)`
    - `on_ai_reasoning_delta(...)`
    - `on_ai_tool_call_started(...)`
    - `on_ai_tool_call_arguments_delta(...)`
    - `on_ai_tool_call_finished(...)`
  - 这些回调此时对应的就是 `self._projector.xxx`
  - 所以这些 projector 方法确实是在工作线程里被调用的

- 对应代码位置
  - 工作线程入口： [backend/src/chat_session.py](/home/bruce/projects/bionic-claw/backend/src/chat_session.py#L376)
  - `Agent.run()` 调 `stream(...)`： [backend/src/core/agent.py](/home/bruce/projects/bionic-claw/backend/src/core/agent.py#L134)
  - `stream(...)` 里真正触发 projector 回调：
    - 内容增量： [backend/src/core/chat.py](/home/bruce/projects/bionic-claw/backend/src/core/chat.py#L195)
    - 思维链增量： [backend/src/core/chat.py](/home/bruce/projects/bionic-claw/backend/src/core/chat.py#L200)
    - 工具开始 / 参数增量 / 工具完成： [backend/src/core/chat.py](/home/bruce/projects/bionic-claw/backend/src/core/chat.py#L207)
  - 工具结果回调：
    - `Agent.run()` 调 `execute_tool_and_append(...)`： [backend/src/core/agent.py](/home/bruce/projects/bionic-claw/backend/src/core/agent.py#L149)
    - 里面调用 `on_tool_result(...)`： [backend/src/core/chat.py](/home/bruce/projects/bionic-claw/backend/src/core/chat.py#L301)
    - 这时对应的也是 `self._projector.on_tool_result`

- 但是，也有 projector 方法不是在工作线程里调用的
  - 比如：
    - `self._projector.on_generation_started()`
    - `self._projector.on_agent_run_completed()`
    - `self._projector.on_generation_completed()`
  - 它们是在 [backend/src/chat_session.py](/home/bruce/projects/bionic-claw/backend/src/chat_session.py#L372) 这个 async 协程里直接调用的
  - 也就是仍然在 asyncio 主线程里

- 还有一个容易漏掉的点
  - `on_queued_user_msg_committed` 注册的不是 `self._projector.on_user_message_committed`
  - 而是 `self._on_queued_user_msg_committed`
  - 它会先在工作线程里被 `Agent._safe_drain_user_message_queue(...)` 调到
  - 然后再由 `self._on_queued_user_msg_committed(...)` 间接调用 `self._projector.on_user_message_committed(...)`
  - 对应代码：
    - 回调触发： [backend/src/core/agent.py](/home/bruce/projects/bionic-claw/backend/src/core/agent.py#L95)
    - 间接转给 projector： [backend/src/chat_session.py](/home/bruce/projects/bionic-claw/backend/src/chat_session.py#L397)

- 所以最准确的结论是
  - `self._projector` 不是“整个对象都运行在另一个线程里”
  - 而是：
    - 一部分方法会被工作线程调用
    - 一部分方法会被 asyncio 主线程调用
  - 正因为调用来源混杂，所以它内部统一把事件交给 `emit=self._emit_from_any_thread`
  - 这样不管这次调用来自哪个线程，最后都能安全回到主事件循环

## 你的问题：为什么还要把“别的线程”的事件塞回 loop？

- 先看关键代码
  - `ChatSession._run_agent_until_idle()` 里有 `await asyncio.to_thread(self._agent.run)`

- 这行的意思
  - `self._agent.run` 是同步函数，会阻塞
  - 如果直接在事件循环线程里调用，整个 WebSocket 服务都会卡住
  - 所以这里把它扔到线程池里跑

- 新问题来了
  - `agent.run()` 跑在线程池线程里
  - 但 `self._outgoing_queue` 是当前 WebSocket 会话所在事件循环上的 `asyncio.Queue`
  - 线程池线程不能直接乱操作这个异步对象

- 所以需要 `loop.call_soon_threadsafe(...)`
  - 含义是：`“麻烦事件循环线程在安全的时候帮我执行这件事”`
  - 这里执行的事是 `self._outgoing_queue.put_nowait(event)`
  - 也就是把 agent 线程产出的事件，安全地投递回 WebSocket 那边

## 你的问题：`sender_task = asyncio.create_task(...)` 是第一次见

- 这行在干什么
  - [backend/src/web_app.py](/home/bruce/projects/bionic-claw/backend/src/web_app.py) 里这句：

```python
sender_task = asyncio.create_task(websocket_sender_loop(websocket, session))
```

- 它的语义
  - `create_task()` 会把一个协程包装成“后台任务”
  - 任务一创建，事件循环就可以在合适的时候调度它运行
  - 当前函数不会卡在这里等它做完

- 这里为什么要单独开一个后台任务
  - `websocket_sender_loop()` 是一个死循环
  - 它不断执行：
    - `event = await session.next_event()`
    - `await websocket.send_json(event)`
  - 也就是说，它专门负责“只要 session 里有新事件，就发给前端”

- 如果不用 `create_task()` 会怎样
  - 如果你直接 `await websocket_sender_loop(...)`
  - 代码会卡在发送循环里，后面的 `receive_text()` 就永远没机会执行
  - 于是这个连接只能发，不能收

- 所以这里其实是在并发做两件事
  - 当前 `websocket_endpoint()` 主协程负责接收前端命令
  - `sender_task` 后台任务负责把后端事件推回前端

## 你的问题：`await session.send_session_started()` 又是什么

- 它不是“开线程”也不是“注册任务”
  - 它只是正常调用一个异步函数，并等待它执行完

- 看实现
  - [backend/src/chat_session.py](/home/bruce/projects/bionic-claw/backend/src/chat_session.py) 里的 `send_session_started()` 本质上只是：

```python
await self._emit(
    {
        "type": "session.started",
        "sessionId": self.session_id,
    }
)
```

- `_emit()` 又做了什么
  - 往 `self._outgoing_queue` 里放一个事件
  - 之后 `sender_task` 那边会从 `next_event()` 里把它取出来，再通过 WebSocket 发给前端

- 所以整段配合起来是这样的
  - 先启动后台发送任务：`sender_task = asyncio.create_task(...)`
  - 再把第一条事件放进队列：`await session.send_session_started()`
  - 后台发送任务立刻就能取到这条事件并发给前端

- 为什么顺序是这个，而不是反过来
  - 因为先把发送任务挂起来，再往队列里塞启动事件，时序更直观
  - 即使反过来，这个项目里通常也不一定错，因为队列会先缓存事件
  - 但现在这种写法更符合“消费者先就位，再投递第一条消息”的直觉

## 把这几行代码连起来看

```python
await websocket.accept()
loop = asyncio.get_running_loop()
session = ChatSession(loop=loop)

sender_task = asyncio.create_task(websocket_sender_loop(websocket, session))
await session.send_session_started()
```

- 可以按这个顺序理解
  - `await websocket.accept()`
    - 先把 WebSocket 握手接起来
  - `loop = asyncio.get_running_loop()`
    - 取出当前连接所在的事件循环
  - `session = ChatSession(loop=loop)`
    - 建一个会话对象，让它以后知道该往哪个 loop 回投事件
  - `sender_task = asyncio.create_task(...)`
    - 启动后台发送循环，专门负责把队列里的事件发给前端
  - `await session.send_session_started()`
    - 往队列里放第一条 `session.started`

## 这个文件里两类常见写法的区别

- `await 某个协程()`
  - 含义：现在就调用，并且等它做完
  - 例子：`await session.send_session_started()`

- `asyncio.create_task(某个协程())`
  - 含义：把它挂成后台任务，让它并发跑
  - 例子：`sender_task = asyncio.create_task(websocket_sender_loop(...))`

- 一个很实用的记忆法
  - `await` 更像“我亲自去办，并等结果”
  - `create_task()` 更像“我把这件长期运行的事交给后台，同时自己继续做别的事”

## 在这个项目里，完整的数据流是什么

- 前端发消息
  - `websocket_endpoint()` 里 `await websocket.receive_text()`
  - 然后 `await session.submit_user_message(...)`

- 后端开始跑 agent
  - `submit_user_message()` 里如果当前没有运行中的任务，就会：
    - `self._runner_task = asyncio.create_task(self._run_agent_until_idle())`

- agent 在线程里执行
  - `_run_agent_until_idle()` 里：
    - `await asyncio.to_thread(self._agent.run)`

- agent 产生流式事件
  - 这些回调最后会走到 `_emit_from_any_thread()`
  - 再通过 `self._loop.call_soon_threadsafe(...)` 回到事件循环线程

- sender_task 发给前端
  - `websocket_sender_loop()` 一直 `await session.next_event()`
  - 拿到事件后 `await websocket.send_json(event)`

## 只抓住这三个点就够了

- `loop`
  - 当前协程所属的事件循环，总调度器

- `await`
  - 调用异步操作，并等待它完成

- `create_task()`
  - 开一个并发的后台任务，让当前协程继续往下执行

## 如果你继续看这块代码，下一步最值得理解的是

- 为什么 `ChatSession` 既有 `_emit()`，又有 `_emit_from_any_thread()`
  - 前者给异步上下文直接用
  - 后者给线程池里的同步代码回投事件用

- 为什么 `submit_user_message()` 和 `_run_agent_until_idle()` 都会用到 `create_task()`
  - 因为这个 WebSocket 会话本质上同时在做“收消息”和“发消息”两件长期任务
