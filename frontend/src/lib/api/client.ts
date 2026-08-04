import { config } from '../config'

export class ApiException extends Error {
  constructor(
    public statusCode: number,
    public code: string,
    message: string,
  ) {
    super(message)
    this.name = 'ApiException'
  }

  get userMessage(): string {
    return this.message || this.code
  }

  static fromBody(statusCode: number, body: Record<string, unknown>): ApiException {
    const err = body.error
    if (err && typeof err === 'object') {
      const e = err as Record<string, unknown>
      return new ApiException(
        statusCode,
        String(e.code ?? 'unknown_error'),
        String(e.message ?? body.detail ?? 'Request failed'),
      )
    }
    return new ApiException(
      statusCode,
      String(err ?? 'unknown_error'),
      String(body.detail ?? body.message ?? 'Request failed'),
    )
  }
}

function uri(path: string): string {
  const base = config.apiBaseUrl.replace(/\/$/, '')
  return `${base}${path}`
}

async function decode(res: Response): Promise<Record<string, unknown>> {
  let decoded: Record<string, unknown>
  try {
    const raw: unknown = await res.json()
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
      throw new ApiException(res.status, 'invalid_response', 'Unexpected response format')
    }
    decoded = raw as Record<string, unknown>
  } catch (e) {
    if (e instanceof ApiException) throw e
    throw new ApiException(
      res.status,
      'invalid_response',
      res.status >= 400 ? `Server error (${res.status})` : 'Invalid JSON',
    )
  }
  if (res.status >= 400) throw ApiException.fromBody(res.status, decoded)
  return decoded
}

function wrapNetwork(err: unknown): never {
  if (err instanceof ApiException) throw err
  if (err instanceof DOMException && err.name === 'AbortError') throw err
  const base = config.apiBaseUrl || '(same origin)'
  throw new ApiException(
    0,
    'network_error',
    `Cannot reach the API at ${base}. Start the backend or check VITE_API_BASE_URL.`,
  )
}

export type JsonHeaders = Record<string, string>

export type RequestOpts = {
  headers?: JsonHeaders
  signal?: AbortSignal
}

type JsonMethod = 'GET' | 'POST' | 'PATCH' | 'DELETE'

async function requestJson(
  method: JsonMethod,
  path: string,
  opts?: RequestOpts,
  body?: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  try {
    const hasBody = body !== undefined
    const res = await fetch(uri(path), {
      method,
      headers: hasBody ? { 'Content-Type': 'application/json', ...opts?.headers } : opts?.headers,
      signal: opts?.signal,
      ...(hasBody ? { body: JSON.stringify(body ?? {}) } : {}),
    })
    return await decode(res)
  } catch (e) {
    wrapNetwork(e)
  }
}

export const api = {
  async getJson(path: string, opts?: RequestOpts | JsonHeaders): Promise<Record<string, unknown>> {
    // Back-compat: older callers pass headers as the 2nd arg.
    if (opts && ('signal' in opts || 'headers' in opts)) {
      return requestJson('GET', path, opts as RequestOpts)
    }
    return requestJson('GET', path, opts ? { headers: opts as JsonHeaders } : undefined)
  },

  async postJson(
    path: string,
    body?: Record<string, unknown>,
    opts?: RequestOpts | JsonHeaders,
  ): Promise<Record<string, unknown>> {
    if (opts && ('signal' in opts || 'headers' in opts)) {
      return requestJson('POST', path, opts as RequestOpts, body)
    }
    return requestJson('POST', path, opts ? { headers: opts as JsonHeaders } : undefined, body)
  },

  async patchJson(
    path: string,
    body?: Record<string, unknown>,
    opts?: RequestOpts | JsonHeaders,
  ): Promise<Record<string, unknown>> {
    if (opts && ('signal' in opts || 'headers' in opts)) {
      return requestJson('PATCH', path, opts as RequestOpts, body)
    }
    return requestJson('PATCH', path, opts ? { headers: opts as JsonHeaders } : undefined, body)
  },

  async deleteJson(path: string, opts?: RequestOpts | JsonHeaders): Promise<Record<string, unknown>> {
    if (opts && ('signal' in opts || 'headers' in opts)) {
      return requestJson('DELETE', path, opts as RequestOpts)
    }
    return requestJson('DELETE', path, opts ? { headers: opts as JsonHeaders } : undefined)
  },
}
