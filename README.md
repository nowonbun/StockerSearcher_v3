# StockSearcher 자체 배치 운영

PostgreSQL 수집·예측과 Nuxt 웹을 유지하고, 자체 Python 배치 서비스가 예약·수동 실행을 관리합니다. Prefect 런타임은 사용하지 않습니다.

## 구성

- `batch.service`: Compose 내부 관리 API, SQLite 영속 대기열, 단일 실행 worker, 예약 관리.
- `batch.scheduler`: Asia/Seoul 시간대 5필드 cron 판정.
- `batch.runner`: 시장별 Linux flock, 순차 작업, 시간 제한, 실행별 로그.
- `batch.history`: 기존 SQLite 작업 이력. 기존 이력을 삭제하거나 변환하지 않습니다.
- `batch.web`: 샘플 디자인 기반 Python 서버 렌더링 관리 웹. 대시보드·작업·예약·이력·로그·정책 화면을 제공합니다.
- 배치 웹은 Node/TypeScript 없이 실행됩니다. 기존 Nuxt 주식 조회 웹과 레거시 배치 API는 호환성을 위해 유지합니다.
- PostgreSQL `stock`: 주식 데이터. 기존 메타데이터 DB와 볼륨은 삭제하지 않습니다.

## 시작과 웹 관리

1. `.env.example`을 `.env`로 복사하고 DB 비밀번호를 설정합니다. 배치 웹과 API에는 로그인·인증 설정이 필요하지 않습니다.
2. 운영자가 전환 시 실행 중인 기존 배치를 확인하고 종료될 때까지 기다립니다.
3. 기존 Prefect 서비스를 명시적으로 중지한 뒤 새 구성을 실행합니다.

```bash
# 이전 컨테이너가 존재하는 전환 환경에서만 실행합니다.
docker stop <기존-prefect-runner-컨테이너명> <기존-prefect-server-컨테이너명>
# 볼륨을 삭제하지 않습니다. down -v 또는 DB DROP을 사용하지 마세요.
docker compose up -d --build --remove-orphans
```

Python 배치 웹은 `http://localhost:4200`(BATCH_UI_PORT 변경 가능)에 접속하면 바로 대시보드가 열립니다. 서버에서는 `http://서버주소:4200`으로 접속합니다. **로그인·관리 토큰·인증 세션은 없습니다.** 모든 변경 폼은 브라우저의 요청 위조를 막기 위한 CSRF 쿠키·숨김 값을 사용합니다. 이 값은 자동 처리되며 사용자가 입력할 필요가 없습니다. 서버 재시작 후에도 재로그인이 필요하지 않습니다.

- 배치 웹 8002는 모든 호스트 인터페이스의 4200 포트에 연결합니다. `BATCH_UI_BIND` 설정은 제거했으며 기존 .env에 남아 있어도 사용하지 않습니다. 내부 배치 API 8001은 공개하지 않습니다.
- Python 웹과 레거시 배치 프록시의 로컬 Host 제한을 제거했습니다. 주식 뷰어 4201도 모든 인터페이스에서 연결합니다. 다른 출처의 쓰기 요청 차단과 Python 폼 CSRF 검사는 유지합니다.
- 인증이 없으므로 포트에 접근할 수 있는 사람은 배치 실행·취소·예약 변경이 가능합니다. 접근 범위는 서버 방화벽 또는 사설망에서 관리하세요. 서버 방화벽·DNS·포트 전달 상태는 별도 확인이 필요합니다.
- 기존 주식 조회 웹은 `http://localhost:4201`이며 그대로 유지됩니다. 그 안의 기존 Nuxt 배치 화면은 레거시 호환용입니다. 새 디자인은 4200의 Python 화면을 사용하세요.
- 요약 카드와 7일 차트는 전체 SQLite 실행 이력을 집계합니다. 관리 큐와 작업 이력의 동일 ID는 중복 계산하지 않으며 서울 날짜·완료 시각을 기준으로 합니다. 이력 목록은 최근 100건입니다.
- 대시보드·이력·로그 화면에서 자동 새로고침을 켜면 10초마다 Python 서버에서 다시 렌더링합니다. 예약 편집 화면은 자동 새로고침하지 않습니다.
- `design/batch_preview.html`은 예시 데이터와 비활성 실행 버튼을 포함한 정적 디자인 참고물이며 실제 실행 화면이 아닙니다.

서버 구성은 `.env.server.example`을 참고합니다. 새 소스로 배치 이미지를 먼저 빌드·게시하고 `.env.server`의 이미지 태그를 갱신해야 합니다.

```bash
docker compose -f docker-compose.server.yml --env-file .env.server up -d --remove-orphans
```

기존 PostgreSQL 자격 증명·데이터 볼륨·모델 경로는 그대로 유지합니다. `STOCK_DB_NAME`과 초기화 SQL은 빈 볼륨 초기화에만 적용됩니다. 기존 Prefect 메타데이터는 자체 배치로 자동 이전하지 않으며 DB에 보존합니다. 기존 `history.sqlite3` 이력은 최근 실행 목록에서 읽을 수 있습니다.


### 이전 인증 버전에서 업데이트

실행 중인 배치를 먼저 확인한 뒤 다음 명령으로 소스와 새 실행 구성을 반영합니다. 실제 DB 비밀번호와 볼륨은 유지합니다.

```bash
docker compose up -d --build --force-recreate batch batch-web viewer
```

기존 `.env`에 남아 있는 이전 관리 토큰·인증 쿠키 설정은 더 이상 사용하지 않으므로 삭제해도 됩니다. DB 설정은 삭제하지 마세요. 기존 로그인 쿠키를 지우거나 별도 로그인할 필요는 없습니다. 브라우저에서 새로고침하여 바로 사용합니다.

## 실행 모드: 세 가지 패턴

시장(JP/KR)과 아래 작업 중 하나만 선택합니다. 수집이 성공하면 지표 계산까지 자동 실행되며 별도 원본 수집·지표 계산 버튼은 없습니다. 모델 학습 작업을 추가한 것은 아닙니다.

| 화면 이름 | CLI/API mode | 실행 순서 |
| --- | --- | --- |
| 통합 데이터 수집 | collect | 시세 수집 → 지표 자동 계산 |
| 예측 | predict | 일봉 예측 → 주봉 예측 |
| 수집·예측 | full | 시세 수집 → 지표 자동 계산 → 일봉 예측 → 주봉 예측 |

통합 수집은 현재 웹·MCP·v3 예측이 읽는 원본 OHLCV·지표 테이블을 갱신합니다. 내부적으로 `base_dataset_*`를 실행한 뒤 `indicator_dataset_*`를 실행합니다. 이전 `dataset/dataset_*`의 구형 통합 테이블 수집 경로는 배치에서 더 이상 호출하지 않습니다. DB 테이블을 삭제·통합하거나 데이터를 자동 복사하는 변경은 아닙니다.

시세 수집 또는 지표 계산 단계가 실패하면 이후 단계(예측 포함)를 실행하지 않습니다. 데이터 수집은 최근 설정 구간을 갱신하며 초기 과거 데이터와 필요한 `stock_data_split_*` 뷰·모델 파일이 준비됐는지는 별도 확인해야 합니다. 배치가 스키마를 자동 변경하지 않습니다.

### 이전 설정 호환

새 CLI·API·수동 실행·예약 입력은 `collect`, `predict`, `full`만 허용합니다. 서버가 단일 실행 잠금을 획득한 뒤 시작 복구 단계에서 **저장된 이전 예약·대기 작업**을 다음과 같이 전환합니다.

- base / indicators / split → collect (시세 수집과 지표 계산 전체 실행)
- daily / weekly → predict (일봉과 주봉 예측 모두 실행)
- collect / predict / full → 이름 유지, 위 표의 새 순서 적용

예약의 cron·활성 여부는 유지합니다. 전환 결과 같은 시장·작업으로 겹치는 대기 작업은 가장 먼저 등록된 한 건만 유지하고 나머지는 사유와 함께 cancelled 처리합니다. 완료된 실행·작업 이력은 원래 모드와 결과를 보존합니다. 이미 실행 중인 작업은 기존 재시작 복구 규칙대로 interrupted 처리합니다. 전환 전에 실행 중 배치가 없는지 확인하세요. 환경 변수의 이전 BATCH_SCHEDULE_MODE 값도 같은 규칙으로 초기화합니다.

## 예약·대기열·취소

- 기본 예약: JP 평일 12·18시, KR 평일 14·20시. 초기 모드는 collect입니다.
- 최초 실행 시 BATCH_SCHEDULE_MODE, BATCH_SCHEDULE_JP/KR로 초기화합니다. 이후 웹에서 저장한 SQLite 설정이 우선합니다. 환경 변수를 바꿔도 기존 웹 설정을 덮어쓰지 않습니다.
- cron은 `분 시 일 월 요일` 5필드, 숫자·`*`·목록·범위·`/간격` 지원. 요일은 0 또는 7=일요일입니다. 일·요일을 모두 제한하면 POSIX처럼 OR로 판정합니다.
- 같은 예약의 같은 UTC 분은 한 번만 처리합니다. 서비스 중단 중 놓친 예약은 재생하지 않습니다. BATCH_MAX_SCHEDULE_DELAY_SECONDS는 현재 분의 지연만 제한하며 과거 예약 복구 옵션이 아닙니다.
- 웹·예약 작업은 JP/KR 전체에서 한 개씩 FIFO 실행합니다. 대기열 최대 100개, 동일 시장·모드가 대기·실행 중이면 409로 거부합니다. 예약 중복은 서비스 로그에 기록하며 추가 큐를 만들지 않습니다.
- 취소는 대기 작업을 즉시 취소하거나 실행 wrapper에 종료를 요청합니다. wrapper는 하위 프로세스 그룹을 정리합니다. 이미 커밋된 DB 적재는 취소로 롤백되지 않습니다.
- 서비스 재시작 시 자체 running/cancelling 작업은 interrupted 처리하고 대기 작업은 유지합니다. Linux 부모 종료 신호로 하위 프로세스를 종료하며 오래된 PID를 재사용해 kill하지 않습니다.
- 서비스는 상태 볼륨의 service.lock으로 단일 인스턴스를 강제합니다. 상태 볼륨을 분리한 복수 스케줄러나 별도 host cron을 함께 실행하지 마세요.
- CLI는 기존처럼 직접 실행할 수 있습니다. CLI는 시장별 잠금으로만 보호되며 관리 서비스의 전체 FIFO에 참여하지 않습니다. 웹 관리 중에는 별도 CLI 실행을 피하세요.

```bash
docker compose exec -T batch python -m batch.cli run --market jp --mode collect
docker compose exec -T batch python -m batch.cli history --limit 20
```

## 이력·로그·보안

`batch-state` 볼륨의 manager.sqlite3에 큐·예약·예약 실행 키를, history.sqlite3에 작업 이력을 기록합니다. 로그 디렉터리에는 작업 로그와 실행 시작 실패 확인용 `.manager.log`가 남습니다. 웹은 마지막 64KiB를 표시합니다. 기존 완료 로그를 자동 삭제하지 않으므로 운영자가 보관 주기와 디스크 용량을 관리해야 합니다. 상세 로그에 민감한 값이 출력되지 않도록 수집 코드의 로그도 확인하세요.

하위 작업 기본 시간 제한은 7,200초이며 BATCH_TASK_TIMEOUT_SECONDS로 조정합니다. 배치 실행 환경은 Linux 컨테이너입니다. Windows에서는 mock 단위 테스트만 지원합니다.

## PostgreSQL MCP 서버

`mcp` 서비스는 PostgreSQL `stock` DB를 읽기 전용으로 조회하는 Streamable HTTP MCP 서버입니다. Docker Compose를 기동하면 호스트의 `8000` 포트에서 `/mcp` 경로를 제공합니다. `.env`의 `MCP_PORT`로 호스트 포트만 변경할 수 있습니다.

제공 도구는 `readme`, `usage`, `list_stocks`, `stock_data`, `stock_data_week`, `recommend_stocks`입니다. 데이터 조회 도구의 시장 코드는 `KR` 또는 `JP`만 지원하며, 데이터 변경 도구는 제공하지 않습니다. 기존 MCP 예측 도구 `list_predict_dates`, `predict_rows`는 제거했으며, 웹 예측 API는 변경하지 않았습니다.

AI는 `readme()`로 서버 용도·지원 시장·읽기 전용 범위·최신성 및 추천의 한계를 확인할 수 있습니다. `usage()`는 전체 도구의 설명·실제 MCP 입력 스키마·호출 인자 예시를 반환하며, `usage(tool="recommend_stocks")`로 특정 도구를 선택할 수 있습니다. 두 도움말 도구는 DB 연결 없이 동작합니다. MCP 초기 서버 안내에도 이 호출 순서를 포함하지만, AI가 반드시 호출하는지는 클라이언트 동작에 달려 있습니다.

`stock_data`와 `stock_data_week`도 웹·추천과 동일하게 각각 `stock_data_split_{kr,jp}`, `stock_data_split_week_{kr,jp}` 분리 데이터 뷰를 조회합니다. 날짜 범위 양 끝 포함, 최신 날짜 우선, 기본 조회 건수(일봉 2000·주봉 500)는 유지합니다. 반환 지표는 해당 뷰의 컬럼을 따르며 구형 테이블 전용 지표(DI·ADX 등)가 유지된다고 가정해서는 안 됩니다. 이 변경은 DB 데이터를 복사·재수집하거나 뷰를 생성하지 않으므로 실행 환경에 해당 뷰가 존재해야 합니다.

`recommend_stocks`는 웹의 볼린저밴드 스캐너와 같은 조건으로 후보를 거래대금 내림차순 조회합니다. 일목균형표나 모델 예측 기반 추천이 아니며 수익률을 보장하지 않습니다.

| 입력 | 기본값 및 의미 |
| --- | --- |
| `market` | `KR` 또는 `JP`, 기본 `KR` |
| `date` | `YYYY-MM-DD`. 생략 시 웹처럼 최근 30일 내 두 번째 최신 데이터 날짜, 한 개면 해당 날짜. 날짜가 없으면 빈 목록 |
| `weekly` | `false`: 일봉, `true`: 주봉 |
| `mode` | `upper`: 종가 > 볼린저 상단(기본), `lower`: 볼린저 하단 < 종가 < 60기간 이동평균 |
| `trans_amnt_min` | 거래대금 하한(초과 조건). 일봉 KR 10억 원·JP 5억 엔, 주봉 KR 50억 원·JP 25억 엔 |
| `close_max` | 종가 상한(미만 조건). KR 10만 원·JP 1,000엔 |
| `limit` | 최대 후보 수, 기본 20, 1~500 |

JP는 웹과 동일하게 `stocktype != 'ETF・ETN'` 조건을 적용합니다. 웹과 동일한 분리 데이터 뷰 `stock_data_split_*`를 조회하며, 조건을 충족하지 않는 종목을 채워 넣지 않습니다. 거래대금 동률 순서는 웹과 마찬가지로 보장하지 않습니다.

반환값에는 적용된 시장·날짜·필터·정렬 기준과 `rows`가 포함됩니다. 각 후보는 `rank`, `date`, `code`, `name`, `close`, `transAmnt`, 밴드 가격·대비 비율, 선정 근거 `reason`을 제공하며 하단 모드는 `ma60`도 제공합니다. 순위는 상승 확률이 아닌 거래대금 순위입니다.

호출 예: `recommend_stocks(market="KR", date="2026-10-23", mode="upper", weekly=false, limit=20)`.
변경을 실행 서버에 반영하려면 운영 환경에서 MCP 서비스를 재빌드·재기동하고 클라이언트 도구 목록을 갱신해야 합니다. 소스 변경만으로 원격 서버가 갱신되지는 않습니다.


## 검증과 복구

```bash
PYTHONPATH=src STOCK_DB_PASSWORD=test-password python -W error::ResourceWarning -m unittest discover -s tests -v
cd src/viewer && npm test
# 프로젝트 루트에서 실행하며 실제 비밀번호 대신 테스트 값으로 구성만 검사합니다.
docker compose config --quiet
docker compose -f docker-compose.server.yml --env-file .env.server config --quiet
```

회귀 범위와 실제 통합 검증 한계는 `issue/self_batch_requirements.md`를 참고하세요. Docker 이미지 빌드·실제 DB·외부 수집·브라우저·운영 전환은 단위 테스트로 검증하지 않으며 검증되지 않음입니다.

복구 시 자체 batch 서비스를 먼저 중지하고 이전 소스·이미지·Compose로 되돌린 뒤 기존 Prefect 서비스를 시작합니다. 동시에 두 스케줄러를 시작하지 않습니다. PostgreSQL·batch-state·로그·모델 볼륨을 삭제하지 않습니다. 신규 대기열은 구형 서비스가 처리하지 않습니다.
