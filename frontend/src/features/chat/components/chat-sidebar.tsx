import { Button } from '@/components/ui/button'

type SessionEntry = {
  id: string
  label: string
  active: boolean
  mock?: boolean
}

type ChatSidebarProps = {
  activeAssistantTurnId: string | null
  connectionStatus: string
  mobileVisible: boolean
  onCloseMobile: () => void
  sessionEntries: SessionEntry[]
}

function sidebarVisibilityClassName(mobileVisible: boolean): string {
  return mobileVisible ? 'translate-x-0' : '-translate-x-full'
}

export function ChatSidebar({
  mobileVisible,
  onCloseMobile,
  sessionEntries,
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
          'fixed inset-y-0 left-0 z-40 flex w-80 shrink-0 flex-col border-r border-zinc-800 bg-zinc-950 p-3 transition-transform lg:static lg:translate-x-0',
          sidebarVisibilityClassName(mobileVisible),
        ].join(' ')}
      >
        <div className="flex items-center justify-between gap-2">
          <div>
            <div className="text-sm font-semibold text-zinc-100">Bionic Claw</div>
          </div>
          <div className="flex items-center gap-2">
            <Button className="lg:hidden" onClick={onCloseMobile} size="sm" type="button" variant="ghost">
              关闭
            </Button>
          </div>
        </div>

        <div className="mt-3 flex gap-2">
          <Button disabled size="sm" type="button" variant="secondary">
            新会话
          </Button>
          <Button disabled size="sm" type="button" variant="secondary">
            刷新列表
          </Button>
        </div>

        <div className="mt-3">
          <div className="text-xs text-zinc-400">工作目录</div>
          <div className="mt-2 flex gap-2">
            <input
              className="h-9 min-w-0 flex-1 rounded-md border border-zinc-800 bg-zinc-950 px-2 text-xs text-zinc-500"
              disabled
              placeholder="该功能暂未接入"
              value=""
              onChange={() => {}}
            />
            <Button disabled size="sm" type="button" variant="secondary">
              切换
            </Button>
          </div>
        </div>

        <div className="mt-3 text-xs text-zinc-400">最近会话</div>

        <div className="mt-2 min-h-0 flex-1 space-y-1 overflow-y-auto pr-1">
          {sessionEntries.map((entry) => (
            <button
              key={entry.id}
              className={[
                'w-full rounded-md px-2 py-2 text-left text-xs',
                entry.active ? 'bg-zinc-900 text-zinc-100' : 'text-zinc-400 hover:bg-zinc-900/80',
                entry.mock ? 'opacity-70' : '',
              ].join(' ')}
              disabled
              type="button"
            >
              <div className="truncate">{entry.label}</div>
              {entry.mock ? <div className="mt-1 text-[11px] text-zinc-500">mock</div> : null}
            </button>
          ))}
        </div>
      </aside>
    </>
  )
}
