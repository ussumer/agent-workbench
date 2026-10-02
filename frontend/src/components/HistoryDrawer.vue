<script setup lang="ts">
import { Trash2, X } from 'lucide-vue-next'
import { ref } from 'vue'

import type { ThreadSummary } from '../api/client'

const props = defineProps<{
  threads: ThreadSummary[]
  activeThreadId: string | null
  loading: boolean
  open: boolean
}>()

const emit = defineEmits<{
  (event: 'select', threadId: string): void
  (event: 'delete', threadId: string): void
  (event: 'close'): void
}>()

// Deleting is confirmed inline rather than with a browser dialog, so the confirmation is
// part of the page and can be asserted in a DOM test.
const confirming = ref<string | null>(null)

function askDelete(threadId: string): void {
  confirming.value = threadId
}

function confirmDelete(threadId: string): void {
  confirming.value = null
  emit('delete', threadId)
}

function shortId(id: string): string {
  return id.length > 12 ? `${id.slice(0, 8)}…` : id
}
</script>

<template>
  <aside class="drawer" :class="{ 'drawer--open': props.open }" aria-label="历史会话">
    <header class="drawer__head">
      <h2>历史</h2>
      <button class="icon-button" type="button" aria-label="收起历史" @click="emit('close')">
        <X :size="16" />
      </button>
    </header>

    <p v-if="props.loading" class="muted">加载中…</p>
    <p v-else-if="props.threads.length === 0" class="muted">还没有会话，开始提问即可创建。</p>

    <ul class="drawer__list">
      <li
        v-for="thread in props.threads"
        :key="thread.thread_id"
        :class="{ 'is-active': thread.thread_id === props.activeThreadId }"
      >
        <button class="drawer__item" type="button" @click="emit('select', thread.thread_id)">
          <span class="drawer__title">{{ thread.title || '未命名会话' }}</span>
          <span class="drawer__meta">
            <code>{{ shortId(thread.thread_id) }}</code>
            <span class="badge" :data-status="thread.status">{{ thread.status }}</span>
            <span class="muted">{{ thread.message_count }} 条</span>
          </span>
        </button>

        <div v-if="confirming === thread.thread_id" class="drawer__confirm">
          <span>删除该会话？</span>
          <button type="button" class="danger" @click="confirmDelete(thread.thread_id)">确认</button>
          <button type="button" @click="confirming = null">取消</button>
        </div>
        <button
          v-else
          class="icon-button drawer__delete"
          type="button"
          :aria-label="`删除会话 ${shortId(thread.thread_id)}`"
          @click="askDelete(thread.thread_id)"
        >
          <Trash2 :size="14" />
        </button>
      </li>
    </ul>

    <p class="drawer__note muted">删除会话只移除对话与检查点，保留你的技能、偏好与订单。</p>
  </aside>
</template>
