import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import PlanningGoal from './PlanningGoal.vue'
import { api, type PlanningGoalState } from '../api/client'

const problem = { goal_id: 'planning-test', revision: 1, budget: '2500.00', currency: 'CNY',
  demands: [{ part_id: 'P001', quantity: 42, max_lead_days: 3, required: true,
    priority: 0, allow_partial: false, allow_supplier_split: false }] }
const state: PlanningGoalState = { problem, orders: [], execution_state: null }

beforeEach(() => {
  vi.spyOn(api, 'createPlanningGoal').mockResolvedValue(problem)
  vi.spyOn(api, 'planningGoal').mockResolvedValue(state)
  vi.spyOn(api, 'revisePlanningGoal').mockResolvedValue({ ...state,
    problem: { ...problem, revision: 2, budget: '2200.00' },
    revision_change: { affected_parts: ['P001'], global_allocation_changed: true },
  })
})

describe('same-thread goal revision', () => {
  async function revisionForm() {
    const wrapper = mount(PlanningGoal, { props: { threadId: 'planning-test', busy: false, refresh: 0 } })
    await flushPromises()
    await wrapper.findAll('button').find(b => b.text() === '修改当前目标')!.trigger('click')
    return wrapper
  }

  it('sends current revision, changed constraints and only selected source parts', async () => {
    const wrapper = await revisionForm()
    expect(wrapper.get('[aria-label="物料编号 1"]').attributes('disabled')).toBeDefined()
    await wrapper.get('[aria-label="采购预算"]').setValue('2200.00')
    await wrapper.get('[aria-label="最长交期 1"]').setValue(4)
    await wrapper.get('[aria-label="刷新来源 P001"]').setValue(true)
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(api.revisePlanningGoal).toHaveBeenCalledWith('planning-test', {
      expected_revision: 1, budget: '2200.00', refresh_part_ids: ['P001'],
      demands: [{ ...problem.demands[0], max_lead_days: 4 }],
    })
    expect(api.createPlanningGoal).not.toHaveBeenCalled()
    expect(wrapper.emitted('revised')).toEqual([['planning-test']])
    expect(wrapper.text()).toContain('第 2 版')
    expect(wrapper.text()).toContain('需重新检查：P001')
    await wrapper.findAll('button').find(b => b.text() === '继续规划')!.trigger('click')
    expect(wrapper.emitted('continue')).toEqual([['planning-test']])
  })

  it('preserves committed orders and leaves unchanged sources unrequested', async () => {
    vi.mocked(api.revisePlanningGoal).mockResolvedValue({ ...state,
      problem: { ...problem, revision: 2 }, orders: [
        { interrupt_id: 'committed', revision: 1, result: { ok: true, data: { order_id: 'O-existing' } } },
      ],
    })
    const wrapper = await revisionForm()
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(vi.mocked(api.revisePlanningGoal).mock.calls[0]![1].refresh_part_ids).toEqual([])
    expect(wrapper.text()).toContain('O-existing')
  })

  it('preserves edit inputs on conflict, without retry or announcing success', async () => {
    vi.mocked(api.revisePlanningGoal).mockRejectedValue(new Error('REVISION_CONFLICT: reload the current goal'))
    const wrapper = await revisionForm()
    await wrapper.get('[aria-label="采购预算"]').setValue('2200.00')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(api.revisePlanningGoal).toHaveBeenCalledTimes(1)
    expect(wrapper.get('[aria-label="采购预算"]').element).toHaveProperty('value', '2200.00')
    expect(wrapper.emitted('revised')).toBeUndefined()
    expect(wrapper.text()).toContain('第 1 版')
    expect(wrapper.get('[role="alert"]').text()).toContain('REVISION_CONFLICT')
  })

  it.each(['inflight', 'uncertain'])('disables revision while execution is %s', async phase => {
    vi.mocked(api.planningGoal).mockResolvedValue({ ...state, execution_state: phase })
    const wrapper = mount(PlanningGoal, { props: { threadId: 'planning-test', busy: false, refresh: 0 } })
    await flushPromises()
    const button = wrapper.findAll('button').find(b => b.text() === '修改当前目标')!
    expect(button.attributes('disabled')).toBeDefined()
    await button.trigger('click')
    expect(wrapper.find('form').exists()).toBe(false)
  })
})
afterEach(() => vi.restoreAllMocks())

async function form() {
  const wrapper = mount(PlanningGoal, { props: { threadId: null, busy: false, refresh: 0 } })
  await wrapper.find('button').trigger('click')
  await wrapper.get('[aria-label="采购预算"]').setValue('2500.00')
  await wrapper.get('[aria-label="物料编号 1"]').setValue('P001')
  await wrapper.get('[aria-label="数量 1"]').setValue(42)
  await wrapper.get('[aria-label="最长交期 1"]').setValue(3)
  return wrapper
}

describe('planning goal input', () => {
  it('creates a fresh goal with string money and trusted-server fields only, without streaming', async () => {
    const stream = vi.spyOn(api, 'stream')
    const wrapper = await form()
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    const [thread, body] = vi.mocked(api.createPlanningGoal).mock.calls[0]!
    expect(thread).toMatch(/^planning-/)
    expect(body).toEqual({ budget: '2500.00', demands: problem.demands })
    expect(wrapper.emitted('created')).toEqual([[thread]])
    expect(stream).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('尚无已创建订单')
  })

  it.each(['2.5', '-1.00', '1e3', '2500.001'])('rejects invalid amount %s before a request', async amount => {
    const wrapper = await form()
    await wrapper.get('[aria-label="采购预算"]').setValue(amount)
    await wrapper.get('form').trigger('submit')
    expect(api.createPlanningGoal).not.toHaveBeenCalled()
    expect(wrapper.get('[role="alert"]').text()).toContain('两位小数')
  })

  it('rejects fractional quantities and preserves the form', async () => {
    const wrapper = await form()
    await wrapper.get('[aria-label="数量 1"]').setValue(1.5)
    await wrapper.get('form').trigger('submit')
    expect(api.createPlanningGoal).not.toHaveBeenCalled()
    expect(wrapper.get('[role="alert"]').text()).toContain('正整数')
  })

  it('rejects repeated material requirements', async () => {
    const wrapper = await form()
    await wrapper.findAll('button').find(b => b.text() === '添加物料')!.trigger('click')
    await wrapper.get('[aria-label="物料编号 2"]').setValue(' P001 ')
    await wrapper.get('form').trigger('submit')
    expect(api.createPlanningGoal).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('只能填写一项')
  })

  it('supports optional partial quantities and resets partial when required', async () => {
    const wrapper = await form()
    await wrapper.get('[aria-label="必需 1"]').setValue(false)
    await wrapper.get('[aria-label="优先级 1"]').setValue(2)
    await wrapper.get('[aria-label="部分采购 1"]').setValue(true)
    await wrapper.get('[aria-label="供应商拆分 1"]').setValue(true)
    await wrapper.get('[aria-label="必需 1"]').setValue(true)
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(vi.mocked(api.createPlanningGoal).mock.calls[0]![1].demands[0]).toMatchObject({
      required: true, allow_partial: false, allow_supplier_split: true,
    })
  })

  it('shows backend rejection and retains entered constraints for correction', async () => {
    vi.mocked(api.createPlanningGoal).mockRejectedValue(new Error('ERP source unavailable'))
    const wrapper = await form()
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('ERP source unavailable')
    expect((wrapper.get('[aria-label="物料编号 1"]').element as HTMLInputElement).value).toBe('P001')
    expect(wrapper.emitted('created')).toBeUndefined()
  })

  it('uses saved server constraints and orders after refresh, without optimistically adding orders', async () => {
    const wrapper = mount(PlanningGoal, { props: { threadId: 't', busy: false, refresh: 0 } })
    await flushPromises()
    expect(wrapper.text()).toContain('42 件，3 天内')
    expect(wrapper.text()).toContain('已创建订单（0）')
    vi.mocked(api.planningGoal).mockResolvedValue({ ...state, execution_state: 'uncertain', orders: [
      { interrupt_id: 'a', revision: 1, result: { ok: true, data: { order_id: 'O-real-1', supplier_id: 'S001' } } },
    ] })
    await wrapper.setProps({ refresh: 1 })
    await flushPromises()
    expect(wrapper.text()).toContain('O-real-1')
    expect(wrapper.text()).toContain('已创建订单（1）')
    expect(wrapper.text()).toContain('订单执行结果待核对')
  })

  it('does not leak a slow previous thread response into the new conversation', async () => {
    let resolve!: (value: PlanningGoalState) => void
    vi.mocked(api.planningGoal).mockImplementationOnce(() => new Promise(r => { resolve = r }))
    const wrapper = mount(PlanningGoal, { props: { threadId: 'previous', busy: false, refresh: 0 } })
    await wrapper.setProps({ threadId: null })
    resolve(state)
    await flushPromises()
    expect(wrapper.text()).not.toContain('2500.00')
  })

  it('prevents goal creation while a run is active', async () => {
    const wrapper = await form()
    await wrapper.setProps({ busy: true })
    await wrapper.get('form').trigger('submit')
    expect(api.createPlanningGoal).not.toHaveBeenCalled()
    expect(wrapper.get('[type="submit"]').attributes('disabled')).toBeDefined()
  })

  it('retains the last known goal when refreshing orders fails and shows the error', async () => {
    const wrapper = mount(PlanningGoal, { props: { threadId: 't', busy: false, refresh: 0 } })
    await flushPromises()
    vi.mocked(api.planningGoal).mockRejectedValue(new Error('状态暂时无法查询'))
    await wrapper.setProps({ refresh: 1 })
    await flushPromises()
    expect(wrapper.text()).toContain('42 件，3 天内')
    expect(wrapper.get('[role="alert"]').text()).toContain('状态暂时无法查询')
  })
})
