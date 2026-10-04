<script setup lang="ts">
import { computed, ref, watch, onBeforeUnmount } from 'vue'
import { api, newRequestId, type PlanningDemand, type PlanningGoalState } from '../api/client'

const props = defineProps<{ threadId: string | null; busy: boolean; refresh: number }>()
const emit = defineEmits<{
  (event: 'created', threadId: string): void
  (event: 'revised', threadId: string): void
  (event: 'continue', threadId: string): void
}>()
const editing = ref(false)
const revising = ref(false)
const editingRevision = ref(0)
const refreshParts = ref<string[]>([])
const revised = ref(false)
const reload = ref(0)
const saving = ref(false)
const loading = ref(false)
const error = ref('')
const goal = ref<PlanningGoalState | null>(null)
const budget = ref('')
const demands = ref<PlanningDemand[]>([emptyDemand()])
let epoch = 0
const locked = computed(() => props.busy || saving.value || loading.value || !!goal.value?.execution_state)

function openCreate(): void {
  if (locked.value) return
  revising.value = false
  editing.value = !editing.value
  error.value = ''
}

function openRevision(): void {
  if (!goal.value || locked.value) return
  budget.value = goal.value.problem.budget
  demands.value = goal.value.problem.demands.map(d => ({ ...d }))
  editingRevision.value = goal.value.problem.revision
  refreshParts.value = []
  revising.value = true
  editing.value = true
  error.value = ''
}

function continueCurrent(): void {
  if (props.threadId) emit('continue', props.threadId)
}

function emptyDemand(): PlanningDemand {
  return { part_id: '', quantity: 1, max_lead_days: 7, required: true,
    priority: 0, allow_partial: false, allow_supplier_split: false }
}

function validate(): string {
  if (!/^\d+\.\d{2}$/.test(budget.value.trim())) return '预算请填写非负金额，并保留两位小数。'
  const ids = new Set<string>()
  for (const d of demands.value) {
    if (!d.part_id.trim()) return '请填写每项物料编号。'
    if (ids.has(d.part_id.trim())) return '同一物料只能填写一项需求。'
    ids.add(d.part_id.trim())
    if (!Number.isSafeInteger(d.quantity) || d.quantity <= 0) return '需求数量必须是正整数。'
    if (!Number.isSafeInteger(d.max_lead_days) || d.max_lead_days < 0) return '最长交期必须是非负整数。'
    if (!Number.isSafeInteger(d.priority) || d.priority < 0) return '优先级必须是非负整数。'
    if (d.required && d.allow_partial) return '必需物料必须满足全量，不能允许部分采购。'
  }
  return demands.value.length ? '' : '至少添加一项物料需求。'
}

async function save(): Promise<void> {
  if (locked.value) return
  error.value = validate()
  if (error.value) return
  const generation = ++epoch
  saving.value = true
  const threadId = revising.value ? props.threadId : `planning-${newRequestId()}`
  if (!threadId) { saving.value = false; return }
  try {
    const body = {
      budget: budget.value.trim(),
      demands: demands.value.map(d => ({ ...d, part_id: d.part_id.trim() })),
    }
    if (revising.value) {
      const result = await api.revisePlanningGoal(threadId, {
        ...body, expected_revision: editingRevision.value, refresh_part_ids: [...refreshParts.value],
      })
      if (generation !== epoch) return
      goal.value = { ...result, execution_state: null }
      revised.value = result.problem.revision !== editingRevision.value
      if (revised.value) emit('revised', threadId)
    } else {
      const problem = await api.createPlanningGoal(threadId, body)
      if (generation !== epoch) return
      goal.value = { problem, orders: [], execution_state: null }
      revised.value = false
      emit('created', threadId)
    }
    editing.value = false
  } catch (failure) {
    if (generation === epoch) error.value = failure instanceof Error ? failure.message : String(failure)
  } finally {
    if (generation === epoch) saving.value = false
  }
}

watch(() => [props.threadId, props.refresh, reload.value] as const, async (value, previous) => {
  const generation = ++epoch
  saving.value = false
  error.value = ''
  if (value[0] !== previous?.[0]) {
    goal.value = null
    editing.value = false
    revised.value = false
  }
  if (!props.threadId) { loading.value = false; return }
  loading.value = true
  try {
    const result = await api.planningGoal(props.threadId)
    if (generation === epoch) goal.value = result
  } catch (failure) {
    if (generation === epoch && (failure as { status?: number }).status !== 404) {
      error.value = failure instanceof Error ? failure.message : String(failure)
    }
  } finally {
    if (generation === epoch) loading.value = false
  }
}, { immediate: true })

onBeforeUnmount(() => { epoch += 1 })
</script>

<template>
  <section class="planning-goal" aria-label="采购规划目标">
    <div class="actions">
      <button type="button" :disabled="locked" @click="openCreate">
        {{ editing ? '收起目标表单' : '新建采购规划' }}
      </button>
      <button v-if="goal" type="button" :disabled="locked" @click="openRevision">修改当前目标</button>
      <button v-if="goal" type="button" :disabled="locked" @click="reload += 1">重新读取目标</button>
      <span v-if="loading" class="muted">正在查询规划目标…</span>
    </div>
    <form v-if="editing" @submit.prevent="save">
      <p v-if="revising">修改第 {{ editingRevision }} 版目标。已创建订单保留并计入预算；目标变化后旧审批失效，新候选需重新批准。</p>
      <p v-else>保存后将创建独立规划会话。请在消息框说明目标，候选订单仍需逐笔批准。</p>
      <label>采购预算（CNY）<input v-model="budget" aria-label="采购预算" placeholder="2500.00" :disabled="locked" /></label>
      <fieldset v-for="(d, i) in demands" :key="i" :disabled="locked">
        <legend>需求 {{ i + 1 }}</legend>
        <label>物料编号<input v-model="d.part_id" :aria-label="`物料编号 ${i + 1}`" placeholder="P001" :disabled="revising" /></label>
        <label>数量<input v-model.number="d.quantity" type="number" min="1" step="1" :aria-label="`数量 ${i + 1}`" /></label>
        <label>最长交期（天）<input v-model.number="d.max_lead_days" type="number" min="0" step="1" :aria-label="`最长交期 ${i + 1}`" /></label>
        <label><input v-model="d.required" type="checkbox" :aria-label="`必需 ${i + 1}`" @change="d.required && (d.allow_partial = false)" />必需（全量满足）</label>
        <label v-if="!d.required">可选优先级（越小越优先）<input v-model.number="d.priority" type="number" min="0" step="1" :aria-label="`优先级 ${i + 1}`" /></label>
        <label v-if="!d.required"><input v-model="d.allow_partial" type="checkbox" :aria-label="`部分采购 ${i + 1}`" />允许部分采购</label>
        <label><input v-model="d.allow_supplier_split" type="checkbox" :aria-label="`供应商拆分 ${i + 1}`" />同物料允许多个供应商供货</label>
        <label v-if="revising"><input v-model="refreshParts" :value="d.part_id" type="checkbox" :aria-label="`刷新来源 ${d.part_id}`" />重新核对该物料报价与交期</label>
        <button v-if="!revising" type="button" :disabled="demands.length === 1" :aria-label="`删除需求 ${i + 1}`" @click="demands.splice(i, 1)">删除需求</button>
      </fieldset>
      <div class="actions">
        <button v-if="!revising" type="button" :disabled="locked" @click="demands.push(emptyDemand())">添加物料</button>
        <button type="submit" class="primary" :disabled="locked">{{ saving ? '正在保存…' : revising ? '保存目标修改' : '保存并进入规划会话' }}</button>
        <button type="button" :disabled="saving" @click="editing = false">取消修改</button>
      </div>
    </form>
    <p v-if="error" class="notice" role="alert">{{ error }}</p>
    <div v-if="goal" class="planning-summary">
      <h2>采购规划 · 第 {{ goal.problem.revision }} 版</h2>
      <p>预算 {{ goal.problem.budget }} {{ goal.problem.currency }}</p>
      <p v-if="goal.revision_change">需重新检查：{{ goal.revision_change.affected_parts.join('、') }}{{ goal.revision_change.global_allocation_changed ? '；重新比较预算内的分配。' : '。' }}</p>
      <div v-if="revised" role="status">
        <p>目标修改已保存。旧候选需要重新计算和审批，已创建订单不会重复采购。</p>
        <button type="button" class="primary" :disabled="locked" @click="continueCurrent">继续规划</button>
      </div>
      <ul>
        <li v-for="d in goal.problem.demands" :key="d.part_id">
          {{ d.part_id }}：{{ d.quantity }} 件，{{ d.max_lead_days }} 天内，
          {{ d.required ? '必需全量' : `可选 · 优先级 ${d.priority}` }}
          {{ d.allow_partial ? ' · 可部分采购' : '' }}{{ d.allow_supplier_split ? ' · 可分供应商' : '' }}
        </li>
      </ul>
      <p v-if="goal.execution_state === 'uncertain'" role="status">订单执行结果待核对，请先确认是否已写入。</p>
      <p v-else-if="goal.execution_state" role="status">订单正在执行，请等待实际订单结果。</p>
      <h3>已创建订单（{{ goal.orders.length }}）</h3>
      <p v-if="!goal.orders.length" class="muted">尚无已创建订单。</p>
      <ul v-else>
        <li v-for="order in goal.orders" :key="order.interrupt_id">
          {{ order.result.data.order_id }}
          <span v-if="order.result.data.supplier_id"> · {{ order.result.data.supplier_id }}</span>
          <span v-if="order.result.data.total_amount"> · {{ order.result.data.total_amount }} CNY</span>
        </li>
      </ul>
    </div>
  </section>
</template>
