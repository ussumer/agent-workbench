import { describe, expect, it } from 'vitest'

import { SseParser, streamSse } from '../src/api/sse'

describe('SSE framing', () => {
  it('reassembles an event split across network chunks', () => {
    const parser = new SseParser()

    // The split falls inside the event name, inside the JSON, and inside the payload text.
    expect(parser.push('id: run-1:1\nev')).toEqual([])
    expect(parser.push('ent: run_started\ndata: {"v":1,"thr')).toEqual([])
    const frames = parser.push('ead_id":"t","run_id":"run-1","seq":1}\n\n')

    expect(frames).toHaveLength(1)
    expect(frames[0].event).toBe('run_started')
    expect(JSON.parse(frames[0].data)).toMatchObject({ thread_id: 't', seq: 1 })
  })

  it('delivers three events that arrive in one chunk', () => {
    const parser = new SseParser()
    const frames = parser.push(
      'event: a\ndata: {}\n\nevent: b\ndata: {}\n\nevent: c\ndata: {}\n\n',
    )

    expect(frames.map((frame) => frame.event)).toEqual(['a', 'b', 'c'])
  })

  it('joins multiple data lines of one event with a newline', () => {
    const parser = new SseParser()
    const frames = parser.push('event: token\ndata: line one\ndata: line two\n\n')

    expect(frames[0].data).toBe('line one\nline two')
  })

  it('ignores heartbeat comments', () => {
    const parser = new SseParser()
    const frames = parser.push(': heartbeat\n\nevent: token\ndata: {}\n\n')

    expect(frames).toHaveLength(1)
    expect(frames[0].event).toBe('token')
  })

  it('emits a trailing event that was never terminated by a blank line', () => {
    const parser = new SseParser()
    parser.push('event: done\ndata: {"status":"completed"}')

    const frames = parser.flush()

    expect(frames).toHaveLength(1)
    expect(frames[0].event).toBe('done')
  })

  it('tolerates CRLF line endings', () => {
    const parser = new SseParser()
    const frames = parser.push('event: token\r\ndata: {"a":1}\r\n\r\n')

    expect(frames).toHaveLength(1)
    expect(frames[0].data).toBe('{"a":1}')
  })
})

describe('POST streaming', () => {
  function chunkedResponse(chunks: Uint8Array[], status = 200): Response {
    let index = 0
    const body = {
      getReader() {
        return {
          read: async () =>
            index < chunks.length
              ? { value: chunks[index++], done: false }
              : { value: undefined, done: true },
          releaseLock() {},
        }
      },
    }
    return {
      ok: status >= 200 && status < 300,
      status,
      body,
      json: async () => ({}),
    } as unknown as Response
  }

  it('decodes a multi-byte character split across two chunks', async () => {
    // "你好" is 6 UTF-8 bytes; splitting after 4 puts a character boundary inside a chunk.
    const payload = 'event: token\ndata: {"text":"你好"}\n\n'
    const bytes = new TextEncoder().encode(payload)
    const first = bytes.slice(0, bytes.length - 2)
    const second = bytes.slice(bytes.length - 2)

    const frames: string[] = []
    for await (const frame of streamSse('/api/chat/stream', {}, {
      fetchImpl: async () => chunkedResponse([first, second]),
    })) {
      frames.push(frame.data)
    }

    expect(frames).toHaveLength(1)
    expect(JSON.parse(frames[0]).text).toBe('你好')
  })

  it('POSTs the body and asks for an event stream', async () => {
    let seen: RequestInit | undefined
    const empty = chunkedResponse([])

    for await (const _frame of streamSse('/api/chat/stream', { request_id: 'r1' }, {
      fetchImpl: async (_url, init) => {
        seen = init
        return empty
      },
    })) {
      // no frames expected
    }

    expect(seen?.method).toBe('POST')
    expect((seen?.headers as Record<string, string>).Accept).toBe('text/event-stream')
    expect(JSON.parse(String(seen?.body))).toEqual({ request_id: 'r1' })
  })

  it('surfaces an HTTP failure instead of returning an empty stream', async () => {
    const response = chunkedResponse([], 409)
    ;(response as unknown as { json: () => Promise<unknown> }).json = async () => ({
      detail: 'thread already has an active run',
    })

    await expect(async () => {
      for await (const _frame of streamSse('/api/chat/stream', {}, {
        fetchImpl: async () => response,
      })) {
        // unreachable
      }
    }).rejects.toThrow(/active run/)
  })
})
