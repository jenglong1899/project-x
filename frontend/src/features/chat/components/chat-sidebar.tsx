import { MessageSquarePlus, PanelLeftClose } from 'lucide-react'

import { Button } from '@/components/ui/button'

type SessionEntry = {
  id: string
  conversationId: string
  displayName: string
}

type ChatSidebarProps = {
  activeConversationId: string | null
  mobileVisible: boolean
  onCloseMobile: () => void
  sessionEntries: SessionEntry[]
  disableSwitching: boolean
  onNewConversation: () => void
  onSelectConversation: (conversationId: string) => void
}

function sidebarVisibilityClassName(mobileVisible: boolean): string {
  return mobileVisible ? 'translate-x-0' : '-translate-x-full'
}

export function ChatSidebar({
  activeConversationId,
  mobileVisible,
  onCloseMobile,
  sessionEntries,
  disableSwitching,
  onNewConversation,
  onSelectConversation,
}: ChatSidebarProps) {
  return (
    <>
      {mobileVisible ? (
        <button
          aria-label="关闭侧栏"
          className="fixed inset-0 z-30 bg-black/50 lg:hidden"
          onClick={onCloseMobile}
          type="button"
        />
      ) : null}

      <aside
        className={[
          'fixed inset-y-0 left-0 z-40 flex w-72 shrink-0 flex-col border-r border-zinc-800/80 bg-zinc-900 px-3 py-4 transition-transform lg:static lg:translate-x-0',
          sidebarVisibilityClassName(mobileVisible),
        ].join(' ')}
      >
        <div className="flex items-center justify-between gap-3 px-1">
          <div>
            <div className="text-sm font-semibold text-zinc-100">Bionic Claw</div>
            <div className="mt-1 text-xs text-zinc-500">聊天</div>
          </div>
          <Button className="lg:hidden" onClick={onCloseMobile} size="icon-sm" type="button" variant="ghost">
            <PanelLeftClose />
          </Button>
        </div>

        <div className="mt-4">
          <Button
            className="h-10 w-full justify-start rounded-2xl border border-zinc-700/70 bg-zinc-800 text-zinc-100 hover:bg-zinc-700"
            disabled={disableSwitching}
            size="default"
            type="button"
            variant="ghost"
            onClick={onNewConversation}
          >
            <MessageSquarePlus />
            新建对话
          </Button>
        </div>

        <div className="mt-5 px-1 text-xs font-medium text-zinc-500">最近</div>

        <div className="mt-2 min-h-0 flex-1 space-y-1 overflow-y-auto pr-1">
          {sessionEntries.map((entry) => (
            <button
              key={entry.id}
              className={[
                'w-full rounded-xl px-3 py-2.5 text-left text-sm transition-colors',
                entry.conversationId === activeConversationId
                  ? 'bg-zinc-800 text-zinc-100'
                  : 'text-zinc-400 hover:bg-zinc-800/70 hover:text-zinc-100',
              ].join(' ')}
              disabled={disableSwitching}
              onClick={() => onSelectConversation(entry.conversationId)}
              type="button"
            >
              <div className="truncate">{entry.displayName}</div>
            </button>
          ))}
        </div>
      </aside>
    </>
  )
}
