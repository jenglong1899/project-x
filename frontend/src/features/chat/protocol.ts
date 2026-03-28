import { z } from 'zod'

const nonEmptyString = z.string().min(1)

const generationStartedEventSchema = z.object({
  type: z.literal('generation.started'),
})

const generationCompletedEventSchema = z.object({
  type: z.literal('generation.completed'),
})

const conversationPersistedEventSchema = z.object({
  type: z.literal('conversation.persisted'),
  conversationId: nonEmptyString,
  displayName: z.string(),
})

const resetContextEventSchema = z.object({
  type: z.literal('reset.context'),
  conversationId: nonEmptyString,
  displayName: z.string(),
})

const userMessageCommittedEventSchema = z.object({
  type: z.literal('user.message.committed'),
  userMessageId: nonEmptyString,
  content: z.string(),
})

const assistantMessageStartedEventSchema = z.object({
  type: z.literal('assistant.message.started'),
  messageId: nonEmptyString,
})

const assistantMessageDeltaEventSchema = z.object({
  type: z.literal('assistant.message.delta'),
  messageId: nonEmptyString,
  channel: z.enum(['reasoning', 'content']),
  delta: z.string(),
})

const assistantMessageCompletedEventSchema = z.object({
  type: z.literal('assistant.message.completed'),
  messageId: nonEmptyString,
})

const toolStartedEventSchema = z.object({
  type: z.literal('tool.started'),
  toolCallId: nonEmptyString,
  toolName: nonEmptyString,
  index: z.number().int().nonnegative(),
})

const toolArgumentsDeltaEventSchema = z.object({
  type: z.literal('tool.arguments.delta'),
  toolCallId: nonEmptyString,
  toolName: nonEmptyString,
  argumentsDelta: z.string(),
})

const toolCompletedEventSchema = z.object({
  type: z.literal('tool.completed'),
  toolCallId: nonEmptyString,
  toolName: nonEmptyString,
  arguments: z.string(),
})

const toolResultEventSchema = z.object({
  type: z.literal('tool.result'),
  toolCallId: nonEmptyString,
  result: z.string(),
})

const errorEventSchema = z.object({
  type: z.literal('error'),
  code: nonEmptyString,
  message: nonEmptyString,
})

export const serverEventSchema = z.discriminatedUnion('type', [
  generationStartedEventSchema,
  generationCompletedEventSchema,
  conversationPersistedEventSchema,
  resetContextEventSchema,
  userMessageCommittedEventSchema,
  assistantMessageStartedEventSchema,
  assistantMessageDeltaEventSchema,
  assistantMessageCompletedEventSchema,
  toolStartedEventSchema,
  toolArgumentsDeltaEventSchema,
  toolCompletedEventSchema,
  toolResultEventSchema,
  errorEventSchema,
])

const sendUserMessageCommandSchema = z.object({
  type: z.literal('send_user_message'),
  userMessageId: nonEmptyString,
  content: z.string().trim().min(1),
})

const pingCommandSchema = z.object({
  type: z.literal('ping'),
})

export const clientCommandSchema = z.discriminatedUnion('type', [
  sendUserMessageCommandSchema,
  pingCommandSchema,
])

export type ServerEvent = z.infer<typeof serverEventSchema>
export type ClientCommand = z.infer<typeof clientCommandSchema>

export function parseServerEvent(payload: unknown): ServerEvent {
  return serverEventSchema.parse(payload)
}

export function safeParseServerEvent(payload: unknown) {
  return serverEventSchema.safeParse(payload)
}

export function parseClientCommand(payload: unknown): ClientCommand {
  return clientCommandSchema.parse(payload)
}

export function safeParseClientCommand(payload: unknown) {
  return clientCommandSchema.safeParse(payload)
}
