import { Pool, type QueryResultRow } from 'pg'

let pool: Pool | undefined

function databasePool() {
  if (!pool) {
    const password = process.env.STOCK_DB_PASSWORD
    if (!password) throw createError({ statusCode: 500, statusMessage: 'STOCK_DB_PASSWORD is not configured' })
    pool = new Pool({
      host: process.env.STOCK_DB_HOST || '127.0.0.1',
      port: Number(process.env.STOCK_DB_PORT || '5432'),
      database: process.env.STOCK_DB_NAME || 'stock',
      user: process.env.STOCK_DB_USER || 'stock_app',
      password,
      max: 10,
    })
  }
  return pool
}

export async function queryRows<T extends QueryResultRow>(text: string, values: unknown[] = []) {
  return (await databasePool().query<T>(text, values)).rows
}

export function dateOnly(value: unknown) {
  if (value instanceof Date) return value.toISOString().slice(0, 10)
  return String(value).slice(0, 10)
}

export function numeric(value: unknown) {
  return value === null || value === undefined ? null : Number(value)
}
