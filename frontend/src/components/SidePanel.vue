<script setup lang="ts">
import { AlertTriangle, Check, X } from 'lucide-vue-next'
import { computed, ref } from 'vue'

import type { ChatSnapshot, TodoItem } from '../state/chat'
import { canDecide, canSupplement } from '../state/chat'

const props = defineProps<{
  snapshot: ChatSnapshot
  busy: boolean
}>()

const emit = defineEmits<{
  (event: 'approve'): void
  (event: 'reject'): void
  (event: 'supplement', text: string): void
}>()

const supplementText = ref('')

const supplement = computed(() =>
  props.snapshot.interrupt?.candidates.find((item) => item.type === 'supplement'),
)
const missingFields = computed<string[]>(() => {
  const fields = supplement.value?.missing_fields
  return Array.isArray(fields) ? fields.map((field) => String(field)) : []
})

const approval = computed(() =>
  props.snapshot.interrupt?.candidates.find((item) => item.type === 'approval'),
)
const decisions = computed<string[]>(() => {
  const item = props.snapshot.interrupt?.candidates.find((entry) => entry.type === 'decisions')
  const allowed = item?.allowed
  return Array.isArray(allowed) ? allowed.map((value) => String(value)) : []
})

const lines = computed(() => {
  const raw = approval.value?.arguments
  const arguments_ = raw && typeof raw === 'object' ? (raw as Record<string, unknown>) : {}
  const entries = arguments_.lines
  return Array.isArray(entries) ? (entries as Array<Record<string, unknown>>) : []
})

const canApprove = computed(() => decisions.value.includes('approve'))
const canReject = computed(() => decisions.value.includes('reject'))
const showControls = computed(() => canDecide(props.snapshot) || canSupplement(props.snapshot))

const todoIcon: Record<TodoItem['status'], string> = {
  pending: '○',
  in_progress: '◐',
  completed: '●',
}

function submitSupplement(): void {
  const text = supplementText.value.trim()
  if (!text) return
  emit('supplement', text)
  supplementText.value = ''
}
</script>

<template>
  <section class="panel" aria-label="待办与审批">
    <div class="panel__block">
      <h2>待办</h2>
      <p v-if="props.snapshot.todos.length === 0" class="muted">暂无待办。</p>
      <ul v-else class="todos">
        <li v-for="(todo, index) in props.snapshot.todos" :key="index" :data-status="todo.status">
          <span class="todos__mark" aria-hidden="true">{{ todoIcon[todo.status] }}</span>
          <span>{{ todo.content }}</span>
        </li>
      </ul>
    </div>

    <div v-if="props.snapshot.status === 'interrupted' && !showControls" class="panel__block">
      <h2>已暂停</h2>
      <p class="muted">运行停在一次中断上，但该中断类型本界面暂不支持处理。</p>
    </div>

    <div v-if="canSupplement(props.snapshot)" class="panel__block panel__block--attention">
      <h2>需要补充信息</h2>
      <p>{{ props.snapshot.interrupt?.prompt }}</p>
      <ul v-if="missingFields.length" class="chips">
        <li v-for="field in missingFields" :key="field"><code>{{ field }}</code></li>
      </ul>
      <p class="muted">请只填写你确定的值；不确定的项请明确说明，助手不会替你猜。</p>
      <textarea
        v-model="supplementText"
        rows="4"
        aria-label="补充信息"
        placeholder="例如：P001 数量 50，单价 25.50"
      />
      <div class="actions">
        <button
          type="button"
          class="primary"
          :disabled="props.busy || supplementText.trim().length === 0"
          @click="submitSupplement"
        >
          <Check :size="15" /> 提交补充
        </button>
      </div>
    </div>

    <div v-if="canDecide(props.snapshot)" class="panel__block panel__block--attention">
      <h2>等待审批</h2>
      <p>{{ props.snapshot.interrupt?.prompt }}</p>

      <dl class="review">
        <div><dt>操作</dt><dd><code>{{ approval?.tool_name }}</code></dd></div>
        <div><dt>目标</dt><dd><code>{{ approval?.target ?? '按操作类型决定' }}</code></dd></div>
      </dl>

      <table v-if="lines.length" class="lines">
        <thead>
          <tr><th>物料</th><th>数量</th><th>单价</th></tr>
        </thead>
        <tbody>
          <tr v-for="(line, index) in lines" :key="index">
            <td>{{ line.part_id }}</td>
            <td>{{ line.quantity }}</td>
            <td>{{ line.unit_price }}</td>
          </tr>
        </tbody>
      </table>

      <p class="muted">
        批准后内容即被冻结；若要改动物料、数量或单价，必须重新审批。
      </p>

      <div class="actions">
        <button
          type="button"
          class="primary"
          :disabled="props.busy || !canApprove"
          @click="emit('approve')"
        >
          <Check :size="15" /> 批准
        </button>
        <button
          type="button"
          class="danger"
          :disabled="props.busy || !canReject"
          @click="emit('reject')"
        >
          <X :size="15" /> 拒绝
        </button>
      </div>
    </div>

    <div v-if="props.snapshot.error" class="panel__block panel__block--error" role="alert">
      <h2><AlertTriangle :size="16" /> 出错了</h2>
      <p><code>{{ props.snapshot.error.code }}</code> {{ props.snapshot.error.message }}</p>
      <p class="muted">
        显示的是实际状态，不代表刚才的操作被撤销。请刷新查看服务端的真实进度。
      </p>
    </div>
  </section>
</template>
