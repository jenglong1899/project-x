import { useState } from 'react'

import type { ToolMessageItem } from '@/features/chat/store'

type ToolCallCardProps = {
  item: ToolMessageItem
}

const TOOL_RESULT_FOLD_THRESHOLD = 200
const TOOL_RESULT_HEAD_CHARS = 100
const TOOL_RESULT_TAIL_CHARS = 100

function toolResultDisplayText(result: string, open: boolean): string {
  if (open) {
    return result
  }

  const head = result.slice(0, TOOL_RESULT_HEAD_CHARS)
  const tail = result.slice(-TOOL_RESULT_TAIL_CHARS)
  return `${head}\n\n…（已折叠，双击展开或折叠）…\n\n${tail}`
}

export function ToolCallCard({ item }: ToolCallCardProps) {
  const [resultOpen, setResultOpen] = useState(false)
  const hasLongResult = Boolean(item.result && item.result.length > TOOL_RESULT_FOLD_THRESHOLD)
  const resultText = item.result
    ? toolResultDisplayText(item.result, hasLongResult ? resultOpen : true)
    : null

  return (
    <article>
      <section className="rounded-md border border-zinc-800 bg-zinc-950 p-3">
        <div className="flex items-center justify-between gap-3">
          <div>
            <div className="text-xs font-semibold uppercase tracking-[0.18em] text-zinc-500">
              Tool
            </div>
            <div className="mt-1 text-sm font-medium text-zinc-200">
              {item.toolName || '等待工具名...'}
            </div>
          </div>
          <div className="text-xs text-zinc-500">
            {item.status === 'completed' ? '调用完成' : '调用中'}
          </div>
        </div>

        <div className="mt-3 space-y-3">
          <div>
            <div className="text-xs text-zinc-500">tool call</div>
            <pre className="mt-2 whitespace-pre-wrap rounded-md bg-zinc-900 p-3 text-xs text-zinc-300">
              {item.args || '等待参数...'}
            </pre>
          </div>

          <div>
            <div className="text-xs text-zinc-500">tool result</div>
            {resultText ? (
              <pre
                className={[
                  'mt-2 whitespace-pre-wrap rounded-md bg-zinc-900 p-3 text-xs text-zinc-200',
                  hasLongResult ? 'cursor-pointer' : '',
                ].join(' ')}
                onDoubleClick={() => {
                  if (!hasLongResult) {
                    return
                  }
                  setResultOpen((value) => !value)
                }}
              >
                {resultText}
              </pre>
            ) : (
              <div className="mt-2 rounded-md bg-zinc-900 p-3 text-xs text-zinc-500">
                等待结果…
              </div>
            )}
          </div>
        </div>
      </section>
    </article>
  )
}
