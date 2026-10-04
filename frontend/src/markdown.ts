/**
 * A deliberately small Markdown renderer.
 *
 * The requirement is "Markdown must not execute raw HTML". The way to guarantee that is not
 * to sanitise after rendering — it is to escape every character of the input *first*, then
 * add formatting, so no input can ever produce a tag. A sanitiser is a filter with a bypass
 * list; escaping first has no bypass to find.
 *
 * The supported subset is what the assistant actually emits: headings, bold, inline code,
 * fenced code, tables, lists, links and paragraphs. Anything else stays as literal text,
 * which is the honest failure mode — the user sees what the model wrote.
 */

const ESCAPES: Record<string, string> = {
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
  '"': '&quot;',
  "'": '&#39;',
}

export function escapeHtml(text: string): string {
  return text.replace(/[&<>"']/g, (char) => ESCAPES[char] ?? char)
}

/** Only http(s) links become links; anything else is shown as text. */
export function safeUrl(raw: string): string | null {
  const url = raw.trim()
  if (/^https?:\/\//i.test(url)) return url
  return null
}

function inline(text: string): string {
  let out = text
  // Code first: its contents must not be re-processed by the other rules.
  const codes: string[] = []
  out = out.replace(/`([^`]+)`/g, (_match, code: string) => {
    codes.push(`<code>${code}</code>`)
    return `\u0000${codes.length - 1}\u0000`
  })

  out = out.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
  out = out.replace(/(^|[^*])\*([^*]+)\*/g, '$1<em>$2</em>')
  out = out.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (match, label: string, url: string) => {
    const href = safeUrl(url)
    if (!href) return match
    return `<a href="${href}" target="_blank" rel="noopener noreferrer">${label}</a>`
  })

  out = out.replace(/\u0000(\d+)\u0000/g, (_match, index: string) => codes[Number(index)] ?? '')
  return out
}

function renderTable(lines: string[]): string {
  const rows = lines.map((line) =>
    line
      .replace(/^\||\|$/g, '')
      .split('|')
      .map((cell) => cell.trim()),
  )
  const [header, , ...body] = rows
  const head = `<tr>${header.map((cell) => `<th>${inline(cell)}</th>`).join('')}</tr>`
  const rest = body
    .map((row) => `<tr>${row.map((cell) => `<td>${inline(cell)}</td>`).join('')}</tr>`)
    .join('')
  return `<table><thead>${head}</thead><tbody>${rest}</tbody></table>`
}

function isTableSeparator(line: string): boolean {
  return /^\|?[\s:-]*-[\s|:-]*\|?$/.test(line) && line.includes('-')
}

/**
 * Render Markdown to HTML. The input is escaped before any rule runs, so the output can be
 * bound with `v-html` without executing anything the model or a scraped page wrote.
 */
export function renderMarkdown(source: string): string {
  const escaped = escapeHtml(source ?? '')
  const lines = escaped.split('\n')
  const html: string[] = []

  let index = 0
  while (index < lines.length) {
    const line = lines[index]

    if (line.trim() === '') {
      index += 1
      continue
    }

    const fence = line.match(/^```(\w*)\s*$/)
    if (fence) {
      const body: string[] = []
      index += 1
      while (index < lines.length && !/^```/.test(lines[index])) {
        body.push(lines[index])
        index += 1
      }
      index += 1
      const language = fence[1] ? ` class="language-${fence[1]}"` : ''
      html.push(`<pre><code${language}>${body.join('\n')}</code></pre>`)
      continue
    }

    const heading = line.match(/^(#{1,6})\s+(.*)$/)
    if (heading) {
      const level = heading[1].length
      html.push(`<h${level}>${inline(heading[2])}</h${level}>`)
      index += 1
      continue
    }

    if (/^\s*[-*]\s+/.test(line)) {
      const items: string[] = []
      while (index < lines.length && /^\s*[-*]\s+/.test(lines[index])) {
        items.push(`<li>${inline(lines[index].replace(/^\s*[-*]\s+/, ''))}</li>`)
        index += 1
      }
      html.push(`<ul>${items.join('')}</ul>`)
      continue
    }

    if (/^\s*\d+\.\s+/.test(line)) {
      const items: string[] = []
      while (index < lines.length && /^\s*\d+\.\s+/.test(lines[index])) {
        items.push(`<li>${inline(lines[index].replace(/^\s*\d+\.\s+/, ''))}</li>`)
        index += 1
      }
      html.push(`<ol>${items.join('')}</ol>`)
      continue
    }

    if (line.includes('|') && index + 1 < lines.length && isTableSeparator(lines[index + 1])) {
      const block: string[] = []
      while (index < lines.length && lines[index].includes('|')) {
        block.push(lines[index])
        index += 1
      }
      html.push(renderTable(block))
      continue
    }

    const paragraph: string[] = []
    while (
      index < lines.length &&
      lines[index].trim() !== '' &&
      !/^(#{1,6}\s|```|\s*[-*]\s|\s*\d+\.\s)/.test(lines[index])
    ) {
      paragraph.push(lines[index])
      index += 1
    }
    html.push(`<p>${inline(paragraph.join('<br>'))}</p>`)
  }

  return html.join('\n')
}
