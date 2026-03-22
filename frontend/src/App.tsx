import { useCallback, useEffect, useRef, useState } from 'react'

import { PanelLeft } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { chatClient } from '@/features/chat/client'
import { AssistantTurnBubble } from '@/features/chat/components/assistant-turn-bubble'
import { ChatComposer } from '@/features/chat/components/chat-composer'
import { ChatSidebar } from '@/features/chat/components/chat-sidebar'
import { ToolCallCard } from '@/features/chat/components/tool-call-card'
import { UserTurnBubble } from '@/features/chat/components/user-turn-bubble'
import {
  buildChatItemsFromConversationHistory,
  fetchConversationDetail,
  fetchConversationList,
} from '@/features/chat/conversations'
import { useChatStore } from '@/features/chat/store'

import './App.css'

const SCROLL_BOTTOM_THRESHOLD_PX = 60

type SessionEntry = {
  id: string
  conversationId: string
  displayName: string
}

function upsertSessionEntry(entries: SessionEntry[], entry: SessionEntry): SessionEntry[] {
  const existingIndex = entries.findIndex((item) => item.conversationId === entry.conversationId)
  if (existingIndex === -1) {
    return [entry, ...entries]
  }

  const nextEntries = [...entries]
  const currentEntry = nextEntries[existingIndex]
  nextEntries.splice(existingIndex, 1)
  nextEntries.unshift({
    ...currentEntry,
    ...entry,
  })
  return nextEntries
}

function App() {
  const [draft, setDraft] = useState('')
  const [composerError, setComposerError] = useState<string | null>(null)
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false)

  const connectionStatus = useChatStore((state) => state.connectionStatus)
  const errorMessage = useChatStore((state) => state.errorMessage)
  const items = useChatStore((state) => state.items)
  const pendingUserMessages = useChatStore((state) => state.pendingUserMessages)
  const isGenerating = useChatStore((state) => state.isGenerating)
  const activeConversationId = useChatStore((state) => state.activeConversationId)
  const persistedConversation = useChatStore((state) => state.persistedConversation)
  const clearError = useChatStore((state) => state.clearError)
  const loadConversation = useChatStore((state) => state.loadConversation)
  const resetChatStore = useChatStore((state) => state.reset)

  const feedbackText =
    composerError || errorMessage || '当 AI 用非常自信的语气回答的时候，也不代表其说的话是真的，请核查。'

  const scrollRef = useRef<HTMLDivElement>(null)
  const scrollToBottomRafIdRef = useRef<number | null>(null)
  const shouldFollowOutputRef = useRef(true)
  const lastContentFingerprintRef = useRef('')
  const [isAtBottom, setIsAtBottom] = useState(true)
  const [hasNewContent, setHasNewContent] = useState(false)

  const syncAtBottomState = useCallback((element: HTMLDivElement) => {
    const distance = element.scrollHeight - element.scrollTop - element.clientHeight
    const atBottom = distance <= SCROLL_BOTTOM_THRESHOLD_PX
    shouldFollowOutputRef.current = atBottom
    setIsAtBottom(atBottom)
    if (atBottom) {
      setHasNewContent(false)
    }
  }, [])

  const requestScrollToBottom = useCallback(() => {
    if (scrollToBottomRafIdRef.current !== null) {
      return
    }

    scrollToBottomRafIdRef.current = window.requestAnimationFrame(() => {
      scrollToBottomRafIdRef.current = null
      const element = scrollRef.current
      if (!element) {
        return
      }
      element.scrollTop = element.scrollHeight
      syncAtBottomState(element)
    })
  }, [syncAtBottomState])

  useEffect(() => {
    chatClient.connect()
    return () => {
      chatClient.disconnect()
    }
  }, [])

  const [sessionEntries, setSessionEntries] = useState<SessionEntry[]>([])
  const activeConversationTitle =
    sessionEntries.find((entry) => entry.conversationId === activeConversationId)?.displayName ?? '新对话'

  const loadSessionList = useCallback(async () => {
    try {
      const list = await fetchConversationList()
      setSessionEntries(
        list.map((item) => ({
          id: item.conversationId,
          conversationId: item.conversationId,
          displayName: item.displayName || item.conversationId,
        })),
      )
    } catch (error) {
      setComposerError(error instanceof Error ? error.message : '加载会话列表失败。')
    }
  }, [])

  useEffect(() => {
    void loadSessionList()
  }, [loadSessionList])

  useEffect(() => {
    if (!persistedConversation) {
      return
    }

    setSessionEntries((currentEntries) =>
      upsertSessionEntry(currentEntries, {
        id: persistedConversation.conversationId,
        conversationId: persistedConversation.conversationId,
        displayName: persistedConversation.displayName || persistedConversation.conversationId,
      }),
    )
  }, [persistedConversation])

  useEffect(() => {
    const element = scrollRef.current
    if (!element) {
      return
    }

    const onScroll = () => syncAtBottomState(element)
    element.addEventListener('scroll', onScroll, { passive: true })
    syncAtBottomState(element)

    return () => {
      element.removeEventListener('scroll', onScroll)
    }
  }, [syncAtBottomState])

  useEffect(() => {
    return () => {
      const rafId = scrollToBottomRafIdRef.current
      if (rafId !== null) {
        window.cancelAnimationFrame(rafId)
      }
      scrollToBottomRafIdRef.current = null
    }
  }, [])

  useEffect(() => {
    const unsubscribe = useChatStore.subscribe((next, prev) => {
      if (next.items === prev.items && next.pendingUserMessages === prev.pendingUserMessages) {
        return
      }

      const lastItem = next.items.at(-1)
      const lastPendingMessage = next.pendingUserMessages.at(-1)
      const contentFingerprint = [
        next.items.length,
        lastItem?.id ?? 'no-item',
        lastItem?.kind ?? 'none',
        lastItem?.kind === 'assistant' ? lastItem.reasoning.length : '',
        lastItem?.kind === 'assistant' ? lastItem.text.length : '',
        lastItem?.kind === 'assistant' ? String(lastItem.streaming) : '',
        lastItem?.kind === 'tool' ? lastItem.args.length : '',
        lastItem?.kind === 'tool' ? lastItem.result.length : '',
        next.pendingUserMessages.length,
        lastPendingMessage?.id ?? 'no-pending',
        lastPendingMessage?.text.length ?? 0,
      ].join(':')

      if (contentFingerprint === lastContentFingerprintRef.current) {
        return
      }
      lastContentFingerprintRef.current = contentFingerprint

      if (shouldFollowOutputRef.current) {
        requestScrollToBottom()
        return
      }

      setHasNewContent(true)
    })

    return unsubscribe
  }, [requestScrollToBottom])

  function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setComposerError(null)

    try {
      const userMessageId = chatClient.sendUserMessage(draft)
      if (!userMessageId) {
        return
      }
      setDraft('')
    } catch (error) {
      setComposerError(error instanceof Error ? error.message : '发送消息失败。')
    }
  }

  const disableSwitching = pendingUserMessages.length > 0 || isGenerating

  const handleNewConversation = useCallback(() => {
    if (disableSwitching) {
      return
    }
    setComposerError(null)
    resetChatStore()
    chatClient.disconnect()
    chatClient.connect()
  }, [disableSwitching, resetChatStore])

  const handleSelectConversation = useCallback(
    async (conversationId: string) => {
      if (disableSwitching) {
        return
      }
      setComposerError(null)
      const detail = await fetchConversationDetail(conversationId)
      const nextItems = buildChatItemsFromConversationHistory(detail.messages)
      loadConversation({ conversationId: detail.conversationId, items: nextItems })
      chatClient.disconnect()
      chatClient.connect({ conversationId: detail.conversationId })
      setMobileSidebarOpen(false)
    },
    [disableSwitching, loadConversation],
  )

  return (
    <div className="flex h-full overflow-hidden bg-zinc-950 text-zinc-100">
      <ChatSidebar
        activeConversationId={activeConversationId}
        mobileVisible={mobileSidebarOpen}
        onCloseMobile={() => setMobileSidebarOpen(false)}
        sessionEntries={sessionEntries}
        disableSwitching={disableSwitching}
        onNewConversation={handleNewConversation}
        onSelectConversation={(conversationId) => void handleSelectConversation(conversationId)}
      />

      <main className="flex min-h-0 min-w-0 flex-1 flex-col bg-zinc-950">
        <header className="border-b border-zinc-800/80 px-4 py-3">
          <div className="flex items-center justify-between gap-4">
            <div className="flex min-w-0 items-center gap-2">
              <Button
                className="rounded-full lg:hidden"
                onClick={() => setMobileSidebarOpen(true)}
                size="icon-sm"
                type="button"
                variant="ghost"
              >
                <PanelLeft />
              </Button>
              <div className="min-w-0">
                <div className="truncate text-sm font-medium text-zinc-100">{activeConversationTitle}</div>
                <div className="mt-1 text-xs text-zinc-500">
                  {isGenerating ? '生成中' : '已连接'}
                </div>
              </div>
            </div>
            <div className="hidden text-xs text-zinc-500 sm:block">
              WebSocket {connectionStatus}
            </div>
          </div>
        </header>

        <div className="relative min-h-0 flex-1">
          <div ref={scrollRef} className="h-full overflow-auto px-4 py-6">
            <div className="mx-auto flex w-full max-w-3xl flex-col gap-6">
              {items.length > 0 ? (
                items.map((item) => {
                  if (item.kind === 'user') {
                    return <UserTurnBubble key={item.id} item={item} />
                  }

                  if (item.kind === 'assistant') {
                    return <AssistantTurnBubble key={item.id} item={item} />
                  }

                  return <ToolCallCard key={item.id} item={item} />
                })
              ) : (
                <section className="flex min-h-full flex-1 items-center justify-center py-16">
                  <div className="max-w-xl text-center">
                    <div className="text-3xl font-medium tracking-tight text-zinc-100 sm:text-4xl">
                      今天想聊点什么？
                    </div>
                    <div className="mt-4 text-sm leading-6 text-zinc-500">
                      可以直接继续历史会话，也可以新建一个对话开始新的任务。
                    </div>
                  </div>
                </section>
              )}
            </div>
          </div>

          {!isAtBottom && hasNewContent ? (
            <div className="pointer-events-none absolute bottom-4 right-4">
              <Button
                className="pointer-events-auto shadow-lg"
                onClick={() => {
                  setHasNewContent(false)
                  requestScrollToBottom()
                }}
                size="sm"
                type="button"
                variant="secondary"
              >
                跳到最新
              </Button>
            </div>
          ) : null}
        </div>

        <footer className="px-4 pb-5 pt-3">
          <div className="mx-auto w-full max-w-3xl">
            {pendingUserMessages.length > 0 ? (
              <div className="mb-3 space-y-2">
                {pendingUserMessages.map((message) => (
                  <article key={message.id} className="flex justify-end">
                    <div className="max-w-[85%] rounded-3xl bg-zinc-800/80 px-4 py-3">
                      <div className="flex items-center justify-between gap-3 text-xs font-semibold text-zinc-300">
                        <div>user（待发送）</div>
                        <div
                          aria-label="等待中"
                          className="h-3 w-3 animate-spin rounded-full border-2 border-zinc-500 border-t-transparent"
                          role="img"
                        />
                      </div>
                      <pre className="mt-2 whitespace-pre-wrap text-sm text-zinc-100">
                        {message.text}
                      </pre>
                    </div>
                  </article>
                ))}
              </div>
            ) : null}

            <ChatComposer
              canClearError={Boolean(errorMessage)}
              draft={draft}
              feedbackText={feedbackText}
              onClearError={clearError}
              onDraftChange={setDraft}
              onSubmit={handleSubmit}
            />
          </div>
        </footer>
      </main>
    </div>
  )
}

export default App
