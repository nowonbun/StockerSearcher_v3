<script setup lang="ts">
type Job = { run_id: string; market: string; mode: string; status: string; created_at?: string; started_at?: string; finished_at?: string; trigger_source: string; error_message?: string; legacy?: boolean; tasks?: { task_name: string; status: string; exit_code: number | null }[] }
type Schedule = { market: string; enabled: boolean; cron: string; mode: string; timezone: string }
const busy = ref(false)
const refreshing = ref(false)
const error = ref('')
const notice = ref('')
const market = ref('JP')
const mode = ref('collect')
const modes = ref<string[]>([])
const runs = ref<Job[]>([])
const schedules = ref<Schedule[]>([])
const selected = ref<Job | null>(null)
const logs = ref('')
let timer: ReturnType<typeof setInterval> | undefined
const labels: Record<string, string> = { collect: '통합 데이터 수집', predict: '예측', full: '수집·예측' }
const taskLabels: Record<string, string> = { base: '시세 수집', indicators: '지표 자동 계산', daily: '일봉 예측', weekly: '주봉 예측' }
const statuses: Record<string, string> = { queued: '대기', running: '실행 중', cancelling: '취소 중', cancelled: '취소', succeeded: '성공', failed: '실패', interrupted: '재시작 중단' }
const timestamp = (value?: string) => value ? new Date(value).toLocaleString('ko-KR', { timeZone: 'Asia/Seoul' }) : '—'
async function api(path: string, method = 'GET', body?: unknown) {
  const response = await fetch(`/api/batch/${path}`, { method, headers: { 'content-type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body), cache: 'no-store' })
  const result = await response.json()
  if (!response.ok) {
    throw new Error(result.data?.message || result.statusMessage || '배치 요청 실패')
  }
  return result
}
async function refresh() {
  if (refreshing.value) return
  refreshing.value = true
  try {
    const result = await api('runs'); runs.value = result.runs; modes.value = result.modes
    if (selected.value) {
      const id = selected.value.run_id
      const [detail, log] = await Promise.all([api(`runs/${id}`), api(`runs/${id}/log`)])
      if (selected.value?.run_id === id) { selected.value = detail; logs.value = log.text }
    }
  } finally { refreshing.value = false }
}
async function action(work: () => Promise<void>) {
  if (busy.value) return
  busy.value = true; error.value = ''; notice.value = ''
  try { await work() } catch (cause) { error.value = cause instanceof Error ? cause.message : '요청 실패' } finally { busy.value = false }
}
async function initialize() {
  await action(async () => {
    await refresh(); schedules.value = (await api('schedules')).schedules
    clearInterval(timer)
    timer = setInterval(() => { void refresh().catch((cause) => { error.value = String(cause) }) }, 3000)
  })
}
async function run() {
  await action(async () => { selected.value = await api('runs', 'POST', { market: market.value, mode: mode.value }); await refresh(); notice.value = '실행을 대기열에 등록했습니다.' })
}
async function cancel(job: Job) {
  if (!window.confirm(`${job.market} ${labels[job.mode] || job.mode} 실행을 취소할까요?`)) return
  await action(async () => { await api(`runs/${job.run_id}/cancel`, 'POST'); await refresh() })
}
async function save(schedule: Schedule) {
  await action(async () => { schedules.value = (await api(`schedules/${schedule.market}`, 'PUT', { enabled: schedule.enabled, cron: schedule.cron, mode: schedule.mode })).schedules; notice.value = '예약 설정을 저장했습니다.' })
}
async function select(job: Job) { selected.value = job; logs.value = ''; await action(refresh) }
onMounted(() => { void initialize() })
onBeforeUnmount(() => clearInterval(timer))
</script>

<template>
  <section class="batch-panel">
    <header><h1>배치 관리</h1><span>Asia/Seoul · JP/KR 전체 순차 실행</span></header>
    <p>Prefect 없이 실행하는 자체 배치입니다. 상세 로그는 마지막 64KiB를 3초마다 갱신합니다.</p>
    <p>로컬 단독 사용 모드입니다. 로그인 없이 사용할 수 있으며 외부에 공개하지 마세요.</p>
    <p v-if="error" role="alert" class="batch-error">{{ error }}</p><p v-if="notice" role="status">{{ notice }}</p>
    <div>
      <div class="batch-toolbar"><button :disabled="busy || refreshing" @click="action(refresh)">새로고침</button><button :disabled="busy" @click="initialize">설정 다시 불러오기</button></div>
      <h2>수동 실행</h2>
      <form class="batch-form" @submit.prevent="run"><label>시장<select v-model="market"><option>JP</option><option>KR</option></select></label><label>작업<select v-model="mode"><option v-for="value in modes" :key="value" :value="value">{{ labels[value] || value }}</option></select></label><button :disabled="busy">실행 등록</button></form>
      <p class="batch-warning">통합 데이터 수집은 시세 수집 후 지표를 자동 계산합니다. 예측은 일봉·주봉을 순서대로 실행하며, 수집·예측은 전체 단계를 실행합니다. 앞 단계 실패 시 이후 작업은 중단됩니다.</p>
      <h2>예약 설정</h2><p>5필드 cron: 분 시 일 월 요일(0·7=일요일). *, 목록, 범위, /간격을 지원합니다. 재시작 중 놓친 예약은 재생하지 않습니다. 같은 시장·작업의 중복 등록은 거부합니다.</p>
      <form v-for="schedule in schedules" :key="schedule.market" class="batch-form" @submit.prevent="save(schedule)"><strong>{{ schedule.market }}</strong><label><input v-model="schedule.enabled" type="checkbox">예약 활성화</label><label>cron<input v-model="schedule.cron" required maxlength="200" aria-label="cron"></label><label>작업<select v-model="schedule.mode"><option v-for="value in modes" :key="value" :value="value">{{ labels[value] || value }}</option></select></label><button :disabled="busy">저장</button></form>
      <h2>최근 실행 100건</h2><div class="batch-table"><table><thead><tr><th>등록 시각</th><th>시장 / 작업</th><th>경로</th><th>상태</th><th>시작 / 종료</th><th>관리</th></tr></thead><tbody><tr v-for="job in runs" :key="job.run_id"><td>{{ timestamp(job.created_at) }}</td><td>{{ job.market }} / {{ labels[job.mode] || job.mode }}</td><td>{{ job.trigger_source }}</td><td :class="{ 'batch-error': ['failed', 'interrupted'].includes(job.status) }">{{ statuses[job.status] || job.status }}</td><td>{{ timestamp(job.started_at) }}<br>{{ timestamp(job.finished_at) }}</td><td><button :disabled="busy" @click="select(job)">상세·로그</button><button v-if="!job.legacy && ['queued', 'running'].includes(job.status)" :disabled="busy" @click="cancel(job)">취소</button></td></tr><tr v-if="!runs.length"><td colspan="6">실행 이력이 없습니다.</td></tr></tbody></table></div>
      <section v-if="selected" aria-label="실행 상세"><h2>실행 상세 · {{ selected.market }} / {{ selected.mode }}</h2><code>{{ selected.run_id }}</code><p>{{ statuses[selected.status] || selected.status }} <span class="batch-error">{{ selected.error_message }}</span></p><ul><li v-for="task in selected.tasks || []" :key="task.task_name">{{ taskLabels[task.task_name] || task.task_name }} — {{ statuses[task.status] || task.status }} / 종료 코드 {{ task.exit_code ?? '—' }}</li></ul><pre aria-label="배치 로그">{{ logs || '아직 로그가 없습니다.' }}</pre></section>
    </div>
  </section>
</template>

<style scoped>
.batch-panel { color: #d7e4ff; }.batch-panel header,.batch-form,.batch-toolbar { display:flex; gap:12px; align-items:center; flex-wrap:wrap; }.batch-panel h2 { margin-top:28px; font-size:17px; }.batch-panel p,.batch-panel small { color:#a4b9dc; line-height:1.6; }.batch-form { margin:12px 0; padding:16px; background:#0b1a36; border:1px solid #29446f; border-radius:8px; }.batch-form label { display:flex; gap:8px; align-items:center; }.batch-panel input,.batch-panel select,.batch-panel button { padding:8px; color:#d7e4ff; background:#081733; border:1px solid #426498; border-radius:5px; }.batch-panel button { cursor:pointer; }.batch-panel button:disabled { opacity:.5; cursor:wait; }.batch-panel .batch-error { color:#ff9b9b; }.batch-panel .batch-warning { border-left:3px solid #edbb62; padding-left:12px; }.batch-table { overflow:auto; }.batch-panel th,.batch-panel td { padding:12px; border-bottom:1px solid #29446f; text-align:left; }.batch-panel pre { white-space:pre-wrap; overflow-wrap:anywhere; max-height:480px; overflow:auto; padding:16px; background:#020914; border:1px solid #29446f; }.batch-panel code { overflow-wrap:anywhere; }
</style>
