import { useState } from 'react'

import type { AssistantMessageItem } from '@/features/chat/store'

type AssistantTurnBubbleProps = {
  item: AssistantMessageItem
}

export function AssistantTurnBubble({ item }: AssistantTurnBubbleProps) {
  const [reasoningOpen, setReasoningOpen] = useState(true)

  return (
    <article className="flex justify-start">
      <div className="max-w-[85%] rounded-md bg-zinc-900 p-3 ring-1 ring-zinc-800">
        <div className="text-xs font-semibold text-zinc-300">
          assistant{item.streaming ? '（输出中…）' : ''}
        </div>

        {item.reasoning ? (
          <details
            className="mt-2"
            open={reasoningOpen}
            onToggle={(event) => {
              setReasoningOpen(event.currentTarget.open)
            }}
          >
            <summary className="cursor-pointer text-xs text-zinc-400">reasoning</summary>
            <pre className="mt-2 whitespace-pre-wrap text-xs text-zinc-300">{item.reasoning}</pre>
          </details>
        ) : null}

	        <pre className="mt-2 whitespace-pre-wrap text-sm text-zinc-100">
	          {item.text || '…'}
	        </pre>
	      </div>
	    </article>
	  )
}
