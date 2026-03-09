import type { FormEvent } from 'react'

import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'

type ChatComposerProps = {
  canClearError: boolean
  draft: string
  feedbackText: string
  onClearError: () => void
  onDraftChange: (value: string) => void
  pauseButtonDisabled?: boolean
  pauseButtonLabel?: string
  onSubmit: (event: FormEvent<HTMLFormElement>) => void
}

export function ChatComposer({
  canClearError,
  draft,
  feedbackText,
  onClearError,
  onDraftChange,
  pauseButtonDisabled = true,
  pauseButtonLabel = '暂停（mock）',
  onSubmit,
}: ChatComposerProps) {
  return (
    <form onSubmit={onSubmit}>
      <div className="flex gap-2">
        <Textarea
          className="min-h-[44px] min-w-0 flex-1 resize-y border-zinc-800 bg-zinc-900 text-zinc-100 placeholder:text-zinc-500 focus-visible:ring-zinc-700"
          onChange={(event) => onDraftChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault()
              event.currentTarget.form?.requestSubmit()
            }
          }}
          placeholder="输入消息，回车发送；Shift+Enter 换行。"
          rows={4}
          value={draft}
        />

        <div className="flex shrink-0 flex-col gap-2 sm:flex-row sm:items-end">
          <Button
            className="h-[44px]"
            disabled={pauseButtonDisabled}
            type="button"
            variant="secondary"
          >
            {pauseButtonLabel}
          </Button>
          {canClearError ? (
            <Button
              className="h-[44px]"
              onClick={onClearError}
              type="button"
              variant="secondary"
            >
              清空错误
            </Button>
          ) : null}
          <Button className="h-[44px]" disabled={!draft.trim()} type="submit">
            发送
          </Button>
        </div>
      </div>

      <div className="mt-2 flex items-center justify-between gap-3 text-xs text-zinc-500">
        <span className="min-w-0 truncate">{feedbackText}</span>
        <span className="shrink-0">WebSocket / 流式</span>
      </div>
    </form>
  )
}
