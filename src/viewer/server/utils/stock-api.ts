import { getQuery, createError, type H3Event } from 'h3'
import { dateOnly, numeric, queryRows } from './db'

type Market = 'JP' | 'KR'
type Tables = { predict: string; data: string; list: string; weeklyPredict: string; weeklyData: string }

const tables: Record<Market, Tables> = {
  JP: { predict: 'stock_predict_jp', data: 'stock_data_jp', list: 'stock_list_jp', weeklyPredict: 'stock_predict_week_jp', weeklyData: 'stock_data_week_jp' },
  KR: { predict: 'stock_predict_kr', data: 'stock_data_kr', list: 'stock_list_kr', weeklyPredict: 'stock_predict_week_kr', weeklyData: 'stock_data_week_kr' },
}

function marketOf(value: unknown): Market {
  const market = String(value || 'KR').toUpperCase()
  if (market !== 'JP' && market !== 'KR') throw createError({ statusCode: 400, statusMessage: 'market must be JP or KR' })
  return market
}

function requiredDate(value: unknown, name: string) {
  const date = String(value || '')
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) throw createError({ statusCode: 400, statusMessage: `${name} must be YYYY-MM-DD` })
  return date
}

function requiredCode(value: unknown) {
  const code = String(value || '')
  if (!/^[A-Za-z0-9._-]{1,20}$/.test(code)) throw createError({ statusCode: 400, statusMessage: 'code must contain 1-20 letters, numbers, dot, underscore, or hyphen' })
  return code
}

function requiredNumber(value: unknown, name: string) {
  const number = Number(value)
  if (!Number.isFinite(number)) throw createError({ statusCode: 400, statusMessage: `${name} must be numeric` })
  return number
}

function etfClause(market: Market, alias: string) {
  return market === 'JP' ? ` AND ${alias}.stocktype != 'ETF・ETN'` : ''
}

async function predictDates(market: Market, weekly: boolean) {
  const table = weekly ? tables[market].weeklyPredict : tables[market].predict
  const rows = await queryRows<{ data_cutoff: unknown }>(`SELECT DISTINCT data_cutoff FROM ${table} ORDER BY data_cutoff DESC LIMIT $1`, [120])
  return { dates: rows.map((row) => dateOnly(row.data_cutoff)) }
}

async function predictions(market: Market, asOf: string, weekly: boolean) {
  const t = tables[market]
  const predictionTable = weekly ? t.weeklyPredict : t.predict
  const dataTable = weekly ? t.weeklyData : t.data
  const dateJoin = weekly
    ? `d.code = p.code AND d.date = (SELECT MAX(date) FROM ${dataTable} WHERE date <= $2)`
    : 'd.code = p.code AND d.date = $2'
  const rows = await queryRows<Record<string, unknown>>(`
    SELECT p.data_cutoff, p.code, l.name, p.probability, d.open, d.close, d.low, d.high, d.volume
    FROM ${predictionTable} p
    JOIN ${t.list} l ON l.code = p.code
    LEFT JOIN ${dataTable} d ON ${dateJoin}
    WHERE p.data_cutoff = $1${etfClause(market, 'l')}
    ORDER BY p.probability DESC`, [asOf, asOf])
  return { rows: rows.map((row) => ({ data_cutoff: dateOnly(row.data_cutoff), code: row.code, name: row.name, probability: numeric(row.probability), open: numeric(row.open), close: numeric(row.close), low: numeric(row.low), high: numeric(row.high), volume: numeric(row.volume) })) }
}

async function scannerDates(market: Market, weekly: boolean) {
  const table = weekly ? tables[market].weeklyData : tables[market].data
  const code = market === 'JP' ? '7203' : '005930'
  const rows = await queryRows<{ date: unknown }>(`SELECT date FROM ${table} WHERE code = $1 ORDER BY date DESC LIMIT $2`, [code, 120])
  return { dates: rows.map((row) => dateOnly(row.date)) }
}

async function scannerDefaults(market: Market, weekly: boolean) {
  const table = weekly ? tables[market].weeklyData : tables[market].data
  const rows = await queryRows<{ date: unknown }>(`SELECT DISTINCT date FROM ${table} WHERE date >= CURRENT_DATE - INTERVAL '30 days' ORDER BY date DESC LIMIT 2`)
  return {
    date: rows[1] ? dateOnly(rows[1].date) : rows[0] ? dateOnly(rows[0].date) : null,
    close_max: market === 'JP' ? 1000 : 100000,
    trans_amnt_min: weekly ? (market === 'JP' ? 2500000000 : 5000000000) : (market === 'JP' ? 500000000 : 1000000000),
  }
}

async function scanner(market: Market, date: string, transAmount: number, closeMax: number, weekly: boolean, lower: boolean) {
  const t = tables[market]
  const dataTable = weekly ? t.weeklyData : t.data
  const bandColumn = lower ? 'lowerband60_1' : 'upperband60_1'
  const extraCondition = lower ? 'sd.lowerband60_1 < sd.close AND sd.close < sd."60mvavg"' : 'sd.close > sd.upperband60_1'
  const rows = await queryRows<Record<string, unknown>>(`
    SELECT sd.date, sd.code, sl.name, sd.close, sd.${bandColumn} AS band, sd."60mvavg" AS ma60,
           sd.transamnt AS "transAmnt", (sd.close / NULLIF(sd.${bandColumn}, 0)) AS ratio
    FROM ${dataTable} sd
    JOIN ${t.list} sl ON sl.code = sd.code
    WHERE sd.date = $1 AND ${extraCondition}
      AND sd.transamnt > $2 AND sd.close < $3${etfClause(market, 'sl')}
    ORDER BY sd.transamnt DESC`, [date, transAmount, closeMax])
  return {
    rows: rows.map((row) => lower
      ? { date: dateOnly(row.date), code: row.code, name: row.name, close: numeric(row.close), lowerband60_1: numeric(row.band), ma60: numeric(row.ma60), transAmnt: numeric(row.transAmnt), lowerband_ratio: numeric(row.ratio) }
      : { date: dateOnly(row.date), code: row.code, name: row.name, close: numeric(row.close), upperband60_1: numeric(row.band), transAmnt: numeric(row.transAmnt), upperband_ratio: numeric(row.ratio) }),
  }
}

async function series(market: Market, code: string, asOf: string, weekly: boolean) {
  const t = tables[market]
  const dataTable = weekly ? t.weeklyData : t.data
  const movingAverages = weekly
    ? '"5mvavg" AS ma5, "20mvavg" AS ma20, "60mvavg" AS ma60'
    : '"5mvavg" AS ma5, "20mvavg" AS ma20, "60mvavg" AS ma60, "120mvavg" AS ma120, "240mvavg" AS ma240'
  const rows = await queryRows<Record<string, unknown>>(`
    SELECT date, open, high, low, close, volume, ${movingAverages},
           upperband60_1 AS bb_upper, lowerband60_1 AS bb_lower, lowerband60_3 AS bb_lower3,
           di_plus, di_minus, adx
    FROM ${dataTable}
    WHERE code = $1 AND date <= $2
    ORDER BY date DESC
    LIMIT $3`, [code, asOf, weekly ? 120 : 240])
  return {
    series: rows.reverse().map((row) => ({
      date: dateOnly(row.date), open: numeric(row.open), high: numeric(row.high), low: numeric(row.low), close: numeric(row.close), volume: numeric(row.volume),
      ma5: numeric(row.ma5), ma20: numeric(row.ma20), ma60: numeric(row.ma60),
      ...(!weekly ? { ma120: numeric(row.ma120), ma240: numeric(row.ma240) } : {}),
      bb_upper: numeric(row.bb_upper), bb_lower: numeric(row.bb_lower), bb_lower3: numeric(row.bb_lower3), di_plus: numeric(row.di_plus), di_minus: numeric(row.di_minus), adx: numeric(row.adx),
    })),
  }
}

export async function handleStockApi(event: H3Event, endpoint: string) {
  const query = getQuery(event)
  const market = marketOf(query.market)
  const asOf = () => requiredDate(query.as_of, 'as_of')
  const scan = (weekly: boolean, lower: boolean) => scanner(market, requiredDate(query.date, 'date'), requiredNumber(query.trans_amnt_min, 'trans_amnt_min'), requiredNumber(query.close_max, 'close_max'), weekly, lower)
  const priceSeries = (weekly: boolean) => series(market, requiredCode(query.code), asOf(), weekly)

  switch (endpoint) {
    case 'predict-dates': return predictDates(market, false)
    case 'predict': return predictions(market, asOf(), false)
    case 'predict-dates-weekly': return predictDates(market, true)
    case 'predict-weekly': return predictions(market, asOf(), true)
    case 'scanner-dates': return scannerDates(market, false)
    case 'scanner-weekly-dates': return scannerDates(market, true)
    case 'scanner-defaults': return scannerDefaults(market, false)
    case 'scanner-weekly-defaults': return scannerDefaults(market, true)
    case 'scanner': return scan(false, false)
    case 'lowerband-scanner': return scan(false, true)
    case 'scanner-weekly': return scan(true, false)
    case 'lowerband-scanner-weekly': return scan(true, true)
    case 'series': return priceSeries(false)
    case 'series-weekly': return priceSeries(true)
    default: throw createError({ statusCode: 404, statusMessage: 'API endpoint not found' })
  }
}
