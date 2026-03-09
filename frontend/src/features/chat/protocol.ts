import { z } from 'zod'

const nonEmptyString = z.string().min(1)

const sessionStartedEventSchema = z.object({
  type: z.literal('session.started'),
  sessionId: nonEmptyString,
})

const userTurnEnqueuedEventSchema = z.object({
  type: z.literal('user.turn.enqueued'),
  userTurnId: nonEmptyString,
})

const userTurnCommittedEventSchema = z.object({
  type: z.literal('user.turn.committed'),
  userTurnId: nonEmptyString,
})

const assistantTurnStartedEventSchema = z.object({
  type: z.literal('assistant.turn.started'),
  assistantTurnId: nonEmptyString,
})

const assistantContentDeltaEventSchema = z.object({
  type: z.literal('assistant.content.delta'),
  assistantTurnId: nonEmptyString,
  delta: z.string(),
})

const assistantReasoningDeltaEventSchema = z.object({
  type: z.literal('assistant.reasoning.delta'),
  assistantTurnId: nonEmptyString,
  delta: z.string(),
})

const assistantToolStartedEventSchema = z.object({
  type: z.literal('assistant.tool.started'),
  assistantTurnId: nonEmptyString,
  toolCallId: nonEmptyString,
  toolName: nonEmptyString,
  index: z.number().int().nonnegative(),
})

const assistantToolArgumentsDeltaEventSchema = z.object({
  type: z.literal('assistant.tool.arguments.delta'),
  assistantTurnId: nonEmptyString,
  toolCallId: nonEmptyString,
  delta: z.string(),
  arguments: z.string(),
})

const assistantToolCompletedEventSchema = z.object({
  type: z.literal('assistant.tool.completed'),
  assistantTurnId: nonEmptyString,
  toolCallId: nonEmptyString,
  arguments: z.string(),
})

const toolResultEventSchema = z.object({
  type: z.literal('tool.result'),
  assistantTurnId: nonEmptyString,
  toolCallId: nonEmptyString,
  result: z.string(),
})

const assistantTurnCompletedEventSchema = z.object({
  type: z.literal('assistant.turn.completed'),
  assistantTurnId: nonEmptyString,
})

const errorEventSchema = z.object({
  type: z.literal('error'),
  code: nonEmptyString,
  message: nonEmptyString,
})

export const serverEventSchema = z.discriminatedUnion('type', [
  sessionStartedEventSchema,
  userTurnEnqueuedEventSchema,
  userTurnCommittedEventSchema,
  assistantTurnStartedEventSchema,
  assistantContentDeltaEventSchema,
  assistantReasoningDeltaEventSchema,
  assistantToolStartedEventSchema,
  assistantToolArgumentsDeltaEventSchema,
  assistantToolCompletedEventSchema,
  toolResultEventSchema,
  assistantTurnCompletedEventSchema,
  errorEventSchema,
])

const sendUserMessageCommandSchema = z.object({
  type: z.literal('send_user_message'),
  userTurnId: nonEmptyString,
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
