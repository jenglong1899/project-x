import type { UserMessageItem } from '@/features/chat/store'

type UserTurnBubbleProps = {
  item: UserMessageItem
}

export function UserTurnBubble({ item }: UserTurnBubbleProps) {
  return (
    <article className="flex justify-end">
      <div className="max-w-[85%] rounded-md bg-zinc-800 p-3">
        <div className="text-xs font-semibold text-zinc-300">user</div>
        <pre className="mt-2 whitespace-pre-wrap text-sm text-zinc-100">{item.text}</pre>
      </div>
    </article>
  )
}
