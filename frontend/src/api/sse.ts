/**
 * Server-sent events over a POST body.
 *
 * The browser's `EventSource` cannot send a request body, so the stream is read from
 * `fetch`'s `ReadableStream` instead. That means the framing is ours to implement, and the
 * contract names the three ways it is easy to get wrong:
 *
 * 1. **A network chunk is not an event.** A chunk may hold half an event, three events, or
 *    a lone `data:` line. The parser keeps a buffer between chunks.
 * 2. **UTF-8 characters are split across chunks.** A multi-byte character can straddle a
 *    boundary, so the decoder must be streaming (`{ stream: true }`); decoding each chunk
 *    independently turns Chinese text into replacement characters.
 * 3. **`data:` may repeat within one event,** and the payload is the lines joined by newlines
 *    — not just the first one.
 *
 * A blank line ends an event. Lines starting with `:` are comments, which is how the
 * server's heartbeat is sent.
 */

export interface SseFrame {
  id: string
  event: string
  data: string
}

const DEFAULT_EVENT = 'message'

export class SseParser {
  private buffer = ''
  private eventName = ''
  private eventId = ''
  private dataLines: string[] = []

  /** Feed decoded text; returns whatever complete events it produced. */
  push(chunk: string): SseFrame[] {
    this.buffer += chunk
    const frames: SseFrame[] = []

    let newline = this.buffer.indexOf('\n')
    while (newline !== -1) {
      const rawLine = this.buffer.slice(0, newline)
      this.buffer = this.buffer.slice(newline + 1)
      // Tolerate CRLF: the payload is the same event either way.
      const line = rawLine.endsWith('\r') ? rawLine.slice(0, -1) : rawLine

      if (line === '') {
        const frame = this.complete()
        if (frame) frames.push(frame)
      } else if (!line.startsWith(':')) {
        this.consume(line)
      }
      newline = this.buffer.indexOf('\n')
    }
    return frames
  }

  /** Events still pending when the connection closes without a trailing blank line. */
  flush(): SseFrame[] {
    const frames: SseFrame[] = []
    if (this.buffer) {
      // A final line with no newline is still a line.
      const line = this.buffer
      this.buffer = ''
      if (!line.startsWith(':')) this.consume(line)
    }
    const frame = this.complete()
    if (frame) frames.push(frame)
    return frames
  }

  private consume(line: string): void {
    const colon = line.indexOf(':')
    const field = colon === -1 ? line : line.slice(0, colon)
    // Per the SSE spec, a single leading space after the colon is stripped.
    let value = colon === -1 ? '' : line.slice(colon + 1)
    if (value.startsWith(' ')) value = value.slice(1)

    if (field === 'event') this.eventName = value
    else if (field === 'id') this.eventId = value
    else if (field === 'data') this.dataLines.push(value)
  }

  private complete(): SseFrame | null {
    if (this.dataLines.length === 0 && !this.eventName && !this.eventId) return null
    const frame: SseFrame = {
      id: this.eventId,
      event: this.eventName || DEFAULT_EVENT,
      data: this.dataLines.join('\n'),
    }
    this.eventName = ''
    this.eventId = ''
    this.dataLines = []
    return frame
  }
}

export interface StreamOptions {
  signal?: AbortSignal
  fetchImpl?: typeof fetch
}

/**
 * POST a body and yield decoded frames until the response ends.
 *
 * Errors are surfaced, never swallowed: a caller that treats a broken stream as "the run
 * finished" would show a partial answer as a complete one.
 */
export async function* streamSse(
  url: string,
  body: unknown,
  options: StreamOptions = {},
): AsyncGenerator<SseFrame> {
  const doFetch = options.fetchImpl ?? fetch
  const response = await doFetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify(body),
    credentials: 'same-origin',
    signal: options.signal,
  })

  if (!response.ok) {
    throw await toStreamError(response)
  }
  if (!response.body) {
    throw new Error('响应没有可读流；本接口需要 POST + fetch ReadableStream')
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder('utf-8')
  const parser = new SseParser()

  try {
    for (;;) {
      const { value, done } = await reader.read()
      if (done) break
      // Streaming decode: a multi-byte character may straddle two chunks.
      for (const frame of parser.push(decoder.decode(value, { stream: true }))) {
        yield frame
      }
    }
    // Flush the decoder first, then the parser: a trailing partial event is still an event.
    for (const frame of parser.push(decoder.decode())) yield frame
    for (const frame of parser.flush()) yield frame
  } finally {
    reader.releaseLock?.()
  }
}

async function toStreamError(response: Response): Promise<Error> {
  let detail = ''
  try {
    const payload = await response.json()
    detail = payload?.detail ?? ''
  } catch {
    detail = ''
  }
  const error = new Error(detail || `请求失败（HTTP ${response.status}）`)
  ;(error as Error & { status?: number }).status = response.status
  return error
}
