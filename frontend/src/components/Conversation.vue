<script setup lang="ts">
import { ChevronDown, ChevronRight } from 'lucide-vue-next'
import { computed, ref } from 'vue'

import { renderMarkdown } from '../markdown'
import type { ChatSnapshot, ToolCallState } from '../state/chat'

const props = defineProps<{
  snapshot: ChatSnapshot
}>()

const expanded = ref<Record<string, boolean>>({})

function toggle(id: string): void {
  expanded.value[id] = !expanded.value[id]
}

// The trace is the tool calls in the order they started; a Map already preserves that.
const trace = computed(() => props.snapshot.toolCalls)

function argsPreview(call: ToolCallState): string {
  return call.argsText.length > 160 ? `${call.argsText.slice(0, 160)}…` : call.argsText
}

function resultPreview(call: ToolCallState): string {
  if (!call.result) return '（等待结果）'
  if (!call.result.ok && call.result.error) {
    return `${call.result.error.code}: ${call.result.error.message}`
  }
  try {
    return JSON.stringify(call.result.data ?? null)
  } catch {
    return '（结果无法显示）'
  }
}

/** Rendered markdown is escaped before formatting, so binding it is safe. */
function body(content: string): string {
  return renderMarkdown(content)
}
</script>

<template>
  <div class="conversation">
    <p v-if="props.snapshot.messages.length === 0" class="muted empty">
      问点什么吧，例如“哪些物料需要补货？”
    </p>

    <article
      v-for="message in props.snapshot.messages"
      :key="message.id"
      class="bubble"
      :class="`bubble--${message.role}`"
      :data-role="message.role"
      :data-streaming="message.streaming ? 'true' : 'false'"
    >
      <header class="bubble__head">
        <span class="bubble__who">{{ message.role === 'user' ? '你' : '助手' }}</span>
        <span v-if="message.streaming" class="bubble__streaming">生成中…</span>
      </header>
      <!-- eslint-disable-next-line vue/no-v-html -- input is escaped before formatting -->
      <div class="bubble__body" v-html="body(message.content)" />
    </article>

    <section v-if="trace.length" class="trace" aria-label="工具轨迹">
      <h2>工具轨迹</h2>
      <ol>
        <li v-for="call in trace" :key="call.id" :data-status="call.status">
          <button class="trace__head" type="button" @click="toggle(call.id)">
            <component :is="expanded[call.id] ? ChevronDown : ChevronRight" :size="14" />
            <code>{{ call.name }}</code>
            <span class="badge" :data-status="call.status">{{ call.status }}</span>
            <span class="muted">{{ call.source }}</span>
          </button>
          <div v-if="expanded[call.id]" class="trace__detail">
            <p class="trace__label">参数</p>
            <pre>{{ argsPreview(call) || '（无参数）' }}</pre>
            <p class="trace__label">结果</p>
            <pre>{{ resultPreview(call) }}</pre>
          </div>
        </li>
      </ol>
    </section>
  </div>
</template>
