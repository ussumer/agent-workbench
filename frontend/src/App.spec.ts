import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import App from './App.vue'

describe('App shell', () => {
  it('renders the workspace heading', () => {
    const wrapper = mount(App)
    expect(wrapper.get('h1').text()).toBe('采购助手工作台')
  })

  it('states that the interactive UI arrives in T14', () => {
    const wrapper = mount(App)
    expect(wrapper.text()).toContain('T14')
  })
})
