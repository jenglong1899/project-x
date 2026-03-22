import { create } from 'zustand'

import type { ServerEvent } from './protocol'

export type ConnectionStatus = 'idle' | 'connecting' | 'open' | 'closed' | 'error'
export type ToolMessageStatus = 'streaming' | 'completed'

export type UserMessageItem = {
  id: string
  kind: 'user'
  text: string
  userMessageId: string
}

export type AssistantMessageItem = {
  id: string
  kind: 'assistant'
  messageId: string
  reasoning: string
  text: string
  streaming: boolean
}

export type ToolMessageItem = {
  id: string
  kind: 'tool'
  toolCallId: string
  toolName: string
  index: number | null
  args: string
  result: string
  status: ToolMessageStatus
}

export type ChatItem = UserMessageItem | AssistantMessageItem | ToolMessageItem

export type PendingUserMessage = {
  id: string
  text: string
}

type ChatState = {
  connectionStatus: ConnectionStatus
  errorMessage: string | null
  activeConversationId: string | null
  items: ChatItem[]
  pendingUserMessages: PendingUserMessage[]
  isGenerating: boolean
}

type StageUserMessageInput = {
  userMessageId: string
  content: string
}

type ChatActions = {
  setConnectionStatus: (status: ConnectionStatus) => void
  setActiveConversationId: (conversationId: string | null) => void
  loadConversation: (input: { conversationId: string; items: ChatItem[] }) => void
  stageUserMessage: (input: StageUserMessageInput) => void
  applyServerEvent: (event: ServerEvent) => void
  clearError: () => void
  reset: () => void
}

export type ChatStore = ChatState & ChatActions

const initialChatState: ChatState = {
  connectionStatus: 'idle',
  errorMessage: null,
  activeConversationId: null,
  items: [],
  pendingUserMessages: [],
  isGenerating: false,
}

function createAssistantItem(messageId: string): AssistantMessageItem {
  return {
    id: `assistant:${messageId}`,
    kind: 'assistant',
    messageId,
    reasoning: '',
    text: '',
    streaming: true,
  }
}

function createToolItem(toolCallId: string): ToolMessageItem {
  return {
    id: `tool:${toolCallId}`,
    kind: 'tool',
    toolCallId,
    toolName: '',
    index: null,
    args: '',
    result: '',
    status: 'streaming',
  }
}

function createUserItem(userMessageId: string, content: string): UserMessageItem {
  return {
    id: `user:${userMessageId}`,
    kind: 'user',
    text: content,
    userMessageId,
  }
}

function upsertPendingMessage(
  pendingUserMessages: PendingUserMessage[],
  id: string,
  text: string,
): PendingUserMessage[] {
  const existingIndex = pendingUserMessages.findIndex((message) => message.id === id)
  if (existingIndex === -1) {
    return [...pendingUserMessages, { id, text }]
  }

  const nextPendingMessages = [...pendingUserMessages]
  nextPendingMessages[existingIndex] = { id, text }
  return nextPendingMessages
}

function updateItemById<T extends ChatItem>(
  items: ChatItem[],
  itemId: string,
  updater: (item: T) => T,
): ChatItem[] {
  const targetIndex = items.findIndex((item) => item.id === itemId)
  if (targetIndex === -1) {
    return items
  }

  const currentItem = items[targetIndex] as T
  const nextItem = updater(currentItem)
  if (nextItem === currentItem) {
    return items
  }

  const nextItems = [...items]
  nextItems[targetIndex] = nextItem
  return nextItems
}

function ensureAssistantItem(
  items: ChatItem[],
  messageId: string,
): {
  items: ChatItem[]
  itemId: string
} {
  const itemId = `assistant:${messageId}`
  if (items.some((item) => item.id === itemId)) {
    return { items, itemId }
  }

  return {
    items: [...items, createAssistantItem(messageId)],
    itemId,
  }
}

function ensureToolItem(
  items: ChatItem[],
  toolCallId: string,
): {
  items: ChatItem[]
  itemId: string
} {
  const itemId = `tool:${toolCallId}`
  if (items.some((item) => item.id === itemId)) {
    return { items, itemId }
  }

  return {
    items: [...items, createToolItem(toolCallId)],
    itemId,
  }
}

function upsertUserItem(
  items: ChatItem[],
  userMessageId: string,
  content: string,
): ChatItem[] {
  const nextItem = createUserItem(userMessageId, content)
  const existingIndex = items.findIndex((item) => item.id === nextItem.id)
  if (existingIndex === -1) {
    return [...items, nextItem]
  }

  return updateItemById<UserMessageItem>(items, nextItem.id, () => nextItem)
}

function reduceServerEvent(state: ChatState, event: ServerEvent): Partial<ChatState> {
  switch (event.type) {
    case 'generation.started':
      return {
        isGenerating: true,
      }

    case 'generation.completed':
      return {
        isGenerating: false,
      }

    case 'conversation.persisted':
      return {
        activeConversationId: event.conversationId,
      }

    case 'user.message.committed':
      return {
        items: upsertUserItem(state.items, event.userMessageId, event.content),
        pendingUserMessages: state.pendingUserMessages.filter(
          (message) => message.id !== event.userMessageId,
        ),
      }

    case 'assistant.message.started': {
      const ensuredAssistant = ensureAssistantItem(state.items, event.messageId)
      return {
        items: ensuredAssistant.items,
      }
    }

    case 'assistant.message.delta': {
      const ensuredAssistant = ensureAssistantItem(state.items, event.messageId)
      return {
        items: updateItemById<AssistantMessageItem>(
          ensuredAssistant.items,
          ensuredAssistant.itemId,
          (item) =>
            event.channel === 'reasoning'
              ? {
                  ...item,
                  reasoning: item.reasoning + event.delta,
                  streaming: true,
                }
              : {
                  ...item,
                  text: item.text + event.delta,
                  streaming: true,
                },
        ),
      }
    }

    case 'assistant.message.completed':
      return {
        items: updateItemById<AssistantMessageItem>(state.items, `assistant:${event.messageId}`, (item) =>
          item.streaming ? { ...item, streaming: false } : item,
        ),
      }

    case 'tool.started': {
      const ensuredTool = ensureToolItem(state.items, event.toolCallId)
      return {
        items: updateItemById<ToolMessageItem>(ensuredTool.items, ensuredTool.itemId, (item) => ({
          ...item,
          toolName: event.toolName,
          index: event.index,
          status: 'streaming',
        })),
      }
    }

    case 'tool.arguments.delta': {
      const ensuredTool = ensureToolItem(state.items, event.toolCallId)
      return {
        items: updateItemById<ToolMessageItem>(ensuredTool.items, ensuredTool.itemId, (item) => ({
          ...item,
          toolName: event.toolName,
          args: item.args + event.argumentsDelta,
          status: 'streaming',
        })),
      }
    }

    case 'tool.completed': {
      const ensuredTool = ensureToolItem(state.items, event.toolCallId)
      return {
        items: updateItemById<ToolMessageItem>(ensuredTool.items, ensuredTool.itemId, (item) => ({
          ...item,
          toolName: event.toolName,
          args: event.arguments,
          status: 'completed',
        })),
      }
    }

    case 'tool.result': {
      const ensuredTool = ensureToolItem(state.items, event.toolCallId)
      return {
        items: updateItemById<ToolMessageItem>(ensuredTool.items, ensuredTool.itemId, (item) => ({
          ...item,
          result: event.result,
        })),
      }
    }

    case 'error':
      return {
        connectionStatus: 'error',
        errorMessage: `${event.code}: ${event.message}`,
        isGenerating: false,
      }
  }
}

export const useChatStore = create<ChatStore>()((set) => ({
  ...initialChatState,
  setConnectionStatus: (status) => {
    set({
      connectionStatus: status,
    })
  },
  setActiveConversationId: (conversationId) => {
    set({
      activeConversationId: conversationId,
    })
  },
  loadConversation: ({ conversationId, items }) => {
    set({
      activeConversationId: conversationId,
      items,
      pendingUserMessages: [],
      isGenerating: false,
      errorMessage: null,
    })
  },
  stageUserMessage: ({ userMessageId, content }) => {
    set((state) => ({
      pendingUserMessages: upsertPendingMessage(state.pendingUserMessages, userMessageId, content),
    }))
  },
  applyServerEvent: (event) => {
    set((state) => reduceServerEvent(state, event))
  },
  clearError: () => {
    set({
      errorMessage: null,
    })
  },
  reset: () => {
    set(initialChatState)
  },
}))
