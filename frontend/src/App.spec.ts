import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import App from './App.vue'
import SidePanel from './components/SidePanel.vue'
import { ChatMachine } from './state/chat'

function machineWith(patch: (machine: ChatMachine) => void): ChatMachine {
  const machine = new ChatMachine()
  machine.beginUserTurn('thread-1', '下单 P001 50 件', 'user:1')
  patch(machine)
  return machine
}

describe('App shell', () => {
  it('renders the workspace heading', () => {
    const wrapper = mount(App)
    expect(wrapper.get('h1').text()).toBe('采购助手工作台')
  })

  it('offers both demo identities and does not pick one on its own', async () => {
    const wrapper = mount(App)

    const chips = wrapper.findAll('.chip')
    expect(chips.map((chip) => chip.text())).toEqual(['demo-a', 'demo-b'])
  })
})

describe('approval panel', () => {
  const approval = (machine: ChatMachine) =>
    machine.applyEnvelope(
      {
        v: 1,
        thread_id: 'thread-1',
        run_id: 'run-1',
        seq: 1,
        source: 'main',
        payload: {
          interrupt_id: 'int-1',
          interrupt_type: 'hitl_approval',
          prompt: '这笔写操作需要你确认后才能执行。',
          candidates: [
            {
              type: 'approval',
              tool_name: 'order_create',
              arguments: {
                supplier_id: 'S001',
                lines: [{ part_id: 'P001', quantity: 50, unit_price: '25.50' }],
              },
            },
            { type: 'decisions', allowed: ['approve', 'reject'] },
          ],
        },
      },
      'interrupt',
    )

  it('shows the frozen line items and disables both buttons while busy', async () => {
    const machine = machineWith(approval)
    const wrapper = mount(SidePanel, {
      props: { snapshot: machine.snapshot(), busy: true },
    })

    expect(wrapper.text()).toContain('P001')
    expect(wrapper.text()).toContain('25.50')

    const buttons = wrapper.findAll('button')
    const approve = buttons.find((button) => button.text().includes('批准'))
    const reject = buttons.find((button) => button.text().includes('拒绝'))

    expect(approve?.attributes('disabled')).toBeDefined()
    expect(reject?.attributes('disabled')).toBeDefined()
  })

  it('emits approve and reject when enabled', async () => {
    const machine = machineWith(approval)
    const wrapper = mount(SidePanel, {
      props: { snapshot: machine.snapshot(), busy: false },
    })

    const buttons = wrapper.findAll('button')
    await buttons.find((button) => button.text().includes('批准'))?.trigger('click')
    await buttons.find((button) => button.text().includes('拒绝'))?.trigger('click')

    expect(wrapper.emitted('approve')).toHaveLength(1)
    expect(wrapper.emitted('reject')).toHaveLength(1)
  })

  it('hides the decision buttons once the interrupt is gone', () => {
    const machine = machineWith(approval)
    machine.applyEnvelope(
      { v: 1, thread_id: 't', run_id: 'run-1', seq: 2, source: 'main', payload: { status: 'completed' } },
      'done',
    )
    const wrapper = mount(SidePanel, { props: { snapshot: machine.snapshot(), busy: false } })

    expect(wrapper.text()).not.toContain('等待审批')
  })
})

describe('supplement form', () => {
  it('keeps submit disabled until something is typed, then emits it', async () => {
    const machine = machineWith((current) =>
      current.applyEnvelope(
        {
          v: 1,
          thread_id: 't',
          run_id: 'run-1',
          seq: 1,
          source: 'main',
          payload: {
            interrupt_id: 'int-2',
            interrupt_type: 'order_info_supplement',
            prompt: '请补充单价。',
            candidates: [{ type: 'supplement', missing_fields: ['lines[0].unit_price'] }],
          },
        },
        'interrupt',
      ),
    )
    const wrapper = mount(SidePanel, { props: { snapshot: machine.snapshot(), busy: false } })

    expect(wrapper.text()).toContain('lines[0].unit_price')

    const submit = wrapper.findAll('button').find((button) => button.text().includes('提交补充'))
    expect(submit?.attributes('disabled')).toBeDefined()

    await wrapper.get('textarea').setValue('P001 数量 50，单价 25.50')
    await wrapper.findAll('button').find((button) => button.text().includes('提交补充'))?.trigger('click')

    expect(wrapper.emitted('supplement')?.[0]).toEqual(['P001 数量 50，单价 25.50'])
  })
})

describe('error reporting', () => {
  it('shows the real code and refuses to imply a rollback', () => {
    const machine = machineWith((current) =>
      current.applyEnvelope(
        {
          v: 1,
          thread_id: 't',
          run_id: 'run-1',
          seq: 1,
          source: 'main',
          payload: { code: 'MODEL_ERROR', message: '模型返回不可解析', retryable: false },
        },
        'error',
      ),
    )
    const wrapper = mount(SidePanel, { props: { snapshot: machine.snapshot(), busy: false } })

    expect(wrapper.text()).toContain('MODEL_ERROR')
    expect(wrapper.text()).toContain('不代表刚才的操作被撤销')
  })
})
