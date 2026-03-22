import type { FormEvent } from 'react'

import { ArrowUp, CircleAlert } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'

type ChatComposerProps = {
  canClearError: boolean
  draft: string
  feedbackText: string
  onClearError: () => void
  onDraftChange: (value: string) => void
  onSubmit: (event: FormEvent<HTMLFormElement>) => void
}

export function ChatComposer({
  canClearError,
  draft,
  feedbackText,
  onClearError,
  onDraftChange,
  onSubmit,
}: ChatComposerProps) {
  return (
    <form className="space-y-3" onSubmit={onSubmit}>
      <div className="rounded-[28px] border border-zinc-800 bg-zinc-900/95 p-3 shadow-[0_12px_40px_rgba(0,0,0,0.28)]">
        <Textarea
          className="min-h-[104px] min-w-0 resize-none border-0 bg-transparent px-1 text-[15px] text-zinc-100 placeholder:text-zinc-500 focus-visible:ring-0 focus-visible:ring-offset-0"
          onChange={(event) => onDraftChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault()
              event.currentTarget.form?.requestSubmit()
            }
          }}
          placeholder="给 Bionic Claw 发送消息"
          rows={3}
          value={draft}
        />

        <div className="mt-3 flex items-center justify-between gap-3">
          <div className="min-w-0 text-xs text-zinc-500">Enter 发送，Shift+Enter 换行</div>
          <Button
            className="size-10 rounded-full bg-zinc-100 text-zinc-950 hover:bg-zinc-200 disabled:bg-zinc-800 disabled:text-zinc-500"
            disabled={!draft.trim()}
            size="icon"
            type="submit"
          >
            <ArrowUp />
          </Button>
        </div>
      </div>

      <div className="flex items-start justify-between gap-3 px-1 text-xs text-zinc-500">
        <div className="min-w-0">
          <div className="flex items-center gap-2 truncate">
            <CircleAlert className="size-3.5 shrink-0" />
            <span className="truncate">{feedbackText}</span>
          </div>
        </div>
        {canClearError ? (
          <Button className="h-auto px-0 text-zinc-400 hover:text-zinc-100" onClick={onClearError} type="button" variant="link">
            清空错误
          </Button>
        ) : null}
      </div>
    </form>
  )
}
