import { createError, getHeader, getMethod, getRequestURL, getRouterParam, readBody, type H3Event } from 'h3'

// 사용자가 URL이나 명령 문자열을 지정할 수 없는 내부 전용 프록시입니다.
export async function handleBatchApi(event: H3Event) {
  const url = getRequestURL(event)
  if (!['localhost', '127.0.0.1', '[::1]', '::1'].includes(url.hostname)) throw createError({ statusCode: 403, statusMessage: '로컬 호스트에서만 사용할 수 있습니다' })
  const path = getRouterParam(event, 'path') || ''
  const method = getMethod(event)
  const id = '[a-f0-9]{32}'
  const allowed = path === 'runs' ? ['GET', 'POST']
    : path === 'schedules' ? ['GET']
      : /^schedules\/(JP|KR)$/.test(path) ? ['PUT']
        : new RegExp(`^runs/${id}(/log)?$`).test(path) ? ['GET']
          : new RegExp(`^runs/${id}/cancel$`).test(path) ? ['POST'] : []
  if (!allowed.length) throw createError({ statusCode: 404, statusMessage: '배치 경로 없음' })
  if (!allowed.includes(method)) throw createError({ statusCode: 405, statusMessage: '허용하지 않는 메서드' })
  if (method !== 'GET') {
    const origin = getHeader(event, 'origin')
    const contentType = (getHeader(event, 'content-type') || '').split(';')[0].trim().toLowerCase()
    if ((origin && origin !== url.origin) || getHeader(event, 'sec-fetch-site') === 'cross-site' || contentType !== 'application/json') throw createError({ statusCode: 403, statusMessage: '동일 출처 JSON 요청만 허용합니다' })
  }
  let body: string | undefined
  if ((method === 'POST' && path === 'runs') || method === 'PUT') {
    const value = await readBody(event)
    const keys = path === 'runs' ? ['market', 'mode'] : ['enabled', 'cron', 'mode']
    if (!value || typeof value !== 'object' || Array.isArray(value) || Object.keys(value).length !== keys.length || Object.keys(value).some((key) => !keys.includes(key))) throw createError({ statusCode: 400, statusMessage: '허용되지 않은 배치 입력' })
    body = JSON.stringify(value)
    if (Buffer.byteLength(body) > 4096) throw createError({ statusCode: 400, statusMessage: '배치 입력 초과' })
  }
  let response: Response
  try {
    response = await fetch(`http://batch:8001/${path}`, {
      method, headers: { 'content-type': 'application/json' }, body,
      cache: 'no-store', redirect: 'error', signal: AbortSignal.timeout(10000),
    })
  } catch {
    throw createError({ statusCode: 502, statusMessage: '배치 서비스에 연결할 수 없습니다' })
  }
  const result = await response.json().catch(() => ({}))
  if (!response.ok) throw createError({ statusCode: response.status, statusMessage: '배치 요청 실패', data: { message: result.error || '배치 서비스 오류' } })
  return result
}
