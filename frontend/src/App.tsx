import { useCallback, useEffect, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { chatClient } from '@/features/chat/client'
import { AssistantTurnBubble } from '@/features/chat/components/assistant-turn-bubble'
import { ChatComposer } from '@/features/chat/components/chat-composer'
import { ChatSidebar } from '@/features/chat/components/chat-sidebar'
import { ToolCallCard } from '@/features/chat/components/tool-call-card'
import { UserTurnBubble } from '@/features/chat/components/user-turn-bubble'
import { useChatStore } from '@/features/chat/store'

import './App.css'

const SCROLL_BOTTOM_THRESHOLD_PX = 60

function previewText(text: string): string {
  const normalized = text.replace(/\s+/g, ' ').trim()
  return normalized || '等待内容...'
}

function App() {
  const [draft, setDraft] = useState('')
  const [composerError, setComposerError] = useState<string | null>(null)
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false)

  const connectionStatus = useChatStore((state) => state.connectionStatus)
  const errorMessage = useChatStore((state) => state.errorMessage)
  const sessionId = useChatStore((state) => state.sessionId)
  const items = useChatStore((state) => state.items)
  const pendingUserMessages = useChatStore((state) => state.pendingUserMessages)
  const isGenerating = useChatStore((state) => state.isGenerating)
  const clearError = useChatStore((state) => state.clearError)

  const feedbackText =
    composerError || errorMessage || '仅支持流式 WebSocket，会实时展示思维链、正文和工具调用。'

  const scrollRef = useRef<HTMLDivElement>(null)
  const scrollToBottomRafIdRef = useRef<number | null>(null)
  const shouldFollowOutputRef = useRef(true)
  const shouldForceScrollOnNextUpdateRef = useRef(false)
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
      if (
        next.items === prev.items &&
        next.pendingUserMessages === prev.pendingUserMessages &&
        next.sessionId === prev.sessionId
      ) {
        return
      }

      if (next.sessionId !== prev.sessionId) {
        shouldForceScrollOnNextUpdateRef.current = true
        setHasNewContent(false)
        lastContentFingerprintRef.current = ''
      }

      const lastItem = next.items.at(-1)
      const lastPendingMessage = next.pendingUserMessages.at(-1)
      const contentFingerprint = [
        next.sessionId ?? 'no-session',
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

      if (shouldForceScrollOnNextUpdateRef.current) {
        shouldForceScrollOnNextUpdateRef.current = false
        requestScrollToBottom()
        return
      }

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

  const sessionEntries = sessionId
    ? [
        {
          id: sessionId,
          label: previewText(sessionId),
          active: true,
        },
        {
          id: 'mock-more-sessions',
          label: '更多会话功能待接入',
          active: false,
          mock: true,
        },
      ]
    : [
        {
          id: 'waiting-session',
          label: '等待会话建立',
          active: true,
          mock: true,
        },
      ]

  return (
    <div className="flex h-full overflow-hidden bg-zinc-950 text-zinc-100">
      <ChatSidebar
        mobileVisible={mobileSidebarOpen}
        onCloseMobile={() => setMobileSidebarOpen(false)}
        sessionEntries={sessionEntries}
      />

      <main className="flex min-h-0 min-w-0 flex-1 flex-col lg:pl-0">
        <header className="border-b border-zinc-800 px-4 py-3 text-xs text-zinc-400">
          <div className="flex items-center justify-between gap-3">
            <div className="flex min-w-0 items-center gap-2">
              <Button
                className="lg:hidden"
                onClick={() => setMobileSidebarOpen(true)}
                size="sm"
                type="button"
                variant="secondary"
              >
                面板
              </Button>
              <div className="min-w-0 truncate">
                WebSocket：{connectionStatus}
                {sessionId ? ` · 会话：${previewText(sessionId)}` : ' · 等待会话启动'}
                {isGenerating ? ' · 生成中' : ''}
                {errorMessage ? ` · 错误：${errorMessage}` : ''}
              </div>
            </div>
          </div>
        </header>

        <div className="relative min-h-0 flex-1">
          <div ref={scrollRef} className="h-full overflow-auto p-4">
            <div className="mx-auto flex w-full max-w-4xl flex-col gap-4">
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
              ) : null}
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

        <footer className="border-t border-zinc-800 p-4">
          <div className="mx-auto w-full max-w-4xl">
            {pendingUserMessages.length > 0 ? (
              <div className="mb-3 space-y-2">
                {pendingUserMessages.map((message) => (
                  <article key={message.id} className="flex justify-end">
                    <div className="max-w-[85%] rounded-md bg-zinc-800/60 p-3">
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
              pauseButtonDisabled
              pauseButtonLabel="暂停（mock）"
              onSubmit={handleSubmit}
            />
          </div>
        </footer>
      </main>
    </div>
  )
}

export default App
