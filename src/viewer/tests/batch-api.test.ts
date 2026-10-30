import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ path: 'runs', method: 'GET', headers: {} as Record<string, string>, url: 'http://localhost:4201', body: {} as unknown }))
vi.mock('h3', () => ({
  getRouterParam: () => mocks.path,
  getMethod: () => mocks.method,
  getHeader: (_event: unknown, key: string) => mocks.headers[key],
  getRequestURL: () => new URL(mocks.url),
  readBody: async () => mocks.body,
  createError: (value: object) => Object.assign(new Error(), value),
}))
import { handleBatchApi } from '../server/utils/batch-api'

describe('배치 관리 프록시', () => {
  beforeEach(() => {
    mocks.path = 'runs'; mocks.method = 'GET'; mocks.headers = {}; mocks.url = 'http://localhost:4201'; mocks.body = {}
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({ runs: [] }) }))
  })
  it('인증 없이 고정 내부 주소를 호출하고 캐시하지 않는다', async () => {
    await expect(handleBatchApi({} as never)).resolves.toEqual({ runs: [] })
    expect(fetch).toHaveBeenCalledWith('http://batch:8001/runs', expect.objectContaining({ headers: { 'content-type': 'application/json' }, cache: 'no-store' }))
  })
  it('허용되지 않은 경로를 거부한다', async () => {
    mocks.path = '../other'
    await expect(handleBatchApi({} as never)).rejects.toMatchObject({ statusCode: 404 })
    expect(fetch).not.toHaveBeenCalled()
  })
  it('교차 출처 쓰기는 차단하고 서버 도메인 조회는 허용한다', async () => {
    mocks.method = 'POST'; mocks.headers = { origin: 'https://evil.example', 'content-type': 'application/json' }
    await expect(handleBatchApi({} as never)).rejects.toMatchObject({ statusCode: 403 })
    expect(fetch).not.toHaveBeenCalled()
    mocks.method = 'GET'; mocks.headers = {}; mocks.url = 'http://myserver.com:4201'
    await expect(handleBatchApi({} as never)).resolves.toEqual({ runs: [] })
  })
  it('서버 도메인 JSON 실행은 인증 없이 전달하고 단순 폼 요청은 거부한다', async () => {
    mocks.method = 'POST'; mocks.body = { market: 'KR', mode: 'collect' }
    await expect(handleBatchApi({} as never)).rejects.toMatchObject({ statusCode: 403 })
    mocks.url = 'http://myserver.com:4201'
    mocks.headers = { 'content-type': 'application/json', origin: 'http://myserver.com:4201' }
    await expect(handleBatchApi({} as never)).resolves.toEqual({ runs: [] })
    expect(fetch).toHaveBeenCalledWith('http://batch:8001/runs', expect.objectContaining({ method: 'POST', body: JSON.stringify(mocks.body) }))
  })
  it('조회 경로의 쓰기와 임의 실행 필드를 거부한다', async () => {
    mocks.method = 'DELETE'
    await expect(handleBatchApi({} as never)).rejects.toMatchObject({ statusCode: 405 })
    mocks.method = 'POST'; mocks.headers = { 'content-type': 'application/json' }; mocks.body = { market: 'KR', mode: 'collect', command: 'bad' }
    await expect(handleBatchApi({} as never)).rejects.toMatchObject({ statusCode: 400 })
    expect(fetch).not.toHaveBeenCalled()
  })
  it('배치 서비스 오류 상태와 장애를 구분한다', async () => {
    vi.mocked(fetch).mockResolvedValue({ ok: false, status: 409, json: async () => ({ error: '중복 실행' }) } as Response)
    await expect(handleBatchApi({} as never)).rejects.toMatchObject({ statusCode: 409, data: { message: '중복 실행' } })
    vi.mocked(fetch).mockRejectedValue(new Error('offline'))
    await expect(handleBatchApi({} as never)).rejects.toMatchObject({ statusCode: 502 })
  })
})
