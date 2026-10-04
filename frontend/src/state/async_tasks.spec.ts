import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

import AsyncTasks from '../components/AsyncTasks.vue'
import {
  AsyncTaskClient,
  TERMINAL_STATUSES,
  describe as describeTask,
  isTerminal,
  type AsyncTask,
} from './async_tasks'

function task(overrides: Partial<AsyncTask> = {}): AsyncTask {
  return {
    task_id: 'task-1',
    status: 'running',
    parent_thread_id: 'thread-1',
    async_thread_id: 'aaaa',
    async_run_id: 'bbbb',
    instruction: '看看哪些物料要补货',
    artifact_ids: [],
    created_at: '',
    updated_at: '',
    terminal: false,
    error: '',
    updates: [],
    ...overrides,
  }
}

describe('async task status', () => {
  it('treats every status the server calls terminal as final', () => {
    for (const status of ['completed', 'failed', 'cancelled', 'lost'] as const) {
      expect(TERMINAL_STATUSES.has(status)).toBe(true)
      expect(isTerminal(task({ status }))).toBe(true)
    }
    for (const status of ['queued', 'running'] as const) {
      expect(isTerminal(task({ status }))).toBe(false)
    }
  })

  it('trusts the server flag over the status word', () => {
    // A new status the frontend has never heard of must not be assumed finished.
    expect(isTerminal(task({ status: 'paused' as never, terminal: false }))).toBe(false)
  })

  it('says something different for lost than for failed', () => {
    const lost = describeTask(task({ status: 'lost' }))
    const failed = describeTask(task({ status: 'failed' }))

    expect(lost).not.toEqual(failed)
    expect(lost).toContain('重新发起')
    expect(lost).not.toContain('完成')
  })

  it('never claims completion without the server saying so', () => {
    for (const status of ['queued', 'running', 'failed', 'cancelled', 'lost'] as const) {
      expect(describeTask(task({ status }))).not.toContain('完成，')
    }
    expect(describeTask(task({ status: 'completed' }))).toContain('分析完成')
  })

  it('mentions the failure reason when there is one', () => {
    expect(describeTask(task({ status: 'failed', error: 'gateway 503' }))).toContain('gateway 503')
  })
})

describe('AsyncTaskClient', () => {
  it('keeps the status code in the error so 503 and 404 stay distinguishable', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(
      new Response('后台分析服务不可用', { status: 503 }),
    )
    const client = new AsyncTaskClient({ fetchImpl: fetchImpl as unknown as typeof fetch })

    await expect(client.status('task-1')).rejects.toThrow('HTTP 503')
  })

  it('sends no identity of its own — the cookie does that', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ data: task() }), { status: 200 }),
    )
    const client = new AsyncTaskClient({ fetchImpl: fetchImpl as unknown as typeof fetch })

    await client.launch('thread-1', '看看库存', 'req-1')

    const [, init] = fetchImpl.mock.calls[0]
    expect(init.credentials).toBe('same-origin')
    expect(JSON.stringify(init.body)).not.toContain('owner')
  })
})

describe('AsyncTasks component', () => {
  it('renders the server status rather than a guess', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({ data: { items: [task({ status: 'lost' })] } }),
        { status: 200 },
      ),
    )
    vi.stubGlobal('fetch', fetchImpl as unknown as typeof fetch)

    const wrapper = mount(AsyncTasks, { props: { threadId: 'thread-1' } })
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(wrapper.text()).toContain('状态未知')
    expect(wrapper.text()).not.toContain('已完成')
    vi.unstubAllGlobals()
  })

  it('offers cancel only while the task can still be cancelled', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          data: { items: [task({ status: 'running' }), task({ task_id: 'task-2', status: 'completed', terminal: true })] },
        }),
        { status: 200 },
      ),
    )
    vi.stubGlobal('fetch', fetchImpl as unknown as typeof fetch)

    const wrapper = mount(AsyncTasks, { props: { threadId: 'thread-1' } })
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(wrapper.findAll('button.cancel')).toHaveLength(1)
    vi.unstubAllGlobals()
  })

  it('says nothing at all when there is no conversation', () => {
    const wrapper = mount(AsyncTasks, { props: { threadId: null } })

    expect(wrapper.text()).toBe('')
  })
})
