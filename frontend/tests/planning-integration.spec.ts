import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import App from '../src/App.vue'
import PlanningGoal from '../src/components/PlanningGoal.vue'
import { api, type SseFrame } from '../src/api/client'

beforeEach(() => {
  vi.spyOn(api, 'session').mockResolvedValue({ user_id: 'demo-a', note: '' })
  vi.spyOn(api, 'listThreads').mockResolvedValue({ items: [], total: 0, page: 1, page_size: 20 })
  vi.spyOn(api, 'planningGoal').mockRejectedValue(Object.assign(new Error('course thread'), { status: 404 }))
})
afterEach(() => vi.restoreAllMocks())

it('binds the created planning conversation to the regular chat stream and clears it on account switch', async () => {
  const stream = vi.spyOn(api, 'stream').mockImplementation(async function* (): AsyncGenerator<SseFrame> {
    yield { event: 'done', id: 'r:1', data: JSON.stringify({ v: 1, thread_id: 'planning-new', run_id: 'r',
      seq: 1, source: 'main', payload: { status: 'completed' } }) }
  })
  const wrapper = mount(App)
  await flushPromises()
  wrapper.findComponent(PlanningGoal).vm.$emit('created', 'planning-new')
  await flushPromises()
  expect(stream).not.toHaveBeenCalled()
  expect(wrapper.findComponent(PlanningGoal).props('threadId')).toBe('planning-new')
  await wrapper.get('[aria-label="输入消息"]').setValue('请按目标规划')
  await wrapper.findAll('button').find(b => b.text().trim() === '发送')!.trigger('click')
  await flushPromises()
  expect(stream.mock.calls[0]![0]).toMatchObject({ thread_id: 'planning-new', message: '请按目标规划' })
  expect(wrapper.findComponent(PlanningGoal).props('refresh')).toBe(1)
  await wrapper.findAll('.chip').find(b => b.text() === 'demo-b')!.trigger('click')
  await flushPromises()
  expect(wrapper.findComponent(PlanningGoal).props('threadId')).toBeNull()
})
