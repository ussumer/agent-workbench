<script setup lang="ts">
import { Menu, Send, User, XCircle } from 'lucide-vue-next'
import { computed, onMounted, reactive, ref, shallowRef } from 'vue'

import AsyncTasks from './components/AsyncTasks.vue'
import Conversation from './components/Conversation.vue'
import HistoryDrawer from './components/HistoryDrawer.vue'
import PlanningGoal from './components/PlanningGoal.vue'
import SidePanel from './components/SidePanel.vue'
import { api, newRequestId, type ThreadSummary } from './api/client'
import { ChatMachine } from './state/chat'

const DEMO_USERS = ['demo-a', 'demo-b'] as const

const machine = shallowRef(reactive(new ChatMachine()))
const snapshot = computed(() => machine.value.snapshot())

const currentUser = ref<string | null>(null)
const threads = ref<ThreadSummary[]>([])
const loadingThreads = ref(false)
const drawerOpen = ref(false)
const draft = ref('')
const busy = ref(false)
const notice = ref('')
const planningRefresh = ref(0)

function planningCreated(threadId: string): void {
  machine.value.resetForAccountSwitch()
  machine.value.threadId = threadId
  draft.value = ''
  notice.value = '规划目标已保存，请输入目标说明开始规划。'
  void refreshThreads()
}

function planningRevised(threadId: string): void {
  if (machine.value.threadId !== threadId) return
  machine.value.interrupt = null
  machine.value.error = null
  machine.value.status = 'idle'
  draft.value = '请按修订后的目标继续规划。读取当前版本与受影响物料，复用已保存的计算数据，只重算受影响部分；已创建订单计入预算且不要重复采购，新候选逐单等待审批。'
  notice.value = '目标已修改，旧审批已失效。点击继续规划生成新候选。'
}

async function continuePlanning(threadId: string): Promise<void> {
  if (busy.value || machine.value.threadId !== threadId) return
  planningRevised(threadId)
  await send()
}

const canSend = computed(() => !busy.value && draft.value.trim().length > 0)

onMounted(async () => {
  // Restore the session if the server still recognises the cookie; otherwise show the
  // account picker. A stale cookie is not an error, so it does not surface as one.
  try {
    await refreshThreads()
    currentUser.value = 'demo-a'
  } catch {
    currentUser.value = null
  }
})

async function switchUser(userId: string): Promise<void> {
  if (busy.value) return
  await api.session(userId)
  currentUser.value = userId
  // Switching accounts must clear what the previous one could see: the previous user's
  // threads and messages are not this user's data.
  machine.value.resetForAccountSwitch()
  notice.value = ''
  await refreshThreads()
}

function dismissSession(): void {
  if (busy.value) return
  currentUser.value = null
  machine.value.resetForAccountSwitch()
  threads.value = []
}

async function refreshThreads(): Promise<void> {
  loadingThreads.value = true
  try {
    const page = await api.listThreads()
    threads.value = page.items
  } catch (failure) {
    notice.value = messageOf(failure)
  } finally {
    loadingThreads.value = false
  }
}

async function openThread(threadId: string): Promise<void> {
  if (busy.value) return
  drawerOpen.value = false
  try {
    const detail = await api.threadDetail(threadId)
    machine.value.resetForAccountSwitch()
    machine.value.threadId = threadId
    machine.value.status = statusFrom(detail.status)
    machine.value.messages = detail.messages.map((message) => ({
      id: message.message_id,
      role: message.role === 'user' ? 'user' : 'assistant',
      content: message.content,
      streaming: false,
    }))
    machine.value.todos = detail.todos.map((todo) => ({
      content: todo.content,
      status: todo.status as 'pending' | 'in_progress' | 'completed',
    }))
    // A refresh after an interrupt must be able to continue the conversation, so the
    // pending interrupt comes from the server rather than from local memory.
    const pending = detail.pending_interrupts[0]
    machine.value.interrupt = pending
      ? {
          interruptId: pending.interrupt_id,
          interruptType: pending.interrupt_type as 'order_info_supplement' | 'hitl_approval',
          prompt: pending.prompt,
          candidates: pending.candidates,
        }
      : null
  } catch (failure) {
    notice.value = messageOf(failure)
  }
}

async function deleteThread(threadId: string): Promise<void> {
  try {
    await api.deleteThread(threadId)
    if (machine.value.threadId === threadId) machine.value.resetForAccountSwitch()
    await refreshThreads()
  } catch (failure) {
    notice.value = messageOf(failure)
  }
}

async function send(): Promise<void> {
  const text = draft.value.trim()
  if (!text || busy.value) return

  busy.value = true
  notice.value = ''
  draft.value = ''
  machine.value.beginUserTurn(machine.value.threadId, text, `user:${newRequestId()}`)
  const requestId = newRequestId()
  const threadId = machine.value.threadId

  try {
    const stream = api.stream({
      request_id: requestId,
      message: text,
      ...(threadId ? { thread_id: threadId } : {}),
    })
    await consume(stream)
  } catch (failure) {
    if (isConflict(failure)) {
      // A conflict means this request id (or this thread) already has a run. Re-reading
      // state is the correct response; retrying would be the wrong one.
      notice.value = `${messageOf(failure)}（已改为查询实际状态，未自动重发）`
      await reconcile()
    } else {
      machine.value.error = {
        code: (failure as { code?: string }).code ?? 'REQUEST_FAILED',
        message: messageOf(failure),
        retryable: false,
      }
      machine.value.status = 'failed'
    }
  } finally {
    busy.value = false
    await refreshThreads()
  }
}

async function decide(type: 'approve' | 'reject'): Promise<void> {
  const interrupt = machine.value.interrupt
  if (!interrupt || busy.value) return
  busy.value = true
  try {
    await consume(
      api.resume(machine.value.threadId as string, {
        request_id: newRequestId(),
        interrupt_id: interrupt.interruptId,
        resume: { decisions: [{ type }] },
      }),
    )
  } catch (failure) {
    notice.value = isConflict(failure)
      ? `${messageOf(failure)}（另一个标签页已经处理过这次审批）`
      : messageOf(failure)
    await reconcile()
  } finally {
    busy.value = false
    await refreshThreads()
  }
}

async function supplement(text: string): Promise<void> {
  const interrupt = machine.value.interrupt
  if (!interrupt || busy.value) return
  busy.value = true
  try {
    await consume(
      api.resume(machine.value.threadId as string, {
        request_id: newRequestId(),
        interrupt_id: interrupt.interruptId,
        resume: { supplement: text },
      }),
    )
  } catch (failure) {
    notice.value = messageOf(failure)
    await reconcile()
  } finally {
    busy.value = false
    await refreshThreads()
  }
}

async function cancelRun(): Promise<void> {
  const threadId = machine.value.threadId
  if (!threadId) return
  try {
    const result = await api.cancel(threadId)
    if (result.cancel_requested) machine.value.cancelRequested()
    notice.value = result.note
  } catch (failure) {
    notice.value = messageOf(failure)
  }
}

/** Apply a frame stream to the machine, and notice a stream that ended without `done`. */
async function consume(stream: AsyncGenerator<Parameters<typeof machine.value.applyFrame>[0]>) {
  for await (const frame of stream) {
    machine.value.applyFrame(frame)
  }
  machine.value.connectionEndedWithoutDone()
  planningRefresh.value += 1
}

/** Re-read the server's view after a disconnect or a conflict. Never resend blindly. */
async function reconcile(): Promise<void> {
  const threadId = machine.value.threadId
  if (!threadId) return
  try {
    const state = await api.threadState(threadId)
    machine.value.status = statusFrom(state.status)
    machine.value.todos = state.todos.map((todo) => ({
      content: todo.content,
      status: todo.status as 'pending' | 'in_progress' | 'completed',
    }))
    const pending = state.pending_interrupts[0]
    machine.value.interrupt = pending
      ? {
          interruptId: pending.interrupt_id,
          interruptType: pending.interrupt_type as 'order_info_supplement' | 'hitl_approval',
          prompt: pending.prompt,
          candidates: pending.candidates,
        }
      : null
  } catch (failure) {
    notice.value = messageOf(failure)
  }
}

function statusFrom(status: string) {
  const allowed = ['idle', 'running', 'interrupted', 'completed', 'failed', 'cancelled'] as const
  return (allowed as readonly string[]).includes(status)
    ? (status as (typeof allowed)[number])
    : 'idle'
}

function isConflict(failure: unknown): boolean {
  const status = (failure as { status?: number }).status
  return status === 409
}

function messageOf(failure: unknown): string {
  return failure instanceof Error ? failure.message : String(failure)
}
</script>

<template>
  <div class="app" :class="{ 'app--drawer-open': drawerOpen }">
    <header class="topbar">
      <button class="icon-button" type="button" aria-label="历史" @click="drawerOpen = !drawerOpen">
        <Menu :size="18" />
      </button>
      <h1>采购助手工作台</h1>

      <div class="accounts">
        <span class="accounts__label"><User :size="14" /> 演示身份</span>
        <button
          v-for="user in DEMO_USERS"
          :key="user"
          type="button"
          class="chip"
          :class="{ 'chip--active': currentUser === user }"
          :aria-pressed="currentUser === user"
          :disabled="busy"
          @click="switchUser(user)"
        >
          {{ user }}
        </button>
        <button
          v-if="currentUser"
          class="icon-button"
          type="button"
          aria-label="清除本地会话"
          :disabled="busy"
          @click="dismissSession"
        >
          <XCircle :size="15" />
        </button>
      </div>
    </header>

    <p v-if="!currentUser" class="gate">
      请选择一个演示身份开始。界面不会替你自动登录。
    </p>

    <main v-else class="workspace">
      <HistoryDrawer
        :threads="threads"
        :active-thread-id="machine.threadId"
        :loading="loadingThreads"
        :open="drawerOpen"
        @select="openThread"
        @delete="deleteThread"
        @close="drawerOpen = false"
      />

      <section class="chat">
        <PlanningGoal :key="currentUser" :thread-id="machine.threadId" :busy="busy" :refresh="planningRefresh" @created="planningCreated" @revised="planningRevised" @continue="continuePlanning" />
        <Conversation :snapshot="snapshot" />

        <AsyncTasks :thread-id="machine.threadId" />

        <footer class="composer">
          <textarea
            v-model="draft"
            rows="2"
            aria-label="输入消息"
            placeholder="例如：哪些物料需要补货？"
            :disabled="busy"
            @keydown.enter.exact.prevent="send"
          />
          <div class="composer__actions">
            <span class="muted">{{
              busy ? '正在运行…' : 'Enter 发送，Shift+Enter 换行'
            }}</span>
            <button
              v-if="machine.status === 'running'"
              type="button"
              :disabled="!machine.threadId"
              @click="cancelRun"
            >
              停止
            </button>
            <button type="button" class="primary" :disabled="!canSend" @click="send">
              <Send :size="15" /> 发送
            </button>
          </div>
          <p v-if="notice" class="notice" role="status">{{ notice }}</p>
        </footer>
      </section>

      <SidePanel :snapshot="snapshot" :busy="busy" @approve="decide('approve')" @reject="decide('reject')" @supplement="supplement" />
    </main>
  </div>
</template>
