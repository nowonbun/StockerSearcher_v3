# StockSearcher 경량 배치 운영

Airflow를 사용하지 않고 기존 수집·예측 스크립트를 실행하는 경량 구성입니다.

## 구성

- `batch.cli`: 터미널과 Prefect flow가 공통으로 호출하는 Python 러너입니다.
- `batch.history`: SQLite에 실행·작업 이력과 로그 경로를 기록합니다.
- `batch.runner`: 시장별 `flock` 잠금으로 cron, CLI, Prefect UI 사이의 중복 실행을 차단합니다.
- Prefect 3 self-hosted: 수동 실행, 정기 스케줄, 실행 상태를 확인하는 UI입니다.
- PostgreSQL `stock` DB: 기존 수집·예측 데이터 저장소입니다. Airflow 메타데이터 DB는 사용하지 않고, 기존 PostgreSQL 볼륨에는 롤백을 위해 남겨 둡니다.

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

제공 도구는 `list_stocks`, `stock_data`, `stock_data_week`, `list_predict_dates`, `predict_rows`입니다. 모든 도구는 `KR` 또는 `JP` 시장 코드만 받으며, 데이터 변경 도구는 제공하지 않습니다.

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

`docker compose up -d --build --remove-orphans`로 `prefect-runner`를 시작하면 `stocksearcher` 배포와 JP/KR 스케줄을 등록합니다. 기본 모드는 기존 Airflow와 같은 수집 전용이며, JP는 평일 12:00·18:00, KR은 평일 14:00·20:00에 `Asia/Seoul` 시간대로 실행합니다.

`BATCH_SCHEDULE_MODE`을 `full`로 설정하면 수집 후 일간·주간 예측까지 순차 실행합니다. 스케줄러는 Prefect 하나만 사용하며, host cron은 등록하지 마세요. Prefect UI에서 `Flows` → `stocksearcher-batch` → `stocksearcher`를 열어 `Schedules`와 `Upcoming`의 cron 및 다음 실행 시간을 확인하거나 수정할 수 있습니다. JP/KR 배치는 동시에 시작되지 않도록 배포 실행 수를 1개로 제한합니다. 컨테이너 또는 Prefect 재기동 후 300초보다 늦어진 예약 실행은 `skipped-stale`로 기록하고 시작하지 않습니다. 이 기준은 `.env`의 `BATCH_MAX_SCHEDULE_DELAY_SECONDS`로 변경할 수 있습니다.

## 이력과 로그

`batch-state` Docker 볼륨에 다음이 저장됩니다.

- `history.sqlite3`: 실행 ID, 시장, 모드, 시작·종료 시각, 상태, 오류, 로그 경로
- `logs/`: 각 실행의 표준 출력과 표준 오류 통합 로그
- `locks/`: 실행 중인 시장별 Linux advisory lock 파일

SQLite는 WAL 모드와 10초 busy timeout을 사용합니다.

| 저장소 | 컨테이너 경로 | 호스트 경로 | 저장 내용 |
| --- | --- | --- | --- |
| `batch-state` Docker 볼륨 | `/var/lib/stock-batch` | Docker 관리 경로 | 자체 실행 이력 SQLite(`history.sqlite3`), 하위 작업 전체 로그, 시장별 잠금 파일 |
| Prefect 메타데이터 | `/root/.prefect` | `./data/metadata` | Prefect SQLite(`./data/metadata/prefect.db`): Deployment, 스케줄, Flow Run, Prefect UI 로그 메타데이터 |

Prefect UI에는 작업 시작·종료·취소·시간 초과 같은 요약 이벤트만 기록합니다. dataset/predict의 행 단위 상세 출력은 Prefect SQLite에 중복 저장하지 않고 `batch-state`의 실행별 로그 파일에서 확인합니다.

기존 `prefect-state` Docker 볼륨에 저장된 Deployment와 실행 이력을 유지하려면, Compose를 재기동하기 전에 현재 서버에서 아래와 같이 복사합니다. 이 과정은 기존 볼륨을 삭제하지 않습니다.

```bash
mkdir -p ./data/metadata
docker run --rm \
  -v stockersearcher_v3_prefect-state:/src:ro \
  -v "$(pwd)/data/metadata:/dst" \
  busybox cp -a /src/. /dst/
```
각 수집·예측 하위 작업의 기본 시간 제한은 7,200초이며, `BATCH_TASK_TIMEOUT_SECONDS`로 변경할 수 있습니다.

## 롤백

전환 전에 현재 Compose 파일을 Git 태그 또는 커밋으로 보관합니다. Airflow로 되돌릴 때는 이전 Compose 파일을 복원한 뒤 다음 순서로 실행합니다.

```bash
docker compose up -d postgres
docker compose run --rm airflow-init
docker compose up -d airflow-webserver airflow-scheduler viewer
```

기존 PostgreSQL 볼륨과 `airflow` 데이터베이스는 삭제하지 마세요. 전환 후 최소 한 번의 정기 수집 주기가 성공한 뒤에만 별도 백업·정리 결정을 합니다.

## 검증

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
docker compose config
docker compose -f docker-compose.server.yml --env-file .env.server config
```

Docker 이미지 빌드와 실제 DB·수집 실행은 이 저장소의 정적 검증에 포함되지 않습니다.
