# StockSearcher 경량 배치 운영

기존 수집·예측 스크립트를 실행하는 경량 구성입니다.

## 구성

- `batch.cli`: 터미널과 Prefect flow가 공통으로 호출하는 Python 러너입니다.
- `batch.history`: SQLite에 실행·작업 이력과 로그 경로를 기록합니다.
- `batch.runner`: 시장별 `flock` 잠금으로 cron, CLI, Prefect UI 사이의 중복 실행을 차단합니다.
- Prefect 3 self-hosted: 수동 실행, 정기 스케줄, 실행 상태를 확인하는 UI입니다.
- PostgreSQL `stock` DB: 기존 수집·예측 데이터 저장소입니다.

`STOCK_DB_NAME`은 빈 PostgreSQL 볼륨을 초기화할 때만 데이터베이스 이름을 정합니다. 기존 볼륨의 데이터베이스 이름은 변경하지 않습니다.

수집, 일간 예측, 주간 예측은 `full` 모드에서 순차 실행됩니다. 기본 `BATCH_SCHEDULE_MODE=collect`는 기존처럼 수집만 자동 실행합니다. 전체 파이프라인을 정기 실행하려면 이 값을 `full`로 설정합니다.

## 시작

```bash
cp .env.example .env
docker compose build
docker compose up -d
```

Prefect UI는 `http://localhost:4200`에서 엽니다. `prefect-runner`가 만든 `stocksearcher` 배포를 실행하면 시장과 모드를 입력하여 수동 실행할 수 있습니다. 다른 장비의 브라우저로 접속하는 경우 `.env`의 `PREFECT_SERVER_UI_API_URL`을 브라우저가 실제로 사용하는 `http://<host-ip-or-hostname>:4200/api` 주소로 설정해야 합니다.

## PostgreSQL MCP 서버

`mcp` 서비스는 PostgreSQL `stock` DB를 읽기 전용으로 조회하는 Streamable HTTP MCP 서버입니다. Docker Compose를 기동하면 호스트의 `8000` 포트에서 `/mcp` 경로를 제공합니다. `.env`의 `MCP_PORT`로 호스트 포트만 변경할 수 있습니다.

제공 도구는 `readme`, `usage`, `list_stocks`, `stock_data`, `stock_data_week`, `recommend_stocks`입니다. 데이터 조회 도구의 시장 코드는 `KR` 또는 `JP`만 지원하며, 데이터 변경 도구는 제공하지 않습니다. 기존 MCP 예측 도구 `list_predict_dates`, `predict_rows`는 제거했으며, 웹 예측 API는 변경하지 않았습니다.

AI는 `readme()`로 서버 용도·지원 시장·읽기 전용 범위·최신성 및 추천의 한계를 확인할 수 있습니다. `usage()`는 전체 도구의 설명·실제 MCP 입력 스키마·호출 인자 예시를 반환하며, `usage(tool="recommend_stocks")`로 특정 도구를 선택할 수 있습니다. 두 도움말 도구는 DB 연결 없이 동작합니다. MCP 초기 서버 안내에도 이 호출 순서를 포함하지만, AI가 반드시 호출하는지는 클라이언트 동작에 달려 있습니다.

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

## 수동 실행

Prefect UI의 `Run` 화면에서 Parameter는 선택 목록으로 표시됩니다.

| Parameter | 선택 값 | 의미 |
| --- | --- | --- |
| `market` | `JP`, `KR` | 실행할 일본 또는 한국 시장 |
| `mode` | `collect`, `predict`, `full`, `daily`, `weekly` | 실행할 작업 종류 |
| `trigger_source` | `prefect-ui`, `prefect-schedule` | 실행 시작 경로를 이력에 기록하는 값 |

`mode`의 동작은 다음과 같습니다.

- `collect`: dataset 수집만 실행
- `predict`: 일간 예측 후 주간 예측 실행
- `full`: dataset 수집 후 일간 예측, 주간 예측을 순차 실행
- `daily`: 일간 예측만 실행
- `weekly`: 주간 예측만 실행

수동 실행에서는 `market`과 `mode`를 선택하고 `trigger_source`는 기본값 `prefect-ui`로 둡니다. `prefect-schedule`은 Prefect 정기 스케줄 전용 값이며, 늦게 시작된 예약 실행을 건너뛰는 처리에 사용됩니다. CLI는 이 Prefect Flow를 거치지 않고 배치 러너를 직접 실행합니다.

컨테이너에서 실행합니다.

```bash
docker compose exec -T prefect-runner python -m batch.cli run --market jp --mode collect
docker compose run --rm prefect-runner python -m batch.cli run --market jp --mode full
docker compose run --rm prefect-runner python -m batch.cli history --limit 20
```

Prefect UI 실행과 같은 시장·같은 시간에 겹치면 Linux `flock`이 두 번째 실행을 종료 코드 2로 거부합니다.

## Prefect 정기 실행

`docker compose up -d --build --remove-orphans`로 `prefect-runner`를 시작하면 `stocksearcher` 배포와 JP/KR 스케줄을 등록합니다. 기본 모드는 수집 전용이며, JP는 평일 12:00·18:00, KR은 평일 14:00·20:00에 `Asia/Seoul` 시간대로 실행합니다.

`BATCH_SCHEDULE_MODE`을 `full`로 설정하면 수집 후 일간·주간 예측까지 순차 실행합니다. 스케줄러는 Prefect 하나만 사용하며, host cron은 등록하지 마세요. Prefect UI에서 `Flows` → `stocksearcher-batch` → `stocksearcher`를 열어 `Schedules`와 `Upcoming`의 cron 및 다음 실행 시간을 확인하거나 수정할 수 있습니다. JP/KR 배치는 동시에 시작되지 않도록 배포 실행 수를 1개로 제한합니다. 컨테이너 또는 Prefect 재기동 후 300초보다 늦어진 예약 실행은 `skipped-stale`로 기록하고 시작하지 않습니다. 이 기준은 `.env`의 `BATCH_MAX_SCHEDULE_DELAY_SECONDS`로 변경할 수 있습니다.

### 배치 러너 기동

`prefect-runner`만 기동하거나 다시 만들 때는 다음 명령을 사용합니다. 이 서비스는 `prefect-server`가 `healthy`가 된 뒤에 시작됩니다.

```bash
docker compose up -d prefect-runner
```

Prefect 서버 설정 또는 이미지가 바뀐 경우에는 서버와 배치 러너를 함께 다시 만듭니다.

```bash
docker compose up -d prefect-server prefect-runner
docker compose up -d --build --force-recreate prefect-server prefect-runner
docker compose up -d --build --force-recreate viewer
```

## 이력과 로그

`batch-state` Docker 볼륨에 다음이 저장됩니다.

- `history.sqlite3`: 실행 ID, 시장, 모드, 시작·종료 시각, 상태, 오류, 로그 경로
- `logs/`: 각 실행의 표준 출력과 표준 오류 통합 로그
- `locks/`: 실행 중인 시장별 Linux advisory lock 파일

SQLite는 WAL 모드와 10초 busy timeout을 사용합니다.

| 저장소 | 컨테이너 경로 | 호스트 경로 | 저장 내용 |
| --- | --- | --- | --- |
| `batch-state` Docker 볼륨 | `/var/lib/stock-batch` | Docker 관리 경로 | 자체 실행 이력 SQLite(`history.sqlite3`), 하위 작업 전체 로그, 시장별 잠금 파일 |
| Prefect 메타데이터 | PostgreSQL DB | Docker `postgres` 서비스 | Deployment, 스케줄, Flow Run, Prefect UI 로그 메타데이터 |

Prefect 서버는 시작 시 PostgreSQL에 필요한 메타데이터 테이블을 생성·업그레이드합니다. Prefect UI에는 작업 시작·종료·취소·시간 초과 같은 요약 이벤트만 기록합니다. dataset/predict의 행 단위 상세 출력은 PostgreSQL에 중복 저장하지 않고 `batch-state`의 실행별 로그 파일에서 확인합니다.

기존 `./data/metadata/prefect.db`의 SQLite 이력은 PostgreSQL로 자동 이전되지 않습니다. 필요하면 전환 전에 해당 파일을 별도로 백업합니다.
각 수집·예측 하위 작업의 기본 시간 제한은 7,200초이며, `BATCH_TASK_TIMEOUT_SECONDS`로 변경할 수 있습니다.

## 검증

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
docker compose config
docker compose -f docker-compose.server.yml --env-file .env.server config
```

Docker 이미지 빌드와 실제 DB·수집 실행은 이 저장소의 정적 검증에 포함되지 않습니다.
