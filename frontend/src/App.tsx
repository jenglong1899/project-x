import type { ComponentProps } from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { CircleAlert, LoaderCircle } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { chatClient } from '@/features/chat/client'
import { AssistantTurnBubble } from '@/features/chat/components/assistant-turn-bubble'
import { ChatComposer } from '@/features/chat/components/chat-composer'
import { ToolCallCard } from '@/features/chat/components/tool-call-card'
import { UserTurnBubble } from '@/features/chat/components/user-turn-bubble'
import { useChatStore } from '@/features/chat/store'

import './App.css'

const SCROLL_BOTTOM_THRESHOLD_PX = 60
type FormSubmitHandler = NonNullable<ComponentProps<'form'>['onSubmit']>

function App() {
  const [draft, setDraft] = useState('')
  const [composerError, setComposerError] = useState<string | null>(null)

  const connectionStatus = useChatStore((state) => state.connectionStatus)
  const errorMessage = useChatStore((state) => state.errorMessage)
  const items = useChatStore((state) => state.items)
  const pendingUserMessages = useChatStore((state) => state.pendingUserMessages)
  const isGenerating = useChatStore((state) => state.isGenerating)
  const pauseRequested = useChatStore((state) => state.pauseRequested)
  const isPaused = useChatStore((state) => state.isPaused)

  const feedbackText =
    composerError || '当 AI 用非常自信的语气回答的时候，也不代表其说的话是真的，请核查。'
  const connectionIssueText =
    connectionStatus === 'error'
      ? errorMessage || 'WebSocket 连接发生错误。'
      : connectionStatus === 'closed'
        ? 'WebSocket 连接已断开。'
        : null

  // 只看最后一条用户消息之后的 assistant/tool 内容，避免历史消息误判成当前输出；
  // 第一个 chunk 或工具事件抵达后，占位就可以消失，不必等整轮结束。
  let lastUserItemIndex = -1
  for (let index = items.length - 1; index >= 0; index -= 1) {
    if (items[index].kind === 'user') {
      lastUserItemIndex = index
      break
    }
  }
  const hasCurrentAssistantOutput =
    lastUserItemIndex !== -1 &&
    items.slice(lastUserItemIndex + 1).some((item) => {
      if (item.kind === 'assistant') {
        return Boolean(item.reasoning || item.text)
      }

      if (item.kind === 'tool') {
        return Boolean(item.toolName || item.args || item.result)
      }

      return false
    })
  const shouldShowGeneratingPlaceholder = isGenerating && !hasCurrentAssistantOutput

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

  const handleSubmit: FormSubmitHandler = (event) => {
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

  return (
    <div className="flex h-full overflow-hidden bg-zinc-950 text-zinc-100">
      <main className="flex flex-col min-h-0 min-w-0 flex-1 bg-zinc-950">
        <div className="relative min-h-0 flex-1">
          {connectionIssueText ? (
            <div className="pointer-events-none absolute right-4 top-4 z-20 max-w-[min(22rem,calc(100%-2rem))]">
              <div
                aria-live="polite"
                className="pointer-events-auto flex items-start gap-3 rounded-lg border border-red-900/70 bg-red-950/95 px-4 py-3 text-sm text-red-100 shadow-2xl shadow-black/40"
                role="status"
              >
                <CircleAlert className="mt-0.5 size-4 shrink-0 text-red-300" />
                <div className="min-w-0">
                  <div className="font-medium">连接异常</div>
                  <div className="mt-1 wrap-break-word text-red-100/80">{connectionIssueText}</div>
                </div>
              </div>
            </div>
          ) : null}

          <div ref={scrollRef} className="h-full overflow-auto px-4 py-6">
            <div className="flex flex-col gap-6 mx-auto w-full max-w-3xl">
              {items.length > 0 ? (
                <>
                  {items.map((item) => {
                    if (item.kind === 'user') {
                      return <UserTurnBubble key={item.id} item={item} />
                    }

                    if (item.kind === 'assistant') {
                      return <AssistantTurnBubble key={item.id} item={item} />
                    }

                    return <ToolCallCard key={item.id} item={item} />
                  })}

                  {shouldShowGeneratingPlaceholder ? (
                    <article className="flex justify-start">
                      <div className="flex max-w-[85%] items-center gap-3 rounded-md bg-zinc-900 p-3 text-sm text-zinc-400 ring-1 ring-zinc-800">
                        <LoaderCircle className="size-4 animate-spin text-zinc-500" />
                        <span>正在等待 AI 响应…</span>
                      </div>
                    </article>
                  ) : null}
                </>
              ) : (
                <section className="flex min-h-full flex-1 items-center justify-center py-16">
                  <div className="max-w-xl text-center">
                    <div className="text-3xl font-medium tracking-tight text-zinc-100 sm:text-4xl">
                      今天想聊点什么？
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
              draft={draft}
              feedbackText={feedbackText}
              isGenerating={isGenerating}
              isPaused={isPaused}
              onDraftChange={setDraft}
              onPauseToggle={() => {
                if (pauseRequested) {
                  return
                }
                if (!isGenerating && !isPaused) {
                  return
                }
                if (isPaused) {
                  chatClient.resume()
                  return
                }
                chatClient.requestPause()
              }}
              pauseRequested={pauseRequested}
              onSubmit={handleSubmit}
            />

            <div className="mt-2 flex items-center gap-3">
              <div className="text-xs text-zinc-400">
                {isPaused ? '已暂停：发送消息会自动恢复运行。' : pauseRequested ? '等待暂停生效…' : ''}
              </div>
            </div>
          </div>
        </footer>
      </main>
    </div>
  )
}

export default App
