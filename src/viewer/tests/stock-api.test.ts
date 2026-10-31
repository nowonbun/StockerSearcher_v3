import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  getQuery: vi.fn(),
  queryRows: vi.fn(),
}))

vi.mock('h3', () => ({
  getQuery: mocks.getQuery,
  createError: (details: { statusCode: number; statusMessage: string }) => Object.assign(new Error(details.statusMessage), details),
}))

vi.mock('../server/utils/db', () => ({
  queryRows: mocks.queryRows,
  dateOnly: (value: unknown) => String(value).slice(0, 10),
  numeric: (value: unknown) => value === null || value === undefined ? null : Number(value),
}))

import { handleStockApi } from '../server/utils/stock-api'

describe('handleStockApi', () => {
  beforeEach(() => {
    mocks.getQuery.mockReset()
    mocks.queryRows.mockReset()
  })

  it('uses KR as the default market and normalizes prediction dates', async () => {
    mocks.getQuery.mockReturnValue({})
    mocks.queryRows.mockResolvedValue([{ data_cutoff: '2026-07-30T00:00:00.000Z' }])

    await expect(handleStockApi({} as never, 'predict-dates')).resolves.toEqual({ dates: ['2026-07-30'] })
    expect(mocks.queryRows).toHaveBeenCalledWith(expect.stringContaining('stock_predict_kr'), [120])
  })

  it('rejects an unsupported market before querying the database', async () => {
    mocks.getQuery.mockReturnValue({ market: 'US' })

    await expect(handleStockApi({} as never, 'predict-dates')).rejects.toMatchObject({ statusCode: 400, statusMessage: 'market must be JP or KR' })
    expect(mocks.queryRows).not.toHaveBeenCalled()
  })

  it('rejects malformed prediction dates and series codes before querying the database', async () => {
    mocks.getQuery.mockReturnValue({ as_of: '2026/07/30' })
    await expect(handleStockApi({} as never, 'predict')).rejects.toMatchObject({ statusCode: 400, statusMessage: 'as_of must be YYYY-MM-DD' })

    mocks.getQuery.mockReturnValue({ code: 'bad code', as_of: '2026-07-30' })
    await expect(handleStockApi({} as never, 'series')).rejects.toMatchObject({ statusCode: 400, statusMessage: 'code must contain 1-20 letters, numbers, dot, underscore, or hyphen' })
    expect(mocks.queryRows).not.toHaveBeenCalled()
  })

  it('passes prediction query parameters and normalizes numeric response fields', async () => {
    mocks.getQuery.mockReturnValue({ market: 'JP', as_of: '2026-07-30' })
    mocks.queryRows.mockResolvedValue([{
      data_cutoff: '2026-07-30', code: '7203', name: 'Toyota', probability: '0.91',
      open: '100', close: '101', low: '99', high: '102', volume: '500',
    }])

    await expect(handleStockApi({} as never, 'predict')).resolves.toEqual({ rows: [{
      data_cutoff: '2026-07-30', code: '7203', name: 'Toyota', probability: 0.91,
      open: 100, close: 101, low: 99, high: 102, volume: 500,
    }] })
    expect(mocks.queryRows).toHaveBeenCalledWith(expect.stringContaining('stock_predict_jp'), ['2026-07-30', '2026-07-30'])
  })

  it('rejects non-numeric scanner thresholds before querying the database', async () => {
    mocks.getQuery.mockReturnValue({ date: '2026-07-30', trans_amnt_min: 'abc', close_max: '1000' })

    await expect(handleStockApi({} as never, 'scanner')).rejects.toMatchObject({ statusCode: 400, statusMessage: 'trans_amnt_min must be numeric' })
    expect(mocks.queryRows).not.toHaveBeenCalled()
  })

  it('lists scanner dates from the market reference code for a fast indexed lookup', async () => {
    mocks.getQuery.mockReturnValue({ market: 'JP' })
    mocks.queryRows.mockResolvedValue([{ date: '2026-07-31' }])

    await expect(handleStockApi({} as never, 'scanner-dates')).resolves.toEqual({ dates: ['2026-07-31'] })
    expect(mocks.queryRows).toHaveBeenCalledWith(
      expect.stringContaining('SELECT date FROM stock_data_split_jp WHERE code = $1'),
      ['7203', 120],
    )

    mocks.getQuery.mockReturnValue({ market: 'KR' })
    await handleStockApi({} as never, 'scanner-dates')
    expect(mocks.queryRows).toHaveBeenLastCalledWith(
      expect.stringContaining('SELECT date FROM stock_data_split_kr WHERE code = $1'),
      ['005930', 120],
    )
  })

  describe.each(['JP', 'KR'])('%s 스캐너 Ratio', (market) => {
    it.each([
      ['scanner', false, false],
      ['lowerband-scanner', false, true],
      ['scanner-weekly', true, false],
      ['lowerband-scanner-weekly', true, true],
    ] as const)('%s 쿼리는 소수 나눗셈과 0 분모 방어를 유지한다', async (endpoint, weekly, lower) => {
      const bandColumn = lower ? 'lowerband60_1' : 'upperband60_1'
      const ratioField = lower ? 'lowerband_ratio' : 'upperband_ratio'
      const ratio = 19140 / 16058
      mocks.getQuery.mockReturnValue({ market, date: '2026-10-30', trans_amnt_min: '1000', close_max: '100000' })
      // DB는 격리하고 SQL 계약과 PostgreSQL numeric 문자열의 응답 변환을 검증한다.
      mocks.queryRows.mockResolvedValue([
        { date: '2026-10-30', code: 'TEST', close: '19140', band: '16058', ratio: String(ratio) },
        { date: '2026-10-30', code: 'NULL', close: '19140', band: null, ratio: null },
        { date: '2026-10-30', code: 'ZERO', close: '19140', band: '0', ratio: null },
      ])

      const result = await handleStockApi({} as never, endpoint)

      expect(mocks.queryRows).toHaveBeenCalledWith(
        expect.stringContaining(`sd.close::numeric / NULLIF(sd.${bandColumn}, 0)`),
        ['2026-10-30', 1000, 100000],
      )
      expect(mocks.queryRows.mock.calls[0][0]).toContain(`FROM stock_data_split_${weekly ? 'week_' : ''}${market.toLowerCase()} sd`)
      expect(result).toMatchObject({ rows: [
        { [ratioField]: ratio }, { [ratioField]: null }, { [ratioField]: null },
      ] })
      expect(result.rows[0][ratioField].toFixed(4)).toBe('1.1919')
    })
  })

  it('reverses weekly series rows and omits daily-only moving averages', async () => {
    mocks.getQuery.mockReturnValue({ code: '7203', as_of: '2026-07-30' })
    mocks.queryRows.mockResolvedValue([
      { date: '2026-07-25', open: '2', high: '3', low: '1', close: '2', volume: '20', ma5: '2', ma20: '2', ma60: '2', bb_upper: '3', bb_lower: '1', bb_lower3: '0', di_plus: '1', di_minus: '0', adx: '5' },
      { date: '2026-07-18', open: '1', high: '2', low: '0', close: '1', volume: '10', ma5: '1', ma20: '1', ma60: '1', bb_upper: '2', bb_lower: '0', bb_lower3: '-1', di_plus: '1', di_minus: '0', adx: '4' },
    ])

    const result = await handleStockApi({} as never, 'series-weekly')

    expect(result).toMatchObject({ series: [{ date: '2026-07-18' }, { date: '2026-07-25' }] })
    expect(result.series[0]).not.toHaveProperty('ma120')
    expect(result.series[0]).not.toHaveProperty('ma240')
    expect(mocks.queryRows).toHaveBeenCalledWith(expect.stringContaining('stock_data_split_week_kr'), ['7203', '2026-07-30', 120])
  })
})
