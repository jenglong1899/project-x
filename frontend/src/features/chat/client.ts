import { parseClientCommand, safeParseServerEvent } from './protocol'
import { useChatStore } from './store'

type ChatClientOptions = {
  url?: string
  path?: string
}

function resolveWebSocketUrl({ url, path = '/ws' }: ChatClientOptions): string {
  if (url) {
    return url
  }

  const resolvedUrl = new URL(path, window.location.href)
  if (resolvedUrl.protocol === 'http:') {
    resolvedUrl.protocol = 'ws:'
  } else if (resolvedUrl.protocol === 'https:') {
    resolvedUrl.protocol = 'wss:'
  }
  return resolvedUrl.toString()
}

function setClientError(message: string) {
  useChatStore.setState({
    connectionStatus: 'error',
    errorMessage: message,
  })
}

export class ChatClient {
  private socket: WebSocket | null = null
  private readonly options: ChatClientOptions
  private readonly intentionallyClosedSockets = new WeakSet<WebSocket>()
  private connectionAttempt = 0

  constructor(options: ChatClientOptions = {}) {
    this.options = options
  }

  connect(options: ChatClientOptions = {}) {
    if (
      this.socket &&
      (this.socket.readyState === WebSocket.OPEN ||
        this.socket.readyState === WebSocket.CONNECTING)
    ) {
      return
    }

    useChatStore.setState({
      connectionStatus: 'connecting',
      errorMessage: null,
    })

    const socket = new WebSocket(resolveWebSocketUrl({ ...this.options, ...options }))
    const connectionAttempt = ++this.connectionAttempt
    this.socket = socket
    const isActiveSocket = () =>
      this.socket === socket && this.connectionAttempt === connectionAttempt

    socket.addEventListener('open', () => {
      if (!isActiveSocket()) {
        return
      }
      useChatStore.getState().setConnectionStatus('open')
    })

    socket.addEventListener('message', (event) => {
      if (!isActiveSocket()) {
        return
      }

      let payload: unknown
      try {
        payload = JSON.parse(String(event.data))
      } catch {
        setClientError('收到的 WebSocket 消息不是合法 JSON。')
        return
      }

      const parsed = safeParseServerEvent(payload)
      if (!parsed.success) {
        setClientError('收到的 WebSocket 消息未通过协议校验。')
        return
      }

      useChatStore.getState().applyServerEvent(parsed.data)
    })

    socket.addEventListener('error', () => {
      if (!isActiveSocket() || this.intentionallyClosedSockets.has(socket)) {
        return
      }
      setClientError('WebSocket 连接发生错误。')
    })

    socket.addEventListener('close', () => {
      const isIntentionalClose = this.intentionallyClosedSockets.has(socket)
      this.intentionallyClosedSockets.delete(socket)
      if (!isActiveSocket()) {
        return
      }

      this.socket = null
      if (isIntentionalClose) {
        return
      }

      const currentStatus = useChatStore.getState().connectionStatus
      if (currentStatus !== 'error') {
        useChatStore.getState().setConnectionStatus('closed')
      }
    })
  }

  disconnect() {
    const socket = this.socket
    this.socket = null
    if (socket) {
      this.intentionallyClosedSockets.add(socket)
      socket.close()
    }
    useChatStore.getState().setConnectionStatus('closed')
  }

  sendUserMessage(content: string) {
    const trimmedContent = content.trim()
    if (!trimmedContent) {
      return null
    }

    const socket = this.socket
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      throw new Error('WebSocket 尚未连接，无法发送消息。')
    }

    const userMessageId = crypto.randomUUID()
    const command = parseClientCommand({
      type: 'send_user_message',
      userMessageId,
      content: trimmedContent,
    })

    useChatStore.getState().stageUserMessage({
      userMessageId,
      content: trimmedContent,
    })
    socket.send(JSON.stringify(command))
    return userMessageId
  }

  ping() {
    const socket = this.socket
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      return
    }

    socket.send(JSON.stringify(parseClientCommand({ type: 'ping' })))
  }
}

export const chatClient = new ChatClient()
