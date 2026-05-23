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
  private reconnectTimeoutId: number | null = null
  private pingIntervalId: number | null = null
  private reconnectDelayMs = 500

  constructor(options: ChatClientOptions = {}) {
    this.options = options
  }

  private clearReconnectTimer() {
    if (this.reconnectTimeoutId === null) {
      return
    }
    window.clearTimeout(this.reconnectTimeoutId)
    this.reconnectTimeoutId = null
  }

  private clearPingTimer() {
    if (this.pingIntervalId === null) {
      return
    }
    window.clearInterval(this.pingIntervalId)
    this.pingIntervalId = null
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

    this.clearReconnectTimer()

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
      this.reconnectDelayMs = 500

      this.clearPingTimer()
      // 这里的 ping 不是为了“服务端回包”，而是为了在开发环境（代理/网关）下避免长时间空闲导致连接被动断开。
      // 断点会把后端事件循环卡住，无法保证不断连；但 keepalive + 自动重连能显著降低调试成本。
      this.pingIntervalId = window.setInterval(() => this.ping(), 15_000)
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
      this.clearPingTimer()
      if (isIntentionalClose) {
        return
      }

      const currentStatus = useChatStore.getState().connectionStatus
      if (currentStatus !== 'error') {
        useChatStore.getState().setConnectionStatus('closed')
      }

      this.clearReconnectTimer()
      const delay = this.reconnectDelayMs
      this.reconnectDelayMs = Math.min(this.reconnectDelayMs * 2, 10_000)
      this.reconnectTimeoutId = window.setTimeout(() => {
        if (this.socket) {
          return
        }
        this.connect(options)
      }, delay)
    })
  }

  disconnect() {
    this.clearReconnectTimer()
    this.clearPingTimer()
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

  requestPause() {
    const socket = this.socket
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      return
    }
    socket.send(JSON.stringify(parseClientCommand({ type: 'request_pause' })))
  }

  resume() {
    const socket = this.socket
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      return
    }
    socket.send(JSON.stringify(parseClientCommand({ type: 'resume' })))
  }
}

export const chatClient = new ChatClient()
