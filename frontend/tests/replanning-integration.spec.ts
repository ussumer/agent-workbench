import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, expect, it, vi } from 'vitest'
import App from '../src/App.vue'
import PlanningGoal from '../src/components/PlanningGoal.vue'
import SidePanel from '../src/components/SidePanel.vue'
import { api, type SseFrame } from '../src/api/client'

afterEach(() => vi.restoreAllMocks())

it('retires the old approval, preserves history and continues on the same thread after revision', async () => {
  vi.spyOn(api, 'listThreads').mockResolvedValue({ items: [], total: 0, page: 1, page_size: 20 })
  vi.spyOn(api, 'planningGoal').mockResolvedValue({ problem: {
    goal_id: 'planning-test', revision: 1, budget: '2500.00', currency: 'CNY', demands: [],
  }, orders: [], execution_state: null })
  const stream = vi.spyOn(api, 'stream').mockImplementation(async function* (): AsyncGenerator<SseFrame> {
    yield { event: 'interrupt', data: JSON.stringify({ v: 1, thread_id: 'planning-test', run_id: 'r',
      seq: 1, source: 'main', payload: { interrupt_id: 'old-approval', interrupt_type: 'hitl_approval',
        prompt: '待审批旧订单', candidates: [] } }) }
    yield { event: 'done', data: JSON.stringify({ v: 1, thread_id: 'planning-test', run_id: 'r',
      seq: 2, source: 'main', payload: { status: 'interrupted' } }) }
  })
  const wrapper = mount(App)
  await flushPromises()
  const goal = wrapper.findComponent(PlanningGoal)
  goal.vm.$emit('created', 'planning-test')
  await flushPromises()
  await wrapper.get('[aria-label="输入消息"]').setValue('初次规划')
  await wrapper.findAll('button').find(b => b.text().trim() === '发送')!.trigger('click')
  await flushPromises()
  expect(wrapper.findComponent(SidePanel).props('snapshot').interrupt.interruptId).toBe('old-approval')
  goal.vm.$emit('revised', 'planning-test')
  await flushPromises()
  expect(wrapper.findComponent(SidePanel).props('snapshot').interrupt).toBeNull()
  expect(wrapper.findComponent(SidePanel).props('snapshot').messages.some((m: { content: string }) => m.content === '初次规划')).toBe(true)
  expect(stream).toHaveBeenCalledTimes(1)
  goal.vm.$emit('continue', 'planning-test')
  await flushPromises()
  expect(stream.mock.calls[1]![0]).toMatchObject({ thread_id: 'planning-test' })
  expect(stream.mock.calls[1]![0].message).toContain('复用已保存的计算数据')
  expect(stream.mock.calls[1]![0].message).toContain('不要重复采购')
})
