<script setup lang="ts">
/**
 * Background analysis tasks for the current conversation.
 *
 * Deliberately dumb about outcomes: it renders what the server reported and stops polling
 * once a task is terminal. It never concludes success on its own — no timeout becomes
 * "probably done", a dropped connection becomes "状态未知", and a task the server calls
 * `lost` is shown as lost. Those are the cases where a confident UI would be actively
 * harmful, because the user would stop waiting for something that is never coming.
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import {
  AsyncTaskClient,
  STATUS_LABELS,
  describe as describeTask,
  isTerminal,
  type AsyncTask,
} from '../state/async_tasks'

const props = defineProps<{
  threadId: string | null
  client?: AsyncTaskClient
}>()

const POLL_INTERVAL_MS = 2000
/** After this many consecutive failures we stop asking; the badge says we do not know. */
const MAX_POLL_FAILURES = 5

const endpoint = props.client ?? new AsyncTaskClient()

const tasks = ref<AsyncTask[]>([])
const polling = ref(false)
const problem = ref('')

let timer: ReturnType<typeof setTimeout> | null = null
let failures = 0

const pending = computed(() => tasks.value.filter((task) => !isTerminal(task)))

function merge(incoming: AsyncTask): void {
  const index = tasks.value.findIndex((task) => task.task_id === incoming.task_id)
  if (index === -1) {
    tasks.value = [...tasks.value, incoming]
  } else {
    const next = [...tasks.value]
    next[index] = incoming
    tasks.value = next
  }
}

async function refresh(): Promise<void> {
  for (const task of pending.value) {
    try {
      merge(await endpoint.status(task.task_id))
      failures = 0
    } catch (failure) {
      failures += 1
      // The task is *not* touched: leaving the last known status is the honest thing to do,
      // and pretending it finished because the request failed is exactly the bug this
      // component exists to avoid.
      problem.value =
        failures >= MAX_POLL_FAILURES
          ? '后台状态暂时查不到（服务不可达）。显示的是最后一次已知状态，不代表已完成。'
          : ''
      if (failures >= MAX_POLL_FAILURES) return
    }
  }
}

function schedule(): void {
  if (timer) clearTimeout(timer)
  if (!pending.value.length || failures >= MAX_POLL_FAILURES) {
    polling.value = false
    return
  }
  polling.value = true
  timer = setTimeout(async () => {
    await refresh()
    schedule()
  }, POLL_INTERVAL_MS)
}

async function load(): Promise<void> {
  if (!props.threadId) {
    tasks.value = []
    return
  }
  problem.value = ''
  failures = 0
  try {
    const response = await fetch(
      `/api/async-tasks?parent_thread_id=${encodeURIComponent(props.threadId)}`,
      { credentials: 'same-origin' },
    )
    if (!response.ok) throw new Error(`HTTP ${response.status}`)
    tasks.value = ((await response.json()).data.items ?? []) as AsyncTask[]
  } catch (failure) {
    problem.value = `读取后台任务失败：${(failure as Error).message}`
    tasks.value = []
  }
  schedule()
}

async function cancel(task: AsyncTask): Promise<void> {
  try {
    merge(await endpoint.cancel(task.task_id, `ui-cancel-${task.task_id}`))
  } catch (failure) {
    problem.value = `取消失败：${(failure as Error).message}`
  }
  schedule()
}

watch(() => props.threadId, load, { immediate: true })
onBeforeUnmount(() => {
  if (timer) clearTimeout(timer)
})

defineExpose({ refresh, load })
</script>

<template>
  <section v-if="threadId" class="async-tasks" aria-label="后台任务">
    <header>
      <h2>后台分析</h2>
      <span v-if="polling" class="hint">轮询中…</span>
    </header>

    <p v-if="problem" class="problem" role="status">{{ problem }}</p>

    <p v-if="!tasks.length" class="empty">当前会话没有后台任务。</p>

    <ul v-else>
      <li v-for="task in tasks" :key="task.task_id">
        <div class="row">
          <span class="status" :data-status="task.status">
            {{ STATUS_LABELS[task.status] ?? task.status }}
          </span>
          <code>{{ task.task_id.slice(0, 12) }}</code>
          <button
            v-if="!isTerminal(task)"
            type="button"
            class="cancel"
            @click="cancel(task)"
          >
            取消
          </button>
        </div>
        <p class="instruction">{{ task.instruction }}</p>
        <p class="detail">{{ describeTask(task) }}</p>
        <ul v-if="task.artifact_ids.length" class="artifacts">
          <li v-for="artifactId in task.artifact_ids" :key="artifactId">
            <a :href="`/api/artifacts/${artifactId}/content`">下载 {{ artifactId.slice(0, 12) }}</a>
          </li>
        </ul>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.async-tasks {
  border-top: 1px solid var(--border, #e5e7eb);
  padding: 0.75rem 1rem;
  font-size: 0.875rem;
}
.async-tasks header {
  align-items: baseline;
  display: flex;
  gap: 0.5rem;
  justify-content: space-between;
}
.async-tasks h2 {
  font-size: 0.9rem;
  margin: 0;
}
.hint,
.empty {
  color: #6b7280;
}
.problem {
  color: #b45309;
}
.row {
  align-items: center;
  display: flex;
  gap: 0.5rem;
}
.status[data-status='completed'] {
  color: #15803d;
}
.status[data-status='failed'],
.status[data-status='lost'] {
  color: #b91c1c;
}
.status[data-status='cancelled'] {
  color: #6b7280;
}
.instruction {
  margin: 0.25rem 0;
}
.detail {
  color: #4b5563;
  margin: 0;
}
.artifacts {
  list-style: none;
  padding-left: 0;
}
button.cancel {
  margin-left: auto;
}
</style>
