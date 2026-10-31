"""Python 서버 렌더링 배치 관리 웹. Node·TypeScript·프런트엔드 빌드가 필요 없습니다."""
from __future__ import annotations

import asyncio
import hmac
import json
import math
import os
import secrets
import re
import sqlite3
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request as HttpRequest, build_opener

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Route

from batch.config import MARKETS, MODES
from batch.scheduler import SEOUL
from mcp_audit import AuditStore

MODE_NAMES = {'collect': '통합 데이터 수집', 'predict': '예측', 'full': '수집·예측'}
# 과거 완료 이력은 원래 작업 구분을 보존해 읽기만 합니다. 새 실행에는 노출하지 않습니다.
LEGACY_MODE_NAMES = {'base': '원본 수집 (이전)', 'indicators': '지표 계산 (이전)', 'split': '수집·지표 계산 (이전)', 'daily': '일봉 예측 (이전)', 'weekly': '주봉 예측 (이전)'}
TASK_NAMES = {'base': '시세 수집', 'indicators': '지표 자동 계산', 'daily': '일봉 예측', 'weekly': '주봉 예측'}


def mode_name(mode):
    return MODE_NAMES.get(mode, LEGACY_MODE_NAMES.get(mode, mode))


STATUS_NAMES = {'queued': '대기', 'running': '실행 중', 'cancelling': '취소 중', 'succeeded': '성공', 'failed': '실패', 'cancelled': '취소', 'interrupted': '중단'}
PAGES = {'dashboard': ('대시보드', '배치 작업의 실행 현황을 한눈에 확인하고, 스케줄 관리 및 수동 실행을 할 수 있습니다.'), 'jobs': ('배치 작업 관리', '시장과 작업을 선택해 한 번에 수동 실행합니다.'), 'schedules': ('스케줄 관리 (Cron)', '시장별 예약을 설정합니다. 모든 예약은 Asia/Seoul 시간대를 사용합니다.'), 'history': ('실행 이력', '최근 100건의 실행 상태와 소요 시간을 확인합니다.'), 'logs': ('로그 조회', '실행별 작업 결과와 마지막 64KiB의 로그를 확인합니다.'), 'settings': ('시스템 설정', '실행 정책과 보안 설정을 확인합니다. 비밀정보는 표시하지 않습니다.')}
HEADERS = {'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'same-origin', 'Content-Security-Policy': "default-src 'none'; style-src 'self'; img-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"}
PAGES['mcp'] = ('MCP 이용 현황', '주식 조회 MCP의 HTTP 접속 기록과 도구 호출 이력을 확인합니다.')


class BackendError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        # 내부 API 요청은 환경 프록시와 외부 리다이렉트를 사용하지 않습니다.
        return None


class BatchClient:
    def __init__(self, base_url='http://batch:8001'):
        self.base_url = base_url.rstrip('/')
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def request(self, path, method='GET', body=None):
        data = json.dumps(body).encode() if body is not None else None
        request = HttpRequest(self.base_url + path, data=data, method=method, headers={'Content-Type': 'application/json'})
        try:
            with self.opener.open(request, timeout=8) as response:
                raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise BackendError(502, '배치 API 응답 크기 초과')
            return json.loads(raw)
        except HTTPError as error:
            try:
                payload = json.loads(error.read(4096))
                message = payload.get('error', '배치 API 요청 실패')
            except (ValueError, AttributeError):
                message = '배치 API 요청 실패'
            finally:
                error.close()
            raise BackendError(error.code, message) from None
        except (URLError, TimeoutError, OSError, ValueError):
            raise BackendError(503, '배치 API에 연결할 수 없습니다. batch 서비스 상태를 확인하세요.') from None

    def get(self, path):
        return self.request(path)

    def post(self, path, body=None):
        return self.request(path, 'POST', body)

    def put(self, path, body):
        return self.request(path, 'PUT', body)


def e(value):
    return escape(str(value if value is not None else ''), quote=True)


def icon(name, cls=''):
    paths = {
        'database': '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v7c0 4 16 4 16 0V5M4 12v7c0 4 16 4 16 0v-7"/>',
        'grid': '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
        'jobs': '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M9 3v18M13 13h5M13 17h5M5 13h1M5 17h1"/>',
        'calendar': '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M7 2v6M17 2v6M3 11h18M7 15h2M13 15h2"/>',
        'clock': '<circle cx="12" cy="12" r="9"/><path d="M12 6v6l4 2"/>',
        'file': '<path d="M6 2h8l4 4v16H6zM14 2v5h5M9 11h6M9 15h6M9 19h4"/>',
        'settings': '<path d="M10 3h4l1 3 3 1 3 3v4l-3 1-1 3-3 3h-4l-1-3-3-1-3-3v-4l3-1 1-3z"/><circle cx="12" cy="12" r="3"/>',
        'bell': '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4"/>',
        'user': '<circle cx="12" cy="8" r="4"/><path d="M4 22v-3c0-8 16-8 16 0v3"/>',
        'play': '<path d="m8 4 12 8-12 8z"/>',
        'check': '<path d="m5 12 5 5L20 6"/>',
        'x': '<path d="m6 6 12 12M18 6 6 18"/>',
        'layers': '<path d="m12 2 10 6-10 6L2 8zM2 13l10 6 10-6M2 18l10 6 10-6"/>',
        'search': '<circle cx="10" cy="10" r="7"/><path d="m16 16 6 6"/>',
        'arrow': '<path d="M4 12h16m-6-6 6 6-6 6"/>',
        'chart': '<path d="M4 21V11h4v10M10 21V6h4v15M16 21V2h4v19"/>',
        'link': '<path d="m9 15 6-6M7 17l-1 1a4 4 0 0 1-6-6l5-5a4 4 0 0 1 6 0M17 7l1-1a4 4 0 0 1 6 6l-5 5a4 4 0 0 1-6 0"/>',
    }
    return f'<svg class="icon {e(cls)}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{paths.get(name, paths["jobs"])}</svg>'


def stamp(value):
    if not value:
        return '—'
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(SEOUL).strftime('%Y-%m-%d %H:%M:%S')
    except (ValueError, TypeError):
        return '—'


def duration(job):
    try:
        start = datetime.fromisoformat(job['started_at'])
        end = datetime.fromisoformat(job['finished_at']) if job.get('finished_at') else datetime.now(UTC)
        seconds = max(0, int((end - start).total_seconds()))
        return f'{seconds // 60}분 {seconds % 60}초'
    except (KeyError, ValueError, TypeError):
        return '—'


def badge(status):
    safe = status if status in STATUS_NAMES else 'unknown'
    return f'<span class="badge {safe}"><span class="dot"></span>{e(STATUS_NAMES.get(status, status))}</span>'


def hidden(csrf):
    return f'<input type="hidden" name="csrf" value="{e(csrf)}">'


def href(page, **params):
    return '/?' + e(urlencode({'page': page, **params}))


def manual_run_form(csrf):
    """시장·작업 조합을 나열하지 않고 서버의 기존 모드 값을 그대로 제출합니다."""
    markets = ''.join(f'<option value="{market}">{market} · {"일본" if market == "JP" else "한국"}</option>' for market in MARKETS)
    options = ''.join(f'<option value="{mode}">{e(MODE_NAMES[mode])}</option>' for mode in MODES)
    return (
        f'<form class="manual-run-form" method="post" action="/run">{hidden(csrf)}'
        f'<label for="manual-market">시장<select id="manual-market" name="market" required><option value="" selected disabled>시장을 선택하세요</option>{markets}</select></label>'
        f'<label for="manual-mode">작업<select id="manual-mode" name="mode" required><option value="" selected disabled>작업을 선택하세요</option>{options}</select></label>'
        f'<button class="button primary" type="submit">{icon("play")}실행 등록</button></form>'
        '<p class="manual-hint">선택한 작업을 대기열에 등록합니다. 같은 시장·작업이 대기 또는 실행 중이면 중복 등록되지 않습니다.</p>'
    )


def manual_job_help():
    return (
        '<aside class="card guidance manual-help"><h2>실행은 세 가지 패턴만 선택합니다</h2>'
        '<p><strong>통합 데이터 수집</strong> — 원본 시세 수집 → 지표 계산. 수집이 끝나면 이동평균·볼린저밴드·일목균형표를 자동으로 계산합니다.</p>'
        '<p><strong>예측</strong> — 일봉 예측 → 주봉 예측. 저장된 최신 데이터를 사용하며 수집은 하지 않습니다.</p>'
        '<p><strong>수집·예측</strong> — 수집 → 지표 계산 → 일봉 예측 → 주봉 예측. 앞 단계가 실패하면 다음 단계는 실행하지 않습니다.</p>'
        '<p class="muted">원본 수집과 지표 계산은 통합 수집의 내부 단계입니다. 별도로 선택하거나 실행할 필요가 없습니다. 기존에 분리 저장하던 가격·지표 테이블과 조회 뷰를 그대로 사용하며, 이전 통합 테이블 수집 경로는 배치에서 호출하지 않습니다.</p></aside>'
    )


def chart_html(daily):
    peak = max([sum(int(row.get(key, 0)) for key in ('succeeded', 'failed', 'running')) for row in daily] + [1])
    ceiling = max(5, math.ceil(peak / 5) * 5)
    svg = '<svg class="chart" viewBox="0 0 640 250" role="img" aria-label="최근 7일 실행 현황, 실제 이력 기준"><title>서울 날짜별 성공·실패·기타 및 진행 중 실행</title>'
    for tick in range(6):
        y = 210 - tick * 36
        svg += f'<line x1="40" y1="{y}" x2="625" y2="{y}" class="chart-grid"/><text x="24" y="{y+4}" text-anchor="end">{ceiling*tick//5}</text>'
    for index, row in enumerate(daily[-7:]):
        x = 64 + index * 82
        y = 210.0
        svg += f'<g><title>{e(row["date"])} 성공 {int(row.get("succeeded",0))}, 실패 {int(row.get("failed",0))}, 기타·진행 {int(row.get("running",0))}</title>'
        for key, color in [('succeeded', '#46b984'), ('failed', '#f16a70'), ('running', '#94a5bd')]:
            height = int(row.get(key, 0)) / ceiling * 180
            y -= height
            svg += f'<rect x="{x}" y="{y:.2f}" width="31" height="{height:.2f}" rx="1" fill="{color}"/>'
        svg += f'<text x="{x+15}" y="235" text-anchor="middle">{e(row["date"][5:].replace("-", "."))}</text></g>'
    return svg + '</svg>'


def history_table(runs, csrf, compact=False):
    rows = ''
    for job in runs[:6] if compact else runs:
        source = '스케줄' if job.get('trigger_source') == 'schedule' else '수동'
        scheduled = source == '스케줄'
        notes = job.get('error_message') or ('실행 대기 중' if job['status'] == 'queued' else '—')
        cancel = ''
        if job['status'] in ('queued', 'running') and not job.get('legacy'):
            cancel = f'<form class="inline" method="post" action="/cancel/{e(job["run_id"])}">{hidden(csrf)}<button class="button danger">취소</button></form>'
        rows += f'<tr><td class="mono nowrap">{stamp(job.get("started_at") or job.get("created_at"))}</td><td><strong>{e(job["market"])} {e(mode_name(job["mode"]))}</strong></td><td><span class="tag {"scheduled" if scheduled else ""}">{source}</span></td><td>{badge(job["status"])}</td><td class="nowrap">{duration(job)}</td><td>{"스케줄러" if scheduled else "로컬 사용자"}</td><td class="notes" title="{e(notes)}">{e(notes)}</td><td class="row-actions"><a class="button" href="{href("logs", run_id=job["run_id"])}">상세보기</a>{cancel}</td></tr>'
    if not rows:
        rows = '<tr><td colspan="8" class="empty">실행 이력이 없습니다. 빠른 실행에서 첫 배치를 등록하세요.</td></tr>'
    return '<div class="table-scroll"><table><thead><tr><th>실행 시간</th><th>작업명</th><th>실행 유형</th><th>상태</th><th>소요 시간</th><th>시작자</th><th>비고</th><th>작업</th></tr></thead><tbody>' + rows + '</tbody></table></div>'


def search_form(page, query, market='', status=''):
    return f'<form class="search-form" method="get" action="/"><input type="hidden" name="page" value="{e(page)}">{icon("search")}<input type="search" name="q" value="{e(query)}" placeholder="배치 작업명 검색..." aria-label="배치 작업명 검색"><button class="button">검색</button></form>'


def schedule_html(schedules, csrf):
    rows = ''
    for schedule in schedules:
        options = ''.join(f'<option value="{mode}" {"selected" if schedule["mode"]==mode else ""}>{e(MODE_NAMES[mode])}</option>' for mode in MODES)
        rows += f'<article class="card schedule-card"><div class="section-heading"><h2>{e(schedule["market"])} 시장 예약</h2><span class="tag {"scheduled" if schedule["enabled"] else ""}">{"활성" if schedule["enabled"] else "중지"}</span></div><form method="post" action="/schedule/{e(schedule["market"])}">{hidden(csrf)}<label>실행 작업<select name="mode">{options}</select></label><label>Cron 표현식<input name="cron" value="{e(schedule["cron"])}" required maxlength="200"></label><label class="checkbox"><input type="checkbox" name="enabled" value="1" {"checked" if schedule["enabled"] else ""}>예약 활성화</label><p class="muted">시간대 Asia/Seoul · 분 / 시 / 일 / 월 / 요일</p><button class="button primary">예약 설정 저장</button></form></article>'
    return f'<div class="schedule-grid">{rows}</div><div class="card guidance"><h2>Cron 사용 안내</h2><p><code>0 12,18 * * 1-5</code> → 평일 12:00, 18:00 실행</p><p>숫자, *, 목록, 범위, /간격을 지원합니다. 0·7은 일요일입니다. 일·요일을 모두 제한하면 OR로 판정합니다.</p><p>중단 중 누락된 예약은 재생하지 않습니다. 저장 즉시 적용되며 재시작 후에도 유지됩니다.</p></div>'


def mcp_usage_html(report, filters):
    if report is None:
        return ''
    fields = [('start', '시작일', 'date'), ('end', '종료일', 'date'),
              ('tool', '도구명 (정확히 일치)', 'text'), ('client', 'User-Agent (정확히 일치)', 'text')]
    inputs = ''.join(f'<label>{label}<input type="{kind}" name="{key}" value="{e(filters.get(key, ""))}" maxlength="200"></label>' for key, label, kind in fields)
    def options(values, key):
        return ''.join(f'<option value="{e(value)}" {"selected" if filters.get(key, "")==value else ""}>{label}</option>' for value, label in values)
    kinds = options([('', '전체'), ('access', 'HTTP 접속'), ('tool', '도구 호출')], 'kind')
    states = options([('', '전체'), ('success', '성공'), ('failed', '실패'), ('cancelled', '취소'), ('200', 'HTTP 200'), ('202', 'HTTP 202'), ('400', 'HTTP 400'), ('403', 'HTTP 403'), ('404', 'HTTP 404'), ('500', 'HTTP 500')], 'status')
    content = f'<section class="card settings-card"><h2>이력 검색</h2><form method="get" action="/" class="manual-run-form"><input type="hidden" name="page" value="mcp">{inputs}<label>유형<select name="kind">{kinds}</select></label><label>결과<select name="status">{states}</select></label><button class="button primary">조회</button></form><p class="muted">보관 기간 {int(report["retention_days"])}일 · Asia/Seoul · 새 기록 저장 시 만료 이력을 정리합니다. 집계는 현재 검색 조건과 보관 중인 기록 기준입니다.</p><p class="muted">HTTP 접속 요청 수는 완료된 HTTP 요청 수이며 사용자 수·AI 수·세션 수가 아닙니다. 도구 호출은 별도로 셉니다. User-Agent는 클라이언트의 자기 신고 값이며 AI 신원을 인증하지 않습니다. IP는 서버에서 관찰한 주소로 프록시 주소일 수 있습니다.</p></section>'
    if not report['available']:
        return content + '<section class="card guidance"><p>아직 기록 DB가 없습니다. MCP 첫 접속·호출 이후 생성됩니다. 호출 후에도 이 상태면 MCP 로그와 전용 볼륨 연결을 확인하세요.</p></section>'
    counts = report['counts']
    content += '<section class="card guidance"><h2>선택 조건 집계</h2>' + ''.join(f'<p>{label}: <strong>{int(counts[key]):,}</strong></p>' for key, label in [('access', 'HTTP 접속 요청'), ('tools', '도구 호출'), ('errors', '실패·취소 호출')]) + '</section>'
    summaries = ''
    for title, data, key in [('일별 기록 수 (최근 90개 날짜, 접속+호출)', report['daily'], 'day'), ('도구별 호출 수 (최대 30개)', report['tools'], 'name'), ('클라이언트별 기록 수 (최대 30개)', report['clients'], 'client')]:
        lines = ''.join(f'<tr><td>{e(row[key])}</td><td>{int(row["count"]):,}</td></tr>' for row in data)
        summaries += f'<section class="card guidance"><details><summary>{title}</summary><div class="table-scroll"><table><thead><tr><th>구분</th><th>건수</th></tr></thead><tbody>{lines or "<tr><td colspan=2>기록이 없습니다.</td></tr>"}</tbody></table></div></details></section>'
    rows = ''
    for row in report['rows']:
        values = [stamp(row['occurred_at']), '도구 호출' if row['kind']=='tool' else 'HTTP 접속',
                  row['name'], row['status'], row['client'], row['ip'],
                  f'{row["duration_ms"]:.2f}', row['arguments'], row['error_type']]
        rows += '<tr>' + ''.join(f'<td>{e(value)}</td>' for value in values) + '</tr>'
    columns = ''.join(f'<th>{label}</th>' for label in ['시각', '유형', '도구/메서드', '결과', 'User-Agent', 'IP', '소요 ms', '조회 조건', '오류 유형'])
    page = report['page']
    links = ''
    if page > 1:
        links += f'<a class="button" href="{href("mcp", **{**filters, "page_number": page-1})}">이전</a> '
    if page * 50 < report['total']:
        links += f'<a class="button" href="{href("mcp", **{**filters, "page_number": page+1})}">다음</a>'
    content += f'<section class="card guidance"><h2>상세 기록 · 총 {int(report["total"]):,}건 · {page}페이지</h2><div class="table-scroll"><table><thead><tr>{columns}</tr></thead><tbody>{rows or "<tr><td colspan=9>조건에 맞는 기록이 없습니다.</td></tr>"}</tbody></table></div>{links}<p class="muted">페이지당 50건. 인증정보·응답 본문·예외 원문은 저장하지 않습니다. 현재 화면은 조회 전용입니다. 저장 실패는 MCP 서비스 로그에서 확인하세요.</p></section>'
    return content + summaries


def dashboard_html(*, page='dashboard', runs=None, schedules=None, summary=None, csrf='', query='', status='', detail=None, log='', notice='', error='', auto=False, audit=None, audit_filters=None):
    runs, schedules, summary = runs or [], schedules or [], summary or {}
    title, description = PAGES.get(page, PAGES['dashboard'])
    menu = [('dashboard', 'grid'), ('jobs', 'jobs'), ('schedules', 'calendar'), ('history', 'clock'), ('logs', 'file'), ('mcp', 'link'), ('settings', 'settings')]
    nav = ''.join(f'<a class="nav-item {"active" if page==key else ""}" href="{href(key)}">{icon(symbol)}<span>{e(PAGES[key][0])}</span></a>' for key, symbol in menu)
    count_failed = int(summary.get('today_failed', 0))
    content = ''
    if page == 'dashboard':
        cards = [('전체 배치 작업', summary.get('registered_jobs', len(MARKETS) * len(MODES)), '등록된 시장별 작업 수', 'layers', 'blue'), ('실행 중', summary.get('running', 0), f'현재 실행 중인 작업 · 대기 {summary.get("queued", 0)}건', 'play', 'green'), ('오늘 성공', summary.get('today_succeeded', 0), '오늘 완료된 작업 수', 'check', 'purple'), ('오늘 실패', count_failed, '오늘 실패한 작업 수', 'x', 'red')]
        content += '<section class="stats" aria-label="실행 요약">' + ''.join(f'<article class="card stat"><span class="stat-icon {color}">{icon(symbol)}</span><div><span class="stat-label">{label}</span><strong>{int(count):,}</strong><span class="stat-hint">{e(hint)}</span></div></article>' for label, count, hint, symbol, color in cards) + '</section>'
        content += f'<div class="dashboard-grid"><section class="card chart-card"><div class="section-heading"><h2>최근 7일 실행 현황</h2><div class="legend"><span class="legend-green">성공</span><span class="legend-red">실패</span><span class="legend-gray">기타·진행</span></div></div>{chart_html(summary.get("daily", []))}<p class="chart-caption">실제 실행 이력 기준 · 서울 날짜 · 완료 작업은 종료 시각 기준</p></section><section class="card quick-card"><div class="section-heading"><h2>빠른 실행</h2><a class="text-link" href="{href("jobs")}">전체보기 {icon("arrow")}</a></div>{manual_run_form(csrf)}</section></div>'
        content += f'<section class="card history-card"><div class="tabs"><a class="tab active" href="{href("history")}">최근 실행 이력</a><a class="tab" href="{href("history", status="running")}">실행 중 ({int(summary.get("running",0))})</a><a class="tab" href="{href("history", status="failed")}">실패 이력</a><a class="tab" href="{href("schedules")}">예약 설정 ({sum(bool(s["enabled"]) for s in schedules)})</a><a class="text-link all-history" href="{href("history")}">전체보기 {icon("arrow")}</a></div>{history_table(runs, csrf, compact=True)}</section>'
    elif page == 'jobs':
        content = f'<section class="card jobs-card"><div class="section-heading"><h2>수동 실행</h2><span class="muted">시장 → 작업 → 실행 등록</span></div>{manual_run_form(csrf)}</section>{manual_job_help()}'
    elif page == 'schedules':
        content = schedule_html(schedules, csrf)
    elif page in ('history', 'logs'):
        status_links = ''.join(f'<a class="tab {"active" if status==key else ""}" href="{href(page, status=key, q=query)}">{label}</a>' for key, label in [('', '전체'), ('running','실행 중'), ('queued','대기'), ('failed','실패'), ('succeeded','성공'), ('cancelled','취소'), ('interrupted','중단')])
        filtered = [r for r in runs if (not status or r['status'] == status or (status=='running' and r['status']=='cancelling')) and query.casefold() in f'{r["market"]} {r["mode"]} {mode_name(r["mode"])}'.casefold()]
        content = f'<section class="card history-card"><div class="section-heading"><h2>실행 목록</h2><span class="muted">최근 {len(runs)}건 · 전체 {int(summary.get("total_runs",0)):,}건</span></div>{search_form(page, query)}<div class="tabs">{status_links}</div>{history_table(filtered, csrf)}</section>'
        if page == 'logs':
            if detail:
                task_rows = ''.join(f'<tr><td>{e(TASK_NAMES.get(task["task_name"], task["task_name"]))}</td><td>{badge(task["status"])}</td><td>{e(task.get("exit_code", "—"))}</td></tr>' for task in detail.get('tasks', []))
                task_rows = task_rows or '<tr><td colspan="3">아직 하위 작업이 없습니다.</td></tr>'
                # 선택 결과는 긴 실행 목록보다 먼저 보여 클릭 직후 확인할 수 있게 합니다.
                content = f'<section class="card log-card"><div class="section-heading"><h2>{e(detail["market"])} {e(mode_name(detail["mode"]))}</h2>{badge(detail["status"])}</div><p class="mono muted">실행 ID {e(detail["run_id"])}</p><p class="error-text">{e(detail.get("error_message") or "")}</p><div class="table-scroll"><table><thead><tr><th>하위 작업</th><th>상태</th><th>종료 코드</th></tr></thead><tbody>{task_rows}</tbody></table></div><h3>실행 로그 <small>마지막 64KiB</small></h3><pre>{e(log or "아직 로그가 없습니다.")}</pre></section>' + content
            else:
                content += '<section class="card guidance"><p>위 실행 목록에서 상세보기를 선택하면 작업 결과와 로그가 표시됩니다.</p></section>'
    elif page == 'mcp':
        content = mcp_usage_html(audit, audit_filters or {})
    else:
        content = '<section class="card settings-card"><h2>운영 정책</h2><dl><dt>웹 렌더링</dt><dd>Python · 서버 렌더링 HTML/CSS · 프런트엔드 빌드 불필요</dd><dt>실행 엔진</dt><dd>자체 Python 러너 · Linux 컨테이너 · 단일 worker</dd><dt>시간대</dt><dd>Asia/Seoul (UTC+09:00)</dd><dt>실행 순서</dt><dd>JP/KR 전체 FIFO 순차 실행 · 동일 시장·모드 중복 등록 차단</dd><dt>대기열</dt><dd>최대 100건 · SQLite에 영속 저장</dd><dt>재시작</dt><dd>대기 작업 유지 · 실행 중 작업 interrupted 처리 · 누락 예약 재생 안 함</dd><dt>접속</dt><dd>로컬 단독 사용 · 로그인 없음 · 요청 위조 방지(CSRF) 유지</dd><dt>데이터 보호</dt><dd>취소는 커밋된 DB 변경을 롤백하지 않음 · CLI 직접 실행은 전체 큐 우회</dd></dl><p class="muted">내부 DB 접속 정보는 표시하지 않습니다. 인증이 없으므로 외부에 공개하지 마세요.</p></section>'
    notification_dot = '<span class="notification-dot"></span>' if count_failed else ''
    notification = f'<a class="notification" href="{href("history", status="failed")}" aria-label="오늘 실패 {count_failed}건">{icon("bell")}{notification_dot}</a>'
    refresh_params = {'q': query, 'status': status, 'auto': '0' if auto else '1'}
    if detail:
        refresh_params['run_id'] = detail['run_id']
    auto_link = f'<a class="refresh-toggle" href="{href(page, **refresh_params)}"><span class="live-dot {"on" if auto else ""}"></span>자동 새로고침 {"켜짐 · 10초" if auto else "꺼짐"}</a>' if page in ('dashboard','history','logs') else ''
    head_refresh = '<meta http-equiv="refresh" content="10">' if auto and page in ('dashboard','history','logs') else ''
    message = f'<div class="notice" role="status">{e(notice)}</div>' if notice else ''
    message += f'<div class="error-banner" role="alert">{e(error)}</div>' if error else ''
    return f'<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">{head_refresh}<title>{e(title)} · 배치 시스템</title><link rel="stylesheet" href="/assets/dashboard.css"></head><body><aside class="sidebar"><a class="brand" href="/">{icon("database")}<span>배치 시스템</span></a><nav aria-label="주 메뉴">{nav}</nav><div class="sidebar-footer"><span class="live-dot on"></span>자체 배치 엔진<span class="engine-label">PYTHON SERVER</span></div></aside><div class="workspace"><header class="topbar"><span class="topbar-location">운영 관리 <span>/</span> {e(title)}</span><div class="topbar-actions">{notification}<a class="profile" href="{href("settings")}"><span class="avatar">{icon("user")}</span><span>로컬 사용자</span><span class="chevron">⌄</span></a></div></header><main><div class="page-heading"><div><h1>{e(title)}</h1><p>{e(description)}</p></div>{auto_link}</div>{message}{content}<footer class="page-footer"><span>StockSearcher Batch Console</span><span>서버 렌더링 · Asia/Seoul</span></footer></main></div></body></html>'


def create_web_app(client=None, audit_store=None):
    client = client or BatchClient(os.environ.get('BATCH_API_URL', 'http://batch:8001'))
    css_path = Path(__file__).resolve().parent / 'static' / 'dashboard.css'

    def html(text, status=200):
        return HTMLResponse(text, status_code=status, headers=HEADERS)

    def redirect(url):
        return RedirectResponse(url, status_code=303, headers=HEADERS)

    async def form_data(request):
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 4096:
                raise ValueError('폼 입력 크기 초과')
        values = parse_qs(raw.decode('utf-8'), keep_blank_values=True, max_num_fields=12)
        if any(len(value) != 1 for value in values.values()):
            raise ValueError('중복 폼 입력')
        return {key: value[0] for key, value in values.items()}

    async def endpoint(request: Request):
        path, method = request.url.path, request.method
        secure = request.url.scheme == 'https'
        if path == '/health' and method == 'GET':
            return Response('ok', headers=HEADERS)
        if path == '/assets/dashboard.css' and method == 'GET':
            return Response(css_path.read_text(encoding='utf-8'), media_type='text/css', headers=HEADERS)
        if path.startswith('/assets/') or path in ('/login', '/logout'):
            return Response('Not found', status_code=404, headers=HEADERS)
        if method not in ('GET', 'POST'):
            return Response('Method not allowed', status_code=405, headers=HEADERS)
        cookie_csrf = request.cookies.get('batch_csrf', '')
        csrf = cookie_csrf if re.fullmatch(r'[A-Za-z0-9_-]{43}', cookie_csrf) else secrets.token_urlsafe(32)

        def page_html(text, status=200):
            response = html(text, status)
            if cookie_csrf != csrf:
                response.set_cookie('batch_csrf', csrf, httponly=True, secure=secure, samesite='strict')
            return response

        if method == 'POST':
            origin = request.headers.get('origin')
            expected_origin = f'{request.url.scheme}://{request.headers.get("host", "")}'
            if (origin and origin != expected_origin) or request.headers.get('sec-fetch-site') == 'cross-site':
                return page_html(dashboard_html(csrf=csrf, error='다른 출처의 요청은 허용하지 않습니다.'), 403)
            try:
                body = await form_data(request)
            except (ValueError, UnicodeError):
                return page_html(dashboard_html(csrf=csrf, error='잘못된 폼 입력입니다.'), 400)
            if cookie_csrf != csrf or not hmac.compare_digest(csrf.encode(), body.get('csrf', '').encode()):
                return page_html(dashboard_html(csrf=csrf, error='화면을 새로고침한 뒤 다시 실행하세요. 요청 위조 방지 값이 일치하지 않습니다.'), 403)
            try:
                if path == '/run':
                    if set(body) != {'csrf', 'market', 'mode'} or body['market'] not in MARKETS or body['mode'] not in MODES:
                        raise ValueError('허용된 시장과 작업을 선택하세요.')
                    result = await asyncio.to_thread(client.post, '/runs', {'market': body['market'], 'mode': body['mode']})
                    return redirect('/?' + urlencode({'page':'logs', 'run_id':result['run_id'], 'notice':'registered'}))
                if path.startswith('/schedule/'):
                    market = path.removeprefix('/schedule/')
                    if market not in MARKETS or not set(body) <= {'csrf', 'enabled', 'cron', 'mode'} or body.get('enabled', '') not in ('', '1'):
                        raise ValueError('예약 입력을 확인하세요.')
                    await asyncio.to_thread(client.put, f'/schedules/{market}', {'enabled':body.get('enabled')=='1', 'cron':body.get('cron',''), 'mode':body.get('mode','')})
                    return redirect('/?page=schedules&notice=saved')
                if path.startswith('/cancel/'):
                    run_id = path.removeprefix('/cancel/')
                    if len(run_id) != 32 or any(c not in '0123456789abcdef' for c in run_id):
                        raise ValueError('실행 ID가 올바르지 않습니다.')
                    await asyncio.to_thread(client.post, f'/runs/{run_id}/cancel')
                    return redirect('/?page=history&notice=cancelled')
                return page_html(dashboard_html(csrf=csrf, error='페이지를 찾을 수 없습니다.'), 404)
            except (BackendError, ValueError) as error:
                return page_html(dashboard_html(csrf=csrf, error=str(error)), getattr(error, 'status', 400))
        if path != '/':
            return page_html(dashboard_html(csrf=csrf, error='페이지를 찾을 수 없습니다.'), 404)
        page = request.query_params.get('page', 'dashboard')
        if page not in PAGES:
            return page_html(dashboard_html(csrf=csrf, error='페이지를 찾을 수 없습니다.'), 404)
        if page == 'mcp':
            filters = {key: request.query_params[key][:200] for key in ('start', 'end', 'kind', 'tool', 'client', 'status', 'page_number') if request.query_params.get(key)}
            try:
                report = await asyncio.to_thread((audit_store or AuditStore()).report, filters)
                return page_html(dashboard_html(page=page, csrf=csrf, audit=report, audit_filters=filters))
            except ValueError as error:
                return page_html(dashboard_html(page=page, csrf=csrf, error=str(error)), 400)
            except (OSError, sqlite3.Error):
                return page_html(dashboard_html(page=page, csrf=csrf, error='MCP 기록 DB를 조회할 수 없습니다. 볼륨·권한·DB 상태를 확인하세요.'), 503)
        query = request.query_params.get('q', '')[:100]
        status = request.query_params.get('status', '')
        detail, log = None, ''
        try:
            runs = (await asyncio.to_thread(client.get, '/runs'))['runs']
            schedules = (await asyncio.to_thread(client.get, '/schedules'))['schedules']
            summary = await asyncio.to_thread(client.get, '/summary')
            if page == 'logs' and request.query_params.get('run_id'):
                run_id = request.query_params['run_id']
                if len(run_id) != 32 or any(c not in '0123456789abcdef' for c in run_id):
                    raise ValueError('실행 ID가 올바르지 않습니다.')
                detail = await asyncio.to_thread(client.get, f'/runs/{run_id}')
                log = (await asyncio.to_thread(client.get, f'/runs/{run_id}/log'))['text']
            notices = {'registered':'배치를 대기열에 등록했습니다.', 'saved':'예약 설정을 저장했습니다.', 'cancelled':'취소 요청을 처리했습니다.'}
            return page_html(dashboard_html(page=page, runs=runs, schedules=schedules, summary=summary, csrf=csrf, query=query, status=status, detail=detail, log=log, notice=notices.get(request.query_params.get('notice'), ''), auto=request.query_params.get('auto')=='1'))
        except (BackendError, ValueError) as error:
            return page_html(dashboard_html(page=page, csrf=csrf, error=str(error)), getattr(error, 'status', 400))

    return Starlette(routes=[Route('/{path:path}', endpoint, methods=['GET', 'POST', 'PUT', 'DELETE'])])


def main():
    import uvicorn
    uvicorn.run(create_web_app(), host='0.0.0.0', port=8002)


if __name__ == '__main__':
    main()
