export function EmptyChatState() {
  return (
    <section className="rounded-md border border-dashed border-zinc-800 bg-zinc-900/40 p-6 text-left">
      <div className="text-lg font-semibold text-zinc-100">开始一段对话</div>
      <p className="mt-2 text-sm leading-7 text-zinc-400">
        左侧是工作台信息，右侧按时间线展示 user、assistant 和 tool 卡片。连接建立后，正文、思维链和工具参数都会持续流式追加。
      </p>
    </section>
  )
}
