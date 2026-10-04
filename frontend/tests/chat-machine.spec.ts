import { describe, expect, it } from 'vitest'

import { ChatMachine, canDecide, canSupplement, normaliseTodos, tryParseObject } from '../src/state/chat'
import type { Envelope } from '../src/state/chat'

function envelope(seq: number, payload: Record<string, unknown>, toolCallId?: string): Envelope {
  return {
    v: 1,
    thread_id: 'thread-1',
    run_id: 'run-1',
    seq,
    source: 'main',
    payload,
    ...(toolCallId ? { tool_call_id: toolCallId } : {}),
  }
}

describe('run terminal states', () => {
  it('ends a run only on done, not on the connection closing', () => {
    const machine = new ChatMachine()
    machine.beginUserTurn('t', '你好', 'user:1')
    machine.applyEnvelope(envelope(1, { text: '正在', message_id: 'm1' }), 'token')

    expect(machine.status).toBe('running')

    machine.applyEnvelope(envelope(2, { status: 'completed' }), 'done')
    expect(machine.status).toBe('completed')
  })

  it('ignores a second done', () => {
    const machine = new ChatMachine()
    machine.beginUserTurn('t', '你好', 'user:1')
    machine.applyEnvelope(envelope(1, { status: 'completed' }), 'done')
    machine.applyEnvelope(envelope(2, { status: 'failed' }), 'done')

    expect(machine.status).toBe('completed')
    expect(machine.snapshot().seq).toBe(1)
  })

  it('never reports completed after an error, even if done says so', () => {
    const machine = new ChatMachine()
    machine.beginUserTurn('t', '你好', 'user:1')
    machine.applyEnvelope(envelope(1, { code: 'MODEL_ERROR', message: 'boom' }), 'error')
    machine.applyEnvelope(envelope(2, { status: 'failed' }), 'done')

    expect(machine.status).toBe('failed')
    expect(machine.error?.code).toBe('MODEL_ERROR')
  })

  it('treats an interrupted run as interrupted, not completed', () => {
    const machine = new ChatMachine()
    machine.beginUserTurn('t', '下单', 'user:1')
    machine.applyEnvelope(
      envelope(1, {
        interrupt_id: 'int-1',
        interrupt_type: 'hitl_approval',
        prompt: '需要确认',
        candidates: [{ type: 'approval', tool_name: 'order_create' }],
      }),
      'interrupt',
    )
    machine.applyEnvelope(envelope(2, { status: 'interrupted', interrupted: true }), 'done')

    expect(machine.status).toBe('interrupted')
    expect(machine.snapshot().interrupt?.interruptType).toBe('hitl_approval')
  })

  it('does not let a late token reopen a finished run', () => {
    const machine = new ChatMachine()
    machine.beginUserTurn('t', '你好', 'user:1')
    machine.applyEnvelope(envelope(1, { status: 'completed' }), 'done')
    machine.applyEnvelope(envelope(2, { text: '迟到的内容', message_id: 'm9' }), 'token')

    expect(machine.status).toBe('completed')
    expect(machine.messages.some((message) => message.content.includes('迟到'))).toBe(false)
  })

  it('marks a stream that ended without done as failed, not finished', () => {
    const machine = new ChatMachine()
    machine.beginUserTurn('t', '你好', 'user:1')
    machine.applyEnvelope(envelope(1, { text: '半截', message_id: 'm1' }), 'token')

    machine.connectionEndedWithoutDone()

    expect(machine.status).toBe('failed')
    expect(machine.error?.code).toBe('STREAM_INCOMPLETE')
  })
})

describe('aggregation by id', () => {
  it('appends tokens to one message instead of creating one per chunk', () => {
    const machine = new ChatMachine()
    machine.beginUserTurn('t', '你好', 'user:1')
    for (const [seq, text] of [[1, 'P001'], [2, ' 库存'], [3, ' 8']] as const) {
      machine.applyEnvelope(envelope(seq, { text, message_id: 'm1' }), 'token')
    }

    const assistants = machine.messages.filter((message) => message.role === 'assistant')
    expect(assistants).toHaveLength(1)
    expect(assistants[0].content).toBe('P001 库存 8')
  })

  it('reconstructs tool arguments from fragments and parses them only when complete', () => {
    const machine = new ChatMachine()
    machine.beginUserTurn('t', '下单', 'user:1')
    machine.applyEnvelope(envelope(1, { name: 'order_create' }, 'c1'), 'tool_start')

    machine.applyEnvelope(envelope(2, { delta: '{"supplier_id": "S' }, 'c1'), 'tool_args')
    expect(machine.toolCalls.get('c1')?.args).toBeNull()

    machine.applyEnvelope(envelope(3, { delta: '001"}' }, 'c1'), 'tool_args')
    expect(machine.toolCalls.get('c1')?.args).toEqual({ supplier_id: 'S001' })
  })

  it('does not start the same tool call twice', () => {
    const machine = new ChatMachine()
    machine.beginUserTurn('t', '下单', 'user:1')
    machine.applyEnvelope(envelope(1, { name: 'order_create' }, 'c1'), 'tool_start')
    machine.applyEnvelope(envelope(2, { name: 'order_create' }, 'c1'), 'tool_start')

    expect(machine.toolCalls.size).toBe(1)
  })

  it('records a failed tool result as an error', () => {
    const machine = new ChatMachine()
    machine.beginUserTurn('t', '下单', 'user:1')
    machine.applyEnvelope(envelope(1, { name: 'order_create' }, 'c1'), 'tool_start')
    machine.applyEnvelope(
      envelope(2, { ok: false, error: { code: 'APPROVAL_REQUIRED', message: '需要审批' } }, 'c1'),
      'tool_result',
    )
    machine.applyEnvelope(envelope(3, { status: 'error' }, 'c1'), 'tool_end')

    const call = machine.toolCalls.get('c1')
    expect(call?.status).toBe('error')
    expect(call?.result?.error?.code).toBe('APPROVAL_REQUIRED')
  })
})

describe('todos and account switching', () => {
  it('replaces the todo list rather than appending', () => {
    const machine = new ChatMachine()
    machine.applyEnvelope(envelope(1, { items: [{ content: 'a', status: 'pending' }] }), 'todos')
    machine.applyEnvelope(envelope(2, { items: [{ content: 'a', status: 'completed' }] }), 'todos')

    expect(machine.todos).toEqual([{ content: 'a', status: 'completed' }])
  })

  it('coerces an unknown todo status to pending', () => {
    expect(normaliseTodos([{ content: 'x', status: 'exploded' }])[0].status).toBe('pending')
  })

  it('clears everything on an account switch', () => {
    const machine = new ChatMachine()
    machine.beginUserTurn('thread-1', '你好', 'user:1')
    machine.applyEnvelope(envelope(1, { text: '内容', message_id: 'm1' }), 'token')
    machine.todos = [{ content: 'a', status: 'pending' }]

    machine.resetForAccountSwitch()

    const snapshot = machine.snapshot()
    expect(snapshot.messages).toEqual([])
    expect(snapshot.todos).toEqual([])
    expect(snapshot.threadId).toBeNull()
    expect(snapshot.status).toBe('idle')
  })

  it('reports an unknown event name instead of dropping it silently', () => {
    const machine = new ChatMachine()
    machine.applyEnvelope(envelope(1, {}), 'quantum_event')

    expect(machine.unknownEvents).toEqual(['quantum_event'])
  })
})

describe('approval affordances', () => {
  it('offers approve/reject only while an approval interrupt is pending', () => {
    const machine = new ChatMachine()
    machine.beginUserTurn('t', '下单', 'user:1')
    expect(canDecide(machine.snapshot())).toBe(false)

    machine.applyEnvelope(
      envelope(1, {
        interrupt_id: 'int-1',
        interrupt_type: 'hitl_approval',
        candidates: [{ type: 'approval', tool_name: 'order_create' }],
      }),
      'interrupt',
    )
    expect(canDecide(machine.snapshot())).toBe(true)
    expect(canSupplement(machine.snapshot())).toBe(false)

    machine.applyEnvelope(envelope(2, { status: 'completed' }), 'done')
    expect(canDecide(machine.snapshot())).toBe(false)
  })

  it('offers the supplement form for a supplement interrupt', () => {
    const machine = new ChatMachine()
    machine.beginUserTurn('t', '下单', 'user:1')
    machine.applyEnvelope(
      envelope(1, {
        interrupt_id: 'int-2',
        interrupt_type: 'order_info_supplement',
        prompt: '请补充单价',
        candidates: [{ type: 'supplement', missing_fields: ['lines[0].unit_price'] }],
      }),
      'interrupt',
    )

    expect(canSupplement(machine.snapshot())).toBe(true)
    expect(canDecide(machine.snapshot())).toBe(false)
  })
})

describe('argument parsing', () => {
  it('accepts a complete object and refuses a partial one', () => {
    expect(tryParseObject('{"a": 1}')).toEqual({ a: 1 })
    expect(tryParseObject('{"a": ')).toBeNull()
    expect(tryParseObject('[1,2]')).toBeNull()
    expect(tryParseObject('')).toBeNull()
  })
})
