import { create } from 'zustand'

import type { ServerEvent } from './protocol'

export type ConnectionStatus = 'idle' | 'connecting' | 'open' | 'closed' | 'error'
export type ToolMessageStatus = 'streaming' | 'completed'

export type UserMessageItem = {
  id: string
  kind: 'user'
  text: string
  userTurnId: string
}

export type AssistantMessageItem = {
  id: string
  kind: 'assistant'
  assistantTurnId: string
  reasoning: string
  text: string
  streaming: boolean
}

export type ToolMessageItem = {
  id: string
  kind: 'tool'
  assistantTurnId: string
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
  sessionId: string | null
  connectionStatus: ConnectionStatus
  errorMessage: string | null
  items: ChatItem[]
  pendingUserMessages: PendingUserMessage[]
  activeAssistantTurnId: string | null
  activeAssistantItemIdByTurnId: Record<string, string>
  toolItemIdByCallId: Record<string, string>
}

type StageUserTurnInput = {
  userTurnId: string
  content: string
}

type ChatActions = {
  setConnectionStatus: (status: ConnectionStatus) => void
  stageUserTurn: (input: StageUserTurnInput) => void
  applyServerEvent: (event: ServerEvent) => void
  clearError: () => void
  reset: () => void
}

export type ChatStore = ChatState & ChatActions

const initialChatState: ChatState = {
  sessionId: null,
  connectionStatus: 'idle',
  errorMessage: null,
  items: [],
  pendingUserMessages: [],
  activeAssistantTurnId: null,
  activeAssistantItemIdByTurnId: {},
  toolItemIdByCallId: {},
}

function createAssistantItem(assistantTurnId: string): AssistantMessageItem {
  return {
    id: `assistant:${crypto.randomUUID()}`,
    kind: 'assistant',
    assistantTurnId,
    reasoning: '',
    text: '',
    streaming: true,
  }
}

function createToolItem(
  assistantTurnId: string,
  toolCallId: string,
  toolName = '',
  index: number | null = null,
): ToolMessageItem {
  return {
    id: `tool:${toolCallId}`,
    kind: 'tool',
    assistantTurnId,
    toolCallId,
    toolName,
    index,
    args: '',
    result: '',
    status: 'streaming',
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

function closeAssistantSegment(state: ChatState, assistantTurnId: string): Partial<ChatState> {
  const assistantItemId = state.activeAssistantItemIdByTurnId[assistantTurnId]
  if (!assistantItemId) {
    return {}
  }

  const restActiveAssistantItemIdByTurnId = { ...state.activeAssistantItemIdByTurnId }
  delete restActiveAssistantItemIdByTurnId[assistantTurnId]

  return {
    items: updateItemById<AssistantMessageItem>(state.items, assistantItemId, (item) =>
      item.streaming ? { ...item, streaming: false } : item,
    ),
    activeAssistantItemIdByTurnId: restActiveAssistantItemIdByTurnId,
  }
}

function ensureAssistantSegment(
  state: ChatState,
  assistantTurnId: string,
): {
  items: ChatItem[]
  activeAssistantItemIdByTurnId: Record<string, string>
  assistantItemId: string
} {
  const existingAssistantItemId = state.activeAssistantItemIdByTurnId[assistantTurnId]
  if (existingAssistantItemId) {
    return {
      items: state.items,
      activeAssistantItemIdByTurnId: state.activeAssistantItemIdByTurnId,
      assistantItemId: existingAssistantItemId,
    }
  }

  const assistantItem = createAssistantItem(assistantTurnId)
  return {
    items: [...state.items, assistantItem],
    activeAssistantItemIdByTurnId: {
      ...state.activeAssistantItemIdByTurnId,
      [assistantTurnId]: assistantItem.id,
    },
    assistantItemId: assistantItem.id,
  }
}

function ensureToolItem(
  state: ChatState,
  assistantTurnId: string,
  toolCallId: string,
): {
  items: ChatItem[]
  toolItemIdByCallId: Record<string, string>
  toolItemId: string
} {
  const existingToolItemId = state.toolItemIdByCallId[toolCallId]
  if (existingToolItemId) {
    return {
      items: state.items,
      toolItemIdByCallId: state.toolItemIdByCallId,
      toolItemId: existingToolItemId,
    }
  }

  const toolItem = createToolItem(assistantTurnId, toolCallId)
  return {
    items: [...state.items, toolItem],
    toolItemIdByCallId: {
      ...state.toolItemIdByCallId,
      [toolCallId]: toolItem.id,
    },
    toolItemId: toolItem.id,
  }
}

function reduceServerEvent(state: ChatState, event: ServerEvent): Partial<ChatState> {
  switch (event.type) {
    case 'session.started':
      return {
        sessionId: event.sessionId,
      }

    case 'user.turn.enqueued':
      return {
        pendingUserMessages: upsertPendingMessage(
          state.pendingUserMessages,
          event.userTurnId,
          state.pendingUserMessages.find((message) => message.id === event.userTurnId)?.text ?? '',
        ),
      }

    case 'user.turn.committed': {
      const pendingMessage = state.pendingUserMessages.find(
        (message) => message.id === event.userTurnId,
      )
      const nextPendingUserMessages = state.pendingUserMessages.filter(
        (message) => message.id !== event.userTurnId,
      )
      const nextUserItem: UserMessageItem = {
        id: `user:${event.userTurnId}`,
        kind: 'user',
        text: pendingMessage?.text ?? '',
        userTurnId: event.userTurnId,
      }
      const existingItemIndex = state.items.findIndex((item) => item.id === nextUserItem.id)
      const nextItems =
        existingItemIndex === -1
          ? [...state.items, nextUserItem]
          : updateItemById<UserMessageItem>(state.items, nextUserItem.id, () => nextUserItem)

      return {
        items: nextItems,
        pendingUserMessages: nextPendingUserMessages,
      }
    }

    case 'assistant.turn.started':
      return {
        activeAssistantTurnId: event.assistantTurnId,
      }

    case 'assistant.reasoning.delta': {
      const ensuredAssistantSegment = ensureAssistantSegment(state, event.assistantTurnId)
      return {
        items: updateItemById<AssistantMessageItem>(
          ensuredAssistantSegment.items,
          ensuredAssistantSegment.assistantItemId,
          (item) => ({
            ...item,
            reasoning: item.reasoning + event.delta,
            streaming: true,
          }),
        ),
        activeAssistantItemIdByTurnId: ensuredAssistantSegment.activeAssistantItemIdByTurnId,
        activeAssistantTurnId: event.assistantTurnId,
      }
    }

    case 'assistant.content.delta': {
      const ensuredAssistantSegment = ensureAssistantSegment(state, event.assistantTurnId)
      return {
        items: updateItemById<AssistantMessageItem>(
          ensuredAssistantSegment.items,
          ensuredAssistantSegment.assistantItemId,
          (item) => ({
            ...item,
            text: item.text + event.delta,
            streaming: true,
          }),
        ),
        activeAssistantItemIdByTurnId: ensuredAssistantSegment.activeAssistantItemIdByTurnId,
        activeAssistantTurnId: event.assistantTurnId,
      }
    }

    case 'assistant.tool.started': {
      const closedAssistantSegment = closeAssistantSegment(state, event.assistantTurnId)
      const toolState = ensureToolItem(
        {
          ...state,
          ...closedAssistantSegment,
          items: closedAssistantSegment.items ?? state.items,
          activeAssistantItemIdByTurnId:
            closedAssistantSegment.activeAssistantItemIdByTurnId ??
            state.activeAssistantItemIdByTurnId,
        },
        event.assistantTurnId,
        event.toolCallId,
      )

      return {
        items: updateItemById<ToolMessageItem>(toolState.items, toolState.toolItemId, (item) => ({
          ...item,
          assistantTurnId: event.assistantTurnId,
          toolCallId: event.toolCallId,
          toolName: event.toolName,
          index: event.index,
          status: 'streaming',
        })),
        activeAssistantItemIdByTurnId:
          closedAssistantSegment.activeAssistantItemIdByTurnId ??
          state.activeAssistantItemIdByTurnId,
        toolItemIdByCallId: toolState.toolItemIdByCallId,
        activeAssistantTurnId: event.assistantTurnId,
      }
    }

    case 'assistant.tool.arguments.delta': {
      const toolState = ensureToolItem(state, event.assistantTurnId, event.toolCallId)
      return {
        items: updateItemById<ToolMessageItem>(toolState.items, toolState.toolItemId, (item) => ({
          ...item,
          assistantTurnId: event.assistantTurnId,
          toolCallId: event.toolCallId,
          args: event.arguments,
          status: 'streaming',
        })),
        toolItemIdByCallId: toolState.toolItemIdByCallId,
        activeAssistantTurnId: event.assistantTurnId,
      }
    }

    case 'assistant.tool.completed': {
      const toolState = ensureToolItem(state, event.assistantTurnId, event.toolCallId)
      return {
        items: updateItemById<ToolMessageItem>(toolState.items, toolState.toolItemId, (item) => ({
          ...item,
          assistantTurnId: event.assistantTurnId,
          toolCallId: event.toolCallId,
          args: event.arguments,
          status: 'completed',
        })),
        toolItemIdByCallId: toolState.toolItemIdByCallId,
      }
    }

    case 'tool.result': {
      const toolState = ensureToolItem(state, event.assistantTurnId, event.toolCallId)
      return {
        items: updateItemById<ToolMessageItem>(toolState.items, toolState.toolItemId, (item) => ({
          ...item,
          assistantTurnId: event.assistantTurnId,
          toolCallId: event.toolCallId,
          result: event.result,
        })),
        toolItemIdByCallId: toolState.toolItemIdByCallId,
      }
    }

    case 'assistant.turn.completed': {
      const closedAssistantSegment = closeAssistantSegment(state, event.assistantTurnId)
      return {
        items: closedAssistantSegment.items ?? state.items,
        activeAssistantItemIdByTurnId:
          closedAssistantSegment.activeAssistantItemIdByTurnId ??
          state.activeAssistantItemIdByTurnId,
        activeAssistantTurnId:
          state.activeAssistantTurnId === event.assistantTurnId
            ? null
            : state.activeAssistantTurnId,
      }
    }

    case 'error':
      return {
        connectionStatus: 'error',
        errorMessage: `${event.code}: ${event.message}`,
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
  stageUserTurn: ({ userTurnId, content }) => {
    set((state) => ({
      pendingUserMessages: upsertPendingMessage(state.pendingUserMessages, userTurnId, content),
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
