import { describe, expect, it } from 'vitest'

import { escapeHtml, renderMarkdown, safeUrl } from '../src/markdown'

describe('raw HTML is never executed', () => {
  it('escapes a script tag instead of emitting it', () => {
    const html = renderMarkdown('<script>alert(1)</script>')

    expect(html).not.toContain('<script')
    expect(html).toContain('&lt;script&gt;')
  })

  it('escapes an img onerror payload', () => {
    const html = renderMarkdown('<img src=x onerror="alert(1)">')

    expect(html).not.toContain('<img')
    expect(html).toContain('&lt;img')
  })

  it('escapes an event handler smuggled into a link label', () => {
    const html = renderMarkdown('[<b>click</b>](https://example.com)')

    expect(html).not.toContain('<b>')
    expect(html).toContain('&lt;b&gt;')
    expect(html).toContain('href="https://example.com"')
  })

  it('refuses a javascript: link and shows it as text', () => {
    const html = renderMarkdown('[点我](javascript:alert(1))')

    expect(html).not.toContain('href="javascript')
    expect(html).toContain('[点我](javascript:alert(1))')
  })

  it('escapes the five characters that matter', () => {
    expect(escapeHtml(`&<>"'`)).toBe('&amp;&lt;&gt;&quot;&#39;')
  })

  it('accepts only http(s) urls', () => {
    expect(safeUrl('https://example.com/a')).toBe('https://example.com/a')
    expect(safeUrl('http://example.com')).toBe('http://example.com')
    expect(safeUrl('javascript:alert(1)')).toBeNull()
    expect(safeUrl('data:text/html;base64,x')).toBeNull()
    expect(safeUrl('/api/artifacts/1')).toBeNull()
  })
})

describe('formatting the assistant actually emits', () => {
  it('renders a heading', () => {
    expect(renderMarkdown('## 需要补货的物料')).toContain('<h2>需要补货的物料</h2>')
  })

  it('renders a table with a header row', () => {
    const html = renderMarkdown(['| 物料 | 库存 |', '|---|---|', '| P001 | 8 |'].join('\n'))

    expect(html).toContain('<table>')
    expect(html).toContain('<th>物料</th>')
    expect(html).toContain('<td>P001</td>')
  })

  it('renders bold, inline code and fenced code', () => {
    expect(renderMarkdown('**重点**')).toContain('<strong>重点</strong>')
    expect(renderMarkdown('用 `part_query` 查')).toContain('<code>part_query</code>')
    expect(renderMarkdown('```python\nx = 1\n```')).toContain('<pre><code class="language-python">')
  })

  it('does not let inline code be re-formatted by later rules', () => {
    const html = renderMarkdown('查询 `order_*` 与 **粗体**')

    expect(html).toContain('<code>order_*</code>')
    expect(html).toContain('<strong>粗体</strong>')
  })

  it('renders unordered and ordered lists', () => {
    expect(renderMarkdown('- 一\n- 二')).toContain('<ul><li>一</li><li>二</li></ul>')
    expect(renderMarkdown('1. 一\n2. 二')).toContain('<ol><li>一</li><li>二</li></ol>')
  })

  it('turns a single newline inside a paragraph into a line break', () => {
    expect(renderMarkdown('第一行\n第二行')).toContain('第一行<br>第二行')
  })

  it('handles empty input without producing stray markup', () => {
    expect(renderMarkdown('')).toBe('')
  })
})
