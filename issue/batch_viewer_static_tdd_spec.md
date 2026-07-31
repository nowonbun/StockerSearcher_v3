# 배치·웹앱 정적 계약 테스트 사양

## 목적

Vitest와 Nuxt 런타임을 설치·실행할 수 없는 환경에서 배치의 실행 가능한 Python 계약과 웹앱 서버 소스의 정적 계약을 검증한다.

## 범위

| 영역 | 대상 | 검증 방식 |
| --- | --- | --- |
| 배치 | `src/batch/config.py`, `history.py`, `cli.py`, `runner.py` | Python `unittest` |
| 웹앱 API | `src/viewer/server/api/*.ts`, `utils/stock-api.ts`, `utils/db.ts` | Python `unittest`의 UTF-8 소스 계약 검사 |

## 배치 수용 기준

| ID | 계약 | 증거 테스트 |
| --- | --- | --- |
| BATCH-01 | 환경 변수에서 source/state/log 경로와 양수 timeout을 읽는다. | `test_batch_config_reads_paths_and_rejects_non_positive_timeout` |
| BATCH-02 | 실행·작업 상태를 SQLite 행으로 기록하고 최근 실행 결과를 반환한다. | `test_history_store_persists_task_and_run_statuses` |
| BATCH-03 | CLI는 JP/KR 시장, 등록된 mode, history limit을 파싱한다. | `test_cli_parser_requires_supported_market_mode_and_history_limit` |
| BATCH-04 | mode별 작업 순서, 시장 lock, 출력 스트림, wrapper 명령을 유지한다. | 기존 `test_batch_runner.py` 테스트 |
| BATCH-05 | process wrapper는 비Linux 실행을 거부하고, 취소·정상 종료·SIGKILL escalation 경계를 유지한다. | `test_batch_process_wrapper.py` |

## 웹앱 정적 수용 기준

| ID | 계약 | 증거 테스트 |
| --- | --- | --- |
| VIEWER-01 | 14개 API route 파일은 각 파일명에 대응하는 endpoint로 `handleStockApi`를 호출한다. | `test_every_api_route_delegates_to_its_expected_stock_api_endpoint` |
| VIEWER-02 | market/date/code/number 입력 검증과 SQL parameter 배열이 소스에 존재한다. | `test_stock_api_keeps_query_validation_and_parameterized_database_calls` |
| VIEWER-03 | DB password 가드, parameterized query, date/numeric normalizer가 소스에 존재한다. | `test_database_utility_requires_password_and_normalizes_date_and_numeric_values` |

## Vitest 웹앱 단위 테스트

`src/viewer/tests/stock-api.test.ts`는 Vitest와 mock된 `h3`·`db` 경계를 사용해 `handleStockApi`를 직접 실행한다.

| ID | 계약 | 증거 테스트 |
| --- | --- | --- |
| VIEWER-RT-01 | market 미지정 시 KR을 사용하고 prediction 날짜를 반환한다. | `uses KR as the default market and normalizes prediction dates` |
| VIEWER-RT-02 | 지원하지 않는 market, 형식이 잘못된 날짜·code, 숫자가 아닌 scanner 입력을 DB 호출 전에 400 오류로 거부한다. | 입력 거부 테스트 3개 |
| VIEWER-RT-03 | JP prediction은 SQL parameter 배열을 전달하고 숫자 응답을 정규화한다. | `passes prediction query parameters and normalizes numeric response fields` |
| VIEWER-RT-04 | KR 주봉 series는 행을 시간순으로 뒤집고 일봉 전용 이동평균을 제외한다. | `reverses weekly series rows and omits daily-only moving averages` |

실행 명령:

```powershell
Set-Location src/viewer
npm test
```

Windows 환경에서 Vitest 기본 `forks` pool은 worker 생성 권한 오류가 발생할 수 있으므로, `package.json`의 test 스크립트는 단일 `threads` pool을 사용한다.

## 실행

```powershell
$env:PYTHONPATH = "$PWD\src"
$env:STOCK_DB_PASSWORD = 'test-password'
python -m unittest tests/test_batch_runner.py tests/test_batch_process_wrapper.py tests/test_prediction_filters.py tests/test_viewer_static_contracts.py tests/test_split_dataset.py tests/test_regression_guard_contract.py -v
```

## 판정과 한계

- 통과: 모든 Python 테스트가 통과하고 실제 DB·네트워크·브라우저 호출이 없다.
- 실패: 실패한 테스트, fixture, traceback을 보존하고 소스 계약 또는 테스트 기대값을 재검토한다.
- 웹앱 정적 검사는 TypeScript 텍스트 계약만 확인한다. Vitest 단위 테스트는 mock된 H3·DB 경계에서 `handleStockApi`를 실행한다.
- 실제 Nuxt 서버, HTTP 요청, PostgreSQL 결과 매핑, 브라우저 렌더링은 **검증되지 않음**이다.

## 되돌리기

이 사양과 관련 테스트는 실행 코드 동작을 변경하지 않는다. 되돌리기는 `tests/test_viewer_static_contracts.py`, `tests/test_batch_process_wrapper.py`, `tests/test_regression_guard_contract.py`와 이 사양 파일을 제거하고 `tests/test_batch_runner.py` 변경을 이전 Git 버전으로 복원하는 방식으로 수행한다.
