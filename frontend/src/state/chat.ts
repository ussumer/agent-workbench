/**
 * The chat state machine: what the UI believes, and why.
 *
 * Two rules from the contract are enforced here rather than in a component, because a
 * component cannot enforce them consistently:
 *
 * * **`done` arrives exactly once, and it is the only thing that ends a run.** A run that
 *   stops producing tokens has not finished; the connection closing has not finished it
 *   either. Only `done` moves the state to a terminal status.
 * * **`interrupted` and `failed` are not `completed`.** The UI must not show a green
 *   "完成" for a run parked on an approval, and a late `token` arriving after an `error`
 *   must not quietly promote the run back to a success.
 *
 * Text and tool calls are aggregated by id, so a reconnect or a duplicate delivery adds
 * nothing rather than appending the same content twice.
 */

import type { SseFrame } from '../api/sse'

export type RunStatus =
  | 'idle'
  | 'running'
  | 'interrupted'
  | 'completed'
  | 'failed'
  | 'cancelled'

export type TodoStatus = 'pending' | 'in_progress' | 'completed'

export interface TodoItem {
  content: string
  status: TodoStatus
}

export interface ToolCallState {
  id: string
  name: string
  /** Raw fragments, kept so the UI can show arguments as they stream in. */
  argsText: string
  args: Record<string, unknown> | null
  result: { ok: boolean; data?: unknown; error?: { code: string; message: string } } | null
  status: 'running' | 'success' | 'error'
  source: string
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  /** True while tokens are still arriving for this message. */
  streaming: boolean
}

export interface InterruptState {
  interruptId: string
  interruptType: 'order_info_supplement' | 'hitl_approval' | 'unknown'
  prompt: string
  candidates: Array<Record<string, unknown>>
}

export interface Envelope {
  v: number
  thread_id: string
  run_id: string
  seq: number
  source: string
  payload: Record<string, unknown>
  tool_call_id?: string
  /** Present only when a caller constructs an envelope by hand; the wire carries it in the
   *  SSE `event:` field instead. */
  event?: string
}

/** Event names this client knows. An unknown name is recorded, not silently dropped. */
export const KNOWN_EVENTS = new Set([
  'run_started',
  'token',
  'tool_start',
  'tool_args',
  'tool_result',
  'tool_end',
  'todos',
  'interrupt',
  'artifact',
  'error',
  'done',
])

export interface ChatSnapshot {
  status: RunStatus
  threadId: string | null
  runId: string | null
  messages: ChatMessage[]
  toolCalls: ToolCallState[]
  todos: TodoItem[]
  interrupt: InterruptState | null
  lastEventId: string | null
  seq: number
  error: { code: string; message: string; retryable: boolean } | null
  interruptionsSeen: number
  unknownEvents: string[]
}

export class ChatMachine {
  status: RunStatus = 'idle'
  threadId: string | null = null
  runId: string | null = null
  messages: ChatMessage[] = []
  toolCalls = new Map<string, ToolCallState>()
  todos: TodoItem[] = []
  interrupt: InterruptState | null = null
  lastEventId: string | null = null
  seq = 0
  error: { code: string; message: string; retryable: boolean } | null = null
  interruptionsSeen = 0
  unknownEvents: string[] = []

  private doneSeen = false

  /** Start a local turn: the user's message appears before the server answers. */
  beginUserTurn(threadId: string | null, text: string, localId: string): void {
    this.threadId = threadId
    this.status = 'running'
    this.error = null
    this.interrupt = null
    this.doneSeen = false
    this.seq = 0
    this.toolCalls.clear()
    this.messages.push({ id: localId, role: 'user', content: text, streaming: false })
  }

  applyFrame(frame: SseFrame): void {
    if (!frame.data) return
    let envelope: Envelope
    try {
      envelope = JSON.parse(frame.data) as Envelope
    } catch {
      // A frame we cannot parse is a protocol problem, not a user-facing event; record it
      // so the failure is visible instead of the event simply vanishing.
      this.unknownEvents.push(`unparseable:${frame.event}`)
      return
    }
    this.applyEnvelope(envelope, frame.event)
  }

  applyEnvelope(envelope: Envelope, eventName?: string): void {
    // The event name lives in the SSE `event:` field, not in the JSON body — the body is
    // the same envelope for every event type. Read it from the frame, and if it is missing
    // say so rather than guessing from the payload's shape.
    const name = eventName ?? (typeof envelope.event === 'string' ? envelope.event : '')
    if (!name || !KNOWN_EVENTS.has(name)) {
      this.unknownEvents.push(name || 'unnamed')
      return
    }
    if (this.doneSeen) {
      // Terminals are final. A late token after `done` must not reopen the run — the
      // contract calls this out as "interrupt 状态和终结不可被迟到 token 覆盖".
      return
    }

    if (envelope.thread_id) this.threadId = envelope.thread_id
    if (envelope.run_id) this.runId = envelope.run_id
    if (typeof envelope.seq === 'number') this.seq = Math.max(this.seq, envelope.seq)
    this.lastEventId = `${envelope.run_id}:${envelope.seq}`
    const payload = envelope.payload ?? {}

    switch (name) {
      case 'run_started':
        this.status = 'running'
        break
      case 'token':
        this.appendToken(String(payload.text ?? ''), String(payload.message_id ?? ''))
        break
      case 'tool_start':
        this.startTool(envelope.tool_call_id ?? '', String(payload.name ?? ''), envelope.source)
        break
      case 'tool_args':
        this.appendToolArgs(envelope.tool_call_id ?? '', String(payload.delta ?? ''))
        break
      case 'tool_result':
        this.applyToolResult(envelope.tool_call_id ?? '', payload)
        break
      case 'tool_end':
        this.endTool(envelope.tool_call_id ?? '', String(payload.status ?? 'success'))
        break
      case 'todos':
        this.todos = normaliseTodos(payload.items)
        break
      case 'interrupt':
        this.applyInterrupt(payload)
        break
      case 'error':
        this.error = {
          code: String(payload.code ?? 'ERROR'),
          message: String(payload.message ?? ''),
          retryable: Boolean(payload.retryable),
        }
        // An error is terminal until the server says otherwise; it is never a success.
        this.status = 'failed'
        break
      case 'done':
        this.applyDone(payload)
        break
    }
  }

  private appendToken(text: string, messageId: string): void {
    if (!text) return
    const id = messageId || `assistant:${this.runId ?? 'current'}`
    const existing = this.messages.find((message) => message.id === id)
    if (existing) {
      existing.content += text
      existing.streaming = true
      return
    }
    this.messages.push({ id, role: 'assistant', content: text, streaming: true })
  }

  private startTool(id: string, name: string, source: string): void {
    if (!id) return
    if (this.toolCalls.has(id)) return
    this.toolCalls.set(id, {
      id,
      name,
      argsText: '',
      args: null,
      result: null,
      status: 'running',
      source: source || 'main',
    })
  }

  private appendToolArgs(id: string, delta: string): void {
    const call = this.toolCalls.get(id)
    if (!call || !delta) return
    call.argsText += delta
    // Arguments arrive as fragments; only a complete object is worth parsing.
    const parsed = tryParseObject(call.argsText)
    if (parsed) call.args = parsed
  }

  private applyToolResult(id: string, payload: Record<string, unknown>): void {
    const call = this.toolCalls.get(id)
    if (!call || call.result) return
    const ok = payload.ok !== false
    const error = payload.error as { code?: string; message?: string } | undefined
    call.result = {
      ok,
      data: payload.data,
      ...(ok || !error
        ? {}
        : {
            error: {
              code: String(error.code ?? 'TOOL_ERROR'),
              message: String(error.message ?? ''),
            },
          }),
    }
    if (!ok) call.status = 'error'
  }

  private endTool(id: string, status: string): void {
    const call = this.toolCalls.get(id)
    if (!call) return
    if (status === 'error') call.status = 'error'
    else if (call.status !== 'error') call.status = 'success'
  }

  private applyInterrupt(payload: Record<string, unknown>): void {
    this.interrupt = {
      interruptId: String(payload.interrupt_id ?? ''),
      interruptType: (String(payload.interrupt_type ?? 'unknown') as InterruptState['interruptType']),
      prompt: String(payload.prompt ?? ''),
      candidates: Array.isArray(payload.candidates)
        ? (payload.candidates as Array<Record<string, unknown>>)
        : [],
    }
    this.interruptionsSeen += 1
    this.status = 'interrupted'
  }

  private applyDone(payload: Record<string, unknown>): void {
    const status = String(payload.status ?? 'failed') as RunStatus
    // Only the four terminal statuses can end a run; anything else is malformed, and
    // guessing "completed" would be the one mistake this whole module exists to prevent.
    this.status =
      status === 'completed' || status === 'interrupted' || status === 'failed' || status === 'cancelled'
        ? status
        : 'failed'
    if (typeof payload.content === 'string' && payload.content) {
      const last = this.messages[this.messages.length - 1]
      if (!last || last.role !== 'assistant') {
        this.messages.push({
          id: `assistant:${this.runId ?? 'final'}`,
          role: 'assistant',
          content: payload.content,
          streaming: false,
        })
      }
    }
    for (const message of this.messages) message.streaming = false
    this.doneSeen = true
    if (this.status !== 'interrupted') this.interrupt = null
  }

  /** Applied when the stream ends without a `done`: the run did not finish cleanly. */
  connectionEndedWithoutDone(): void {
    if (this.doneSeen) return
    this.status = 'failed'
    this.error = this.error ?? {
      code: 'STREAM_INCOMPLETE',
      message: '连接在收到结束事件前断开；请刷新查看实际状态，不要自动重发。',
      retryable: false,
    }
    for (const message of this.messages) message.streaming = false
  }

  cancelRequested(): void {
    this.status = 'cancelled'
  }

  /** Reset everything the previous account could have seen. */
  resetForAccountSwitch(): void {
    this.status = 'idle'
    this.threadId = null
    this.runId = null
    this.messages = []
    this.toolCalls.clear()
    this.todos = []
    this.interrupt = null
    this.lastEventId = null
    this.seq = 0
    this.error = null
    this.interruptionsSeen = 0
    this.unknownEvents = []
    this.doneSeen = false
  }

  snapshot(): ChatSnapshot {
    return {
      status: this.status,
      threadId: this.threadId,
      runId: this.runId,
      messages: this.messages.map((message) => ({ ...message })),
      toolCalls: [...this.toolCalls.values()].map((call) => ({ ...call })),
      todos: this.todos.map((todo) => ({ ...todo })),
      interrupt: this.interrupt ? { ...this.interrupt } : null,
      lastEventId: this.lastEventId,
      seq: this.seq,
      error: this.error ? { ...this.error } : null,
      interruptionsSeen: this.interruptionsSeen,
      unknownEvents: [...this.unknownEvents],
    }
  }
}

export function tryParseObject(text: string): Record<string, unknown> | null {
  const trimmed = text.trim()
  if (!trimmed.startsWith('{')) return null
  try {
    const parsed = JSON.parse(trimmed)
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : null
  } catch {
    return null
  }
}

export function normaliseTodos(items: unknown): TodoItem[] {
  if (!Array.isArray(items)) return []
  return items.map((item) => {
    const record = (item ?? {}) as Record<string, unknown>
    const status = String(record.status ?? 'pending')
    return {
      content: String(record.content ?? ''),
      status: (status === 'in_progress' || status === 'completed' ? status : 'pending') as TodoStatus,
    }
  })
}

/** Whether the approve/reject buttons should be offered. */
export function canDecide(snapshot: ChatSnapshot): boolean {
  return (
    snapshot.status === 'interrupted' &&
    snapshot.interrupt?.interruptType === 'hitl_approval'
  )
}

/** Whether the supplement form should be offered. */
export function canSupplement(snapshot: ChatSnapshot): boolean {
  return (
    snapshot.status === 'interrupted' &&
    snapshot.interrupt?.interruptType === 'order_info_supplement'
  )
}
