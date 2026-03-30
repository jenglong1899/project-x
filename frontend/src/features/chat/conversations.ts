import { z } from 'zod'

import type { ChatItem, ToolMessageItem } from './store'

const nonEmptyString = z.string().min(1)

const conversationListItemSchema = z.object({
  conversationId: nonEmptyString,
  displayName: z.string(),
  lastChatTime: z.string(),
})

const conversationListResponseSchema = z.object({
  conversations: z.array(conversationListItemSchema),
})

const conversationMessageSchema = z
  .object({
    role: z.string(),
    content: z.unknown().optional(),
    reasoning_content: z.string().optional(),
    tool_calls: z.array(z.unknown()).optional(),
    tool_call_id: z.string().optional(),
  })
  .passthrough()

const conversationDetailResponseSchema = z.object({
  conversationId: nonEmptyString,
  displayName: z.string(),
  lastChatTime: z.string(),
  messages: z.array(conversationMessageSchema),
})

export type ConversationListItem = z.infer<typeof conversationListItemSchema>
export type ConversationDetail = z.infer<typeof conversationDetailResponseSchema>

function stringifyMessageContent(content: unknown): string {
  if (content == null) {
    return ''
  }
  if (typeof content === 'string') {
    return content
  }
  try {
    return JSON.stringify(content, null, 2)
  } catch {
    return String(content)
  }
}

export async function fetchConversationList(): Promise<ConversationListItem[]> {
  const response = await fetch('/conversations')
  if (!response.ok) {
    throw new Error(`加载会话列表失败（${response.status}）`)
  }
  const payload: unknown = await response.json()
  const parsed = conversationListResponseSchema.safeParse(payload)
  if (!parsed.success) {
    throw new Error('加载会话列表失败（响应格式不正确）')
  }
  return parsed.data.conversations
}

export async function fetchConversationDetail(conversationId: string): Promise<ConversationDetail> {
  const response = await fetch(`/conversations/${encodeURIComponent(conversationId)}`)
  if (!response.ok) {
    throw new Error(`加载会话失败（${response.status}）`)
  }
  const payload: unknown = await response.json()
  const parsed = conversationDetailResponseSchema.safeParse(payload)
  if (!parsed.success) {
    throw new Error('加载会话失败（响应格式不正确）')
  }
  return parsed.data
}

type ToolCallInfo = {
  toolCallId: string
  toolName: string
  index: number
  args: string
}

function parseToolCalls(rawToolCalls: unknown): ToolCallInfo[] {
  if (!Array.isArray(rawToolCalls)) {
    return []
  }

  const parsed: ToolCallInfo[] = []
  for (let index = 0; index < rawToolCalls.length; index += 1) {
    const toolCall = rawToolCalls[index]
    if (!toolCall || typeof toolCall !== 'object') {
      continue
    }

    const toolCallRecord = toolCall as Record<string, unknown>
    const toolCallId = typeof toolCallRecord.id === 'string' ? toolCallRecord.id : ''

    const functionPayload = toolCallRecord['function']
    const functionRecord =
      functionPayload && typeof functionPayload === 'object'
        ? (functionPayload as Record<string, unknown>)
        : null

    const toolName = functionRecord && typeof functionRecord.name === 'string' ? functionRecord.name : ''
    const args =
      functionRecord && typeof functionRecord.arguments === 'string' ? functionRecord.arguments : ''

    parsed.push({
      toolCallId: toolCallId || crypto.randomUUID(),
      toolName,
      index,
      args,
    })
  }
  return parsed
}

export function buildChatItemsFromConversationHistory(messages: ConversationDetail['messages']): ChatItem[] {
  const items: ChatItem[] = []
  const toolItemIndexByToolCallId = new Map<string, number>()

  let startIndex = 0
  if (messages[0]?.role === 'system' && messages[1]?.role === 'user') {
    startIndex = 2
  }

  for (let i = startIndex; i < messages.length; i += 1) {
    const message = messages[i]
    if (!message) {
      continue
    }

    if (message.role === 'user') {
      const userMessageId = crypto.randomUUID()
      items.push({
        id: `user:${userMessageId}`,
        kind: 'user',
        userMessageId,
        text: stringifyMessageContent(message.content),
      })
      continue
    }

    if (message.role === 'assistant') {
      const messageId = crypto.randomUUID()
      items.push({
        id: `assistant:${messageId}`,
        kind: 'assistant',
        messageId,
        reasoning: message.reasoning_content ?? '',
        text: stringifyMessageContent(message.content),
        streaming: false,
      })

      for (const toolCall of parseToolCalls(message.tool_calls)) {
        const toolItem: ToolMessageItem = {
          id: `tool:${toolCall.toolCallId}`,
          kind: 'tool',
          toolCallId: toolCall.toolCallId,
          toolName: toolCall.toolName,
          index: toolCall.index,
          args: toolCall.args,
          result: '',
          status: 'completed',
        }
        toolItemIndexByToolCallId.set(toolCall.toolCallId, items.length)
        items.push(toolItem)
      }

      continue
    }

    if (message.role === 'tool') {
      const toolCallId = typeof message.tool_call_id === 'string' ? message.tool_call_id : ''
      const result = stringifyMessageContent(message.content)

      if (toolCallId && toolItemIndexByToolCallId.has(toolCallId)) {
        const itemIndex = toolItemIndexByToolCallId.get(toolCallId) ?? -1
        const current = items[itemIndex]
        if (current && current.kind === 'tool') {
          items[itemIndex] = {
            ...current,
            result,
          }
          continue
        }
      }

      const fallbackToolCallId = toolCallId || crypto.randomUUID()
      items.push({
        id: `tool:${fallbackToolCallId}`,
        kind: 'tool',
        toolCallId: fallbackToolCallId,
        toolName: '未命名工具',
        index: null,
        args: '',
        result,
        status: 'completed',
      })
      continue
    }
  }

  return items
}
