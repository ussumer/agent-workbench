/**
 * Background task state, as the server reports it.
 *
 * The rule this module exists to enforce: **a task is only finished when the server says it
 * is.** There is no local success state, no timer that concludes anything, and no optimistic
 * "completed" — because the one failure that actually hurts is showing a finished task whose
 * run the service has forgotten, leaving the user waiting for a report that cannot arrive.
 *
 * `lost` is therefore a first-class status, not an error to be folded into `failed`: it says
 * "we no longer know", which is different from "it broke" and different again from "it worked".
 */

export type AsyncTaskStatus =
  | 'queued'
  | 'running'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'lost'

/** Statuses after which nothing more will happen. Mirrors the server's `terminal` flag. */
export const TERMINAL_STATUSES: ReadonlySet<AsyncTaskStatus> = new Set([
  'completed',
  'failed',
  'cancelled',
  'lost',
])

export interface AsyncTaskUpdate {
  request_id: string
  instruction: string
  handling: string
  note: string
  run_id: string
}

export interface AsyncTask {
  task_id: string
  status: AsyncTaskStatus
  parent_thread_id: string
  async_thread_id: string
  async_run_id: string
  instruction: string
  artifact_ids: string[]
  created_at: string
  updated_at: string
  terminal: boolean
  error: string
  updates: AsyncTaskUpdate[]
}

/** How each status reads to a person. */
export const STATUS_LABELS: Record<AsyncTaskStatus, string> = {
  queued: '排队中',
  running: '分析中',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
  lost: '状态未知',
}

/** The statuses that mean the user should stop waiting. */
export function isTerminal(task: AsyncTask): boolean {
  return task.terminal || TERMINAL_STATUSES.has(task.status)
}

/**
 * What to tell the user about a task.
 *
 * `lost` and `failed` both end the wait but say different things, and a task the frontend
 * cannot classify is reported as unknown rather than assumed to have succeeded — the whole
 * point of this module is that "no bad news" is not "good news".
 */
export function describe(task: AsyncTask): string {
  if (task.status === 'lost') {
    return '后台服务不再认识这次任务（可能重启过），结果无法确认，请重新发起。'
  }
  if (task.status === 'failed') {
    return task.error ? `分析失败：${task.error}` : '分析失败。'
  }
  if (task.status === 'completed') {
    return task.artifact_ids.length
      ? `分析完成，产出 ${task.artifact_ids.length} 个文件。`
      : '分析完成。'
  }
  if (task.status === 'cancelled') {
    return '已取消。'
  }
  return `${STATUS_LABELS[task.status] ?? '处理中'}…`
}

export interface AsyncTaskClientOptions {
  /** Injected for tests; defaults to the browser's fetch. */
  fetchImpl?: typeof fetch
  baseUrl?: string
}

/**
 * The endpoint client. Every call is owner-scoped server-side, so nothing here passes an
 * identity — the cookie does that, and a client that tried to name an owner would be ignored.
 */
export class AsyncTaskClient {
  private readonly fetchImpl: typeof fetch
  private readonly baseUrl: string

  constructor(options: AsyncTaskClientOptions = {}) {
    this.fetchImpl = options.fetchImpl ?? fetch
    this.baseUrl = options.baseUrl ?? ''
  }

  private async request(path: string, init?: RequestInit): Promise<AsyncTask> {
    const response = await this.fetchImpl(`${this.baseUrl}${path}`, {
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      ...init,
    })
    if (!response.ok) {
      // The status code is part of the message on purpose: 503 ("service unavailable") and
      // 404 ("no such task") call for different things from the user, and a generic error
      // would erase that distinction right where it matters.
      const detail = await response.text().catch(() => '')
      throw new Error(`HTTP ${response.status}${detail ? `: ${detail.slice(0, 200)}` : ''}`)
    }
    const body = (await response.json()) as { data: AsyncTask }
    return body.data
  }

  launch(
    parentThreadId: string,
    instruction: string,
    requestId: string,
  ): Promise<AsyncTask> {
    return this.request('/api/async-tasks', {
      method: 'POST',
      body: JSON.stringify({
        parent_thread_id: parentThreadId,
        request_id: requestId,
        instruction,
      }),
    })
  }

  status(taskId: string): Promise<AsyncTask> {
    return this.request(`/api/async-tasks/${encodeURIComponent(taskId)}`)
  }

  update(taskId: string, instruction: string, requestId: string): Promise<AsyncTask> {
    return this.request(`/api/async-tasks/${encodeURIComponent(taskId)}/update`, {
      method: 'POST',
      body: JSON.stringify({ request_id: requestId, instruction }),
    })
  }

  cancel(taskId: string, requestId: string): Promise<AsyncTask> {
    return this.request(`/api/async-tasks/${encodeURIComponent(taskId)}/cancel`, {
      method: 'POST',
      body: JSON.stringify({ request_id: requestId }),
    })
  }
}
