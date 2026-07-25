<script setup lang="ts">
type Market = 'JP' | 'KR'
type PanelKey = 'predict' | 'upper' | 'lower' | 'predictWeekly' | 'upperWeekly' | 'lowerWeekly'
type Row = Record<string, string | number | null>
type SeriesRow = Row & { date: string }
type HeikinAshi = { haOpen: number | null; haHigh: number | null; haLow: number | null; haClose: number | null }
type ChartCandle = { index: number; x: number; wickTop: number; wickBottom: number; bodyY: number; bodyHeight: number; rising: boolean }

const panels: Array<{ key: PanelKey; title: string; weekly: boolean; kind: 'predict' | 'upper' | 'lower' }> = [
  { key: 'predict', title: 'Predict Search', weekly: false, kind: 'predict' },
  { key: 'upper', title: 'UpperBand Scanner', weekly: false, kind: 'upper' },
  { key: 'lower', title: 'LowerBand Scanner', weekly: false, kind: 'lower' },
  { key: 'predictWeekly', title: 'Predict Search', weekly: true, kind: 'predict' },
  { key: 'upperWeekly', title: 'UpperBand Scanner', weekly: true, kind: 'upper' },
  { key: 'lowerWeekly', title: 'LowerBand Scanner', weekly: true, kind: 'lower' },
]

const activePanel = ref<PanelKey>('predict')
const activeMarkets = reactive<Record<PanelKey, Market>>({ predict: 'JP', upper: 'JP', lower: 'JP', predictWeekly: 'JP', upperWeekly: 'JP', lowerWeekly: 'JP' })
const dates = reactive<Record<string, string[]>>({})
const forms = reactive<Record<string, { date: string; query: string; openMin: string; openMax: string; closeMin: string; closeMax: string; transAmount: string }>>({})
const rows = reactive<Record<string, Row[]>>({})
const loading = ref(false)
const error = ref('')
const page = ref(1)
const pageSize = 100
const chart = reactive({ open: false, loading: false, error: '', title: '', rows: [] as SeriesRow[] })

const panel = computed(() => panels.find((item) => item.key === activePanel.value)!)
const market = computed(() => activeMarkets[activePanel.value])
const stateKey = computed(() => `${activePanel.value}-${market.value}`)
const currentForm = computed(() => forms[stateKey.value] || createForm())
const currentRows = computed(() => rows[stateKey.value] || [])

function createForm() { return { date: '', query: '', openMin: '', openMax: '', closeMin: '', closeMax: '', transAmount: '' } }
function getForm(key = stateKey.value) { return (forms[key] ||= createForm()) }
function dateKey(item = panel.value, selectedMarket = market.value) { return `${item.weekly ? 'weekly' : 'daily'}-${selectedMarket}` }
function numberOrEmpty(value: string) { return value.trim() === '' ? undefined : Number(value) }
function format(value: Row[string] | number, digits?: number) {
  if (value === null || value === undefined || value === '') return ''
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return String(value)
  return digits === undefined ? numeric.toLocaleString() : numeric.toFixed(digits)
}
async function fetchJson(path: string, query: Record<string, string | number | undefined>) {
  const params = new URLSearchParams()
  Object.entries(query).forEach(([key, value]) => { if (value !== undefined && value !== '') params.set(key, String(value)) })
  const response = await fetch(`${path}?${params.toString()}`)
  if (!response.ok) throw new Error(`API 요청에 실패했습니다 (${response.status})`)
  return response.json()
}
async function loadDates() {
  const key = dateKey()
  if (panel.value.kind !== 'predict') {
    const suffix = panel.value.weekly ? '-weekly' : ''
    const [dateResult, defaults] = await Promise.all([
      fetchJson(`/api/scanner${suffix}-dates`, { market: market.value }),
      fetchJson(`/api/scanner${suffix}-defaults`, { market: market.value }),
    ])
    dates[key] = dateResult.dates || []
    const form = getForm()
    form.transAmount = String(defaults.trans_amnt_min ?? '')
    form.closeMax = String(defaults.close_max ?? '')
  } else {
    const endpoint = panel.value.weekly ? '/api/predict-dates-weekly' : '/api/predict-dates'
    const result = await fetchJson(endpoint, { market: market.value })
    dates[key] = result.dates || []
  }
  const form = getForm()
  if (!dates[key].includes(form.date)) form.date = dates[key][0] || ''
}
function endpointForCurrentPanel() {
  const suffix = panel.value.weekly ? '-weekly' : ''
  if (panel.value.kind === 'predict') return `/api/predict${suffix}`
  return `/api/${panel.value.kind === 'upper' ? 'scanner' : 'lowerband-scanner'}${suffix}`
}
async function search() {
  error.value = ''; loading.value = true
  try {
    const form = getForm()
    if (!dates[dateKey()]?.length || !form.date) throw new Error('조회 가능한 기준일이 없습니다.')
    const query = panel.value.kind === 'predict'
      ? { market: market.value, as_of: form.date }
      : { market: market.value, date: form.date, trans_amnt_min: numberOrEmpty(form.transAmount), close_max: numberOrEmpty(form.closeMax) }
    const result = await fetchJson(endpointForCurrentPanel(), query)
    rows[stateKey.value] = result.rows || []; page.value = 1
  } catch (cause) { error.value = cause instanceof Error ? cause.message : '데이터를 불러오지 못했습니다.' } finally { loading.value = false }
}
function clearFilters() { const form = getForm(); form.query = ''; form.openMin = ''; form.openMax = ''; form.closeMin = ''; form.closeMax = ''; page.value = 1 }
function selectPanel(key: PanelKey) { activePanel.value = key; error.value = ''; page.value = 1; void loadDates().catch((cause) => { error.value = cause instanceof Error ? cause.message : '기준일을 불러오지 못했습니다.' }) }
function selectMarket(nextMarket: Market) { activeMarkets[activePanel.value] = nextMarket; error.value = ''; page.value = 1; void loadDates().catch((cause) => { error.value = cause instanceof Error ? cause.message : '기준일을 불러오지 못했습니다.' }) }
function closeChart() { chart.open = false }
async function openChart(row: Row) {
  const code = String(row.code || '')
  const asOf = String(panel.value.kind === 'predict' ? row.data_cutoff || '' : row.date || '')
  if (!code || !asOf) return
  chart.open = true; chart.loading = true; chart.error = ''; chart.rows = []
  chart.title = `${code} ${String(row.name || '')} — ${asOf}`
  try {
    const result = await fetchJson(panel.value.weekly ? '/api/series-weekly' : '/api/series', { market: market.value, code, as_of: asOf })
    chart.rows = result.series || []
  } catch (cause) { chart.error = cause instanceof Error ? cause.message : '차트 데이터를 불러오지 못했습니다.' } finally { chart.loading = false }
}
const filteredRows = computed(() => {
  const form = currentForm.value
  const text = form.query.trim().toLocaleLowerCase()
  const filtered = currentRows.value.filter((row) => {
    if (text && !`${row.code ?? ''} ${row.name ?? ''}`.toLocaleLowerCase().includes(text)) return false
    const open = Number(row.open), close = Number(row.close)
    const openMin = numberOrEmpty(form.openMin), openMax = numberOrEmpty(form.openMax), closeMin = numberOrEmpty(form.closeMin), closeMax = numberOrEmpty(form.closeMax)
    return !(openMin !== undefined && open < openMin) && !(openMax !== undefined && open > openMax) && !(closeMin !== undefined && close < closeMin) && !(closeMax !== undefined && close > closeMax)
  })
  const field = panel.value.kind === 'predict' ? 'probability' : 'transAmnt'
  return [...filtered].sort((a, b) => Number(b[field] ?? 0) - Number(a[field] ?? 0))
})
const pageCount = computed(() => Math.max(1, Math.ceil(filteredRows.value.length / pageSize)))
const pagedRows = computed(() => filteredRows.value.slice((page.value - 1) * pageSize, page.value * pageSize))
const heikinAshi = computed<HeikinAshi[]>(() => {
  let previousOpen: number | null = null
  let previousClose: number | null = null
  return chart.rows.map((row) => {
    const open = Number(row.open), high = Number(row.high), low = Number(row.low), close = Number(row.close)
    if (![open, high, low, close].every(Number.isFinite)) return { haOpen: null, haHigh: null, haLow: null, haClose: null }
    const haClose = (open + high + low + close) / 4
    const haOpen = previousOpen === null || previousClose === null ? (open + close) / 2 : (previousOpen + previousClose) / 2
    const haHigh = Math.max(high, haOpen, haClose)
    const haLow = Math.min(low, haOpen, haClose)
    previousOpen = haOpen
    previousClose = haClose
    return { haOpen, haHigh, haLow, haClose }
  })
})
const chartLines = computed(() => {
  const width = 1000, height = 420, padding = 44
  const fields = ['close', 'ma5', 'ma20', 'ma60', 'bb_upper', 'bb_lower']
  const values = [
    ...heikinAshi.value.flatMap((item) => [item.haHigh, item.haLow].filter((value): value is number => value !== null)),
    ...chart.rows.flatMap((row) => fields.filter((field) => field !== 'close').map((field) => Number(row[field])).filter(Number.isFinite)),
  ]
  const min = values.length ? Math.min(...values) : 0, max = values.length ? Math.max(...values) : 1, span = max - min || 1
  const denominator = Math.max(chart.rows.length - 1, 1)
  const spacing = (width - padding * 2) / denominator
  const candleWidth = Math.max(1, Math.min(12, spacing * 0.8))
  const x = (index: number) => padding + index * spacing
  const y = (value: number) => height - padding - ((value - min) / span) * (height - padding * 2)
  const points = (field: string) => chart.rows.map((row, index) => { const value = Number(row[field]); return Number.isFinite(value) ? `${x(index).toFixed(2)},${y(value).toFixed(2)}` : '' }).filter(Boolean).join(' ')
  const candles = heikinAshi.value.flatMap<ChartCandle>((item, index) => {
    if (item.haOpen === null || item.haHigh === null || item.haLow === null || item.haClose === null) return []
    const openY = y(item.haOpen), closeY = y(item.haClose)
    return [{ index, x: x(index), wickTop: y(item.haHigh), wickBottom: y(item.haLow), bodyY: Math.min(openY, closeY), bodyHeight: Math.max(1, Math.abs(openY - closeY)), rising: item.haClose >= item.haOpen }]
  })
  return { min, max, candleWidth, candles, ma5: points('ma5'), ma20: points('ma20'), ma60: points('ma60'), upper: points('bb_upper'), lower: points('bb_lower') }
})

watch([filteredRows, pageCount], () => { if (page.value > pageCount.value) page.value = pageCount.value })
watch(stateKey, () => { page.value = 1 })
onMounted(() => { void loadDates().catch((cause) => { error.value = cause instanceof Error ? cause.message : '기준일을 불러오지 못했습니다.' }) })
</script>

<template>
  <NuxtLayout>
    <div class="layout">
      <aside class="sidebar"><div class="brand">Stock AI Portal</div><div class="brand-sub">AI-Powered Stock Analysis</div><nav><p>DAILY</p><button v-for="item in panels.filter((item) => !item.weekly)" :key="item.key" class="nav-item" :class="{ active: activePanel === item.key }" @click="selectPanel(item.key)">{{ item.title }}</button><p>WEEKLY</p><button v-for="item in panels.filter((item) => item.weekly)" :key="item.key" class="nav-item" :class="{ active: activePanel === item.key }" @click="selectPanel(item.key)">{{ item.title }}</button></nav></aside>
      <main class="content">
        <h1>{{ panel.title }} <small v-if="panel.weekly">(Weekly)</small></h1>
        <div class="tabs"><button v-for="item in (['JP', 'KR'] as Market[])" :key="item" :class="{ active: market === item }" @click="selectMarket(item)">{{ item }}</button></div>
        <section class="search-form">
          <label>Date<select v-model="currentForm.date"><option v-for="date in dates[dateKey()] || []" :key="date" :value="date">{{ date }}</option></select></label>
          <template v-if="panel.kind === 'predict'"><label>Stock search<input v-model="currentForm.query" placeholder="Code or name" @input="page = 1"></label><label>Open min<input v-model="currentForm.openMin" type="number" @input="page = 1"></label><label>Open max<input v-model="currentForm.openMax" type="number" @input="page = 1"></label><label>Close min<input v-model="currentForm.closeMin" type="number" @input="page = 1"></label><label>Close max<input v-model="currentForm.closeMax" type="number" @input="page = 1"></label></template>
          <template v-else><label>Minimum transaction amount<input v-model="currentForm.transAmount" type="number"></label><label>Maximum close<input v-model="currentForm.closeMax" type="number"></label></template>
          <div class="actions"><button class="primary" :disabled="loading" @click="search">{{ loading ? 'Loading...' : 'Search' }}</button><button v-if="panel.kind === 'predict'" @click="clearFilters">Reset</button></div>
        </section>
        <p v-if="error" class="error">{{ error }} — Nuxt DB API와 PostgreSQL 연결 설정을 확인하세요.</p>
        <div class="table-wrap"><table v-if="pagedRows.length"><thead><tr><template v-if="panel.kind === 'predict'"><th>Date</th><th>Code</th><th>Name</th><th>Prob</th><th>Open</th><th>Close</th><th>Low</th><th>High</th><th>Volume</th></template><template v-else><th>Date</th><th>Code</th><th>Name</th><th>Close</th><th>{{ panel.kind === 'upper' ? 'UpperBand' : 'LowerBand' }}</th><th v-if="panel.kind === 'lower'">MA60</th><th>Ratio</th><th>TransAmnt</th></template></tr></thead><tbody><tr v-for="(row, index) in pagedRows" :key="`${row.code}-${row.date || row.data_cutoff}-${index}`" class="clickable-row" @click="openChart(row)"><template v-if="panel.kind === 'predict'"><td>{{ row.data_cutoff }}</td><td>{{ row.code }}</td><td>{{ row.name }}</td><td>{{ format(row.probability, 4) }}</td><td>{{ format(row.open) }}</td><td>{{ format(row.close) }}</td><td>{{ format(row.low) }}</td><td>{{ format(row.high) }}</td><td>{{ format(row.volume) }}</td></template><template v-else><td>{{ row.date }}</td><td>{{ row.code }}</td><td>{{ row.name }}</td><td>{{ format(row.close) }}</td><td>{{ format(panel.kind === 'upper' ? row.upperband60_1 : row.lowerband60_1) }}</td><td v-if="panel.kind === 'lower'">{{ format(row.ma60) }}</td><td>{{ format(row.upperband_ratio ?? row.lowerband_ratio, 4) }}</td><td>{{ format(row.transAmnt) }}</td></template></tr></tbody></table><p v-else class="empty">{{ loading ? 'Loading data.' : 'No search results.' }}</p></div>
        <div class="pagination" v-if="filteredRows.length"><span>{{ filteredRows.length.toLocaleString() }} rows</span><button :disabled="page === 1" @click="page--">Previous</button><span>{{ page }} / {{ pageCount }}</span><button :disabled="page === pageCount" @click="page++">Next</button></div>
        <div v-if="chart.open" class="chart-modal" @click.self="closeChart"><section class="chart-card" role="dialog" aria-modal="true" :aria-label="chart.title"><header><strong>{{ chart.title }}</strong><button aria-label="Close chart" @click="closeChart">×</button></header><p v-if="chart.loading" class="chart-message">Chart data is loading.</p><p v-else-if="chart.error" class="chart-message error">{{ chart.error }}</p><p v-else-if="!chart.rows.length" class="chart-message">No chart data is available.</p><template v-else><div class="chart-legend"><span class="heikin-ashi">Heikin-Ashi</span><span class="ma5">MA5</span><span class="ma20">MA20</span><span class="ma60">MA60</span><span class="band">Bollinger band</span></div><svg class="price-chart" viewBox="0 0 1000 420" role="img" aria-label="Heikin-Ashi and moving average chart"><line x1="44" y1="44" x2="44" y2="376" class="chart-grid" /><line x1="44" y1="376" x2="956" y2="376" class="chart-grid" /><polyline :points="chartLines.upper" class="line-band" /><polyline :points="chartLines.lower" class="line-band" /><g v-for="candle in chartLines.candles" :key="candle.index"><line :x1="candle.x" :x2="candle.x" :y1="candle.wickTop" :y2="candle.wickBottom" class="ha-wick" :class="{ rising: candle.rising, falling: !candle.rising }" /><rect :x="candle.x - chartLines.candleWidth / 2" :y="candle.bodyY" :width="chartLines.candleWidth" :height="candle.bodyHeight" :class="{ rising: candle.rising, falling: !candle.rising }" /></g><polyline :points="chartLines.ma5" class="line-ma5" /><polyline :points="chartLines.ma20" class="line-ma20" /><polyline :points="chartLines.ma60" class="line-ma60" /><text x="4" y="52">{{ format(chartLines.max, 2) }}</text><text x="4" y="376">{{ format(chartLines.min, 2) }}</text></svg></template></section></div>
      </main>
    </div>
  </NuxtLayout>
</template>

<style>
:root { color: #d7e4ff; background: #060e20; font-family: Inter, Roboto, Arial, sans-serif; } * { box-sizing: border-box; } body { margin: 0; background: radial-gradient(circle at 20% 0%, #0d2247, #060e20 52%); } button, input, select { font: inherit; }.layout { display: grid; grid-template-columns: 220px 1fr; min-height: 100vh; }.sidebar { padding: 18px 14px; border-right: 1px solid #15305d; background: linear-gradient(180deg, #081733, #050d1f); }.brand { font-size: 24px; font-weight: 700; }.brand-sub { margin-top: 4px; color: #85a3d4; font-size: 12px; } nav { margin-top: 28px; } nav p { margin: 16px 14px 4px; color: #4a6899; font-size: 11px; font-weight: 700; letter-spacing: .08em; }.nav-item { display: block; width: 100%; padding: 11px 14px; border: 0; border-radius: 10px; background: transparent; color: #7a9cc8; text-align: left; cursor: pointer; }.nav-item:hover, .nav-item.active { background: #12366f; color: #dce8ff; }.content { min-width: 0; padding: 24px; } h1 { margin: 0 0 16px; color: #c4d8ff; font-size: 18px; } h1 small { color: #85a3d4; font-size: 13px; }.tabs { display: flex; border-bottom: 1px solid #1d345f; }.tabs button { padding: 8px 24px; border: 0; border-bottom: 3px solid transparent; background: transparent; color: #6a8cc5; font-weight: 600; cursor: pointer; }.tabs button.active { border-bottom-color: #1c7dff; color: #d7e4ff; }.search-form { display: grid; grid-template-columns: repeat(6, minmax(120px, 1fr)); gap: 14px; align-items: end; margin-top: 16px; padding: 18px; border: 1px solid #1d345f; border-radius: 10px; background: linear-gradient(180deg, #0b1a36, #09152f); }.search-form label { color: #8da8d1; font-size: 12px; }.search-form input, .search-form select { display: block; width: 100%; margin-top: 6px; padding: 8px; border: 1px solid #2a4270; border-radius: 6px; background: #081733; color: #dbe7ff; }.actions { display: flex; gap: 8px; }.actions button, .pagination button, .chart-card header button { padding: 9px 14px; border: 1px solid #2a4270; border-radius: 8px; background: transparent; color: #b8d0f4; cursor: pointer; }.actions .primary { border-color: #176af2; background: #176af2; color: white; }.actions button:disabled, .pagination button:disabled { opacity: .5; cursor: not-allowed; }.error { color: #ff8f8f; }.table-wrap { overflow: auto; min-height: 300px; margin-top: 16px; border: 1px solid #1d345f; border-radius: 10px; background: #0b1a36; } table { width: 100%; border-collapse: collapse; font-size: 13px; } th, td { padding: 10px; border-bottom: 1px solid #1d345f; text-align: right; white-space: nowrap; } th { color: #8da8d1; font-weight: 600; } th:nth-child(2), th:nth-child(3), td:nth-child(2), td:nth-child(3) { text-align: left; } .clickable-row { cursor: pointer; }.clickable-row:hover { background: #10264c; }.empty { padding: 48px; color: #85a3d4; text-align: center; }.pagination { display: flex; justify-content: flex-end; align-items: center; gap: 12px; margin-top: 14px; color: #85a3d4; font-size: 13px; }.chart-modal { position: fixed; inset: 0; z-index: 10; display: grid; place-items: center; padding: 24px; background: rgb(0 0 0 / 65%); }.chart-card { width: min(1100px, 100%); max-height: 90vh; overflow: auto; padding: 18px; border: 1px solid #2a5b9b; border-radius: 12px; background: #081733; }.chart-card header { display: flex; align-items: center; justify-content: space-between; gap: 16px; }.chart-card header button { font-size: 20px; line-height: 1; }.chart-message { padding: 42px; text-align: center; color: #85a3d4; }.chart-legend { display: flex; gap: 14px; margin: 18px 0 8px; font-size: 12px; }.chart-legend span::before { content: ''; display: inline-block; width: 14px; height: 3px; margin-right: 5px; vertical-align: middle; background: currentColor; }.ma5 { color: #66d9ef; }.ma20 { color: #9b8cff; }.ma60 { color: #63d391; }.band { color: #7896c5; }.price-chart { width: 100%; min-width: 650px; background: #060e20; }.price-chart text { fill: #85a3d4; font-size: 18px; }.chart-grid { stroke: #29446f; stroke-width: 1; }.price-chart polyline { fill: none; stroke-width: 2; vector-effect: non-scaling-stroke; }.line-ma5 { stroke: #66d9ef; }.line-ma20 { stroke: #9b8cff; }.line-ma60 { stroke: #63d391; }.line-band { stroke: #7896c5; stroke-dasharray: 5 4; } @media (max-width: 900px) { .layout { grid-template-columns: 1fr; }.sidebar { border-right: 0; border-bottom: 1px solid #15305d; }.search-form { grid-template-columns: repeat(2, minmax(0, 1fr)); } nav { display: flex; flex-wrap: wrap; gap: 4px; } nav p { flex-basis: 100%; }.nav-item { width: auto; } } @media (max-width: 520px) { .content { padding: 14px; }.search-form { grid-template-columns: 1fr; } }
.heikin-ashi { color: #e05c5c; }.ha-wick { stroke-width: 1; vector-effect: non-scaling-stroke; }.price-chart rect.rising, .ha-wick.rising { fill: #e05c5c; stroke: #e05c5c; }.price-chart rect.falling, .ha-wick.falling { fill: #4a9adc; stroke: #4a9adc; }
</style>
