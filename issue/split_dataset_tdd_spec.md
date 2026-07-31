# 분리 데이터셋 TDD 사양

## 1. 문서 계약

| 항목 | 값 |
| --- | --- |
| 변경 식별자 | `split-dataset-tdd-spec-v2` |
| 대상 독자 | `src/dataset/split_dataset.py`를 변경하거나 테스트를 추가하는 개발자 |
| 단일 사실 출처 | `src/dataset/split_dataset.py`, `tests/test_split_dataset.py` |
| 테스트 프레임워크 | Python 표준 라이브러리 `unittest` |
| 테스트 목적 | 외부 서비스 없이 순수 변환과 DB 경계 호출 계약을 재현 가능하게 검증 |
| 문서 범위 | 분리 데이터셋 수집·지표 계산 모듈의 단위·경계 테스트 |
| 비범위 | 실제 DB, 네트워크, Selenium, FinanceDataReader, ChromeDriver, migration, 웹앱 |

이 문서에서 **구현됨**은 현재 `tests/test_split_dataset.py`에 존재하는 테스트를 뜻한다. **추가 필요**는 요구 계약이지만 아직 테스트 파일에 구현되지 않은 항목을 뜻한다. 구현되지 않은 항목을 통과한 것으로 취급해서는 안 된다.

## 2. 변경 대상과 불변 조건

### 2.1 대상 모듈

| 경로 | 책임 | 근거 |
| --- | --- | --- |
| `src/dataset/split_dataset.py` | KR/JP OHLCV 수집, 기술지표 계산, split 테이블 upsert | 함수 정의: 38~188행 |
| `tests/test_split_dataset.py` | 위 모듈의 결정적 단위·경계 테스트 | 현재 테스트 파일 |

### 2.2 반드시 유지할 불변 조건

1. 시장은 `KR`과 `JP`만 허용한다. 지원하지 않는 시장은 `ValueError`로 거부한다.
2. 일봉과 주봉은 각기 다른 `stock_ohlcv_*` 및 `stock_indicator_*` 테이블을 사용한다.
3. OHLCV 행은 `(code, date)` 충돌 시 갱신하는 upsert 계약을 유지한다.
4. 거래량이 0인 OHLCV 행은 저장 payload에서 제외한다.
5. KR 주봉은 `W-FRI` 기준으로 집계한다.
6. JP 주봉 날짜는 해당 주의 월요일으로 정규화한다.
7. 지표 초기 구간의 계산 불가 값은 SQL `NULL`에 대응하는 Python `None`으로 유지한다.
8. 빈 OHLCV payload는 DB 연결을 열지 않는다.
9. 단위 테스트는 실제 DB 연결, 네트워크 호출, 브라우저 실행을 해서는 안 된다.

## 3. 데이터 계약

### 3.1 테이블 매핑 계약

| 시장 | 주기 | OHLCV 테이블 | 지표 테이블 | 종목 테이블 |
| --- | --- | --- | --- | --- |
| KR | 일봉 | `stock_ohlcv_kr` | `stock_indicator_kr` | `stock_list_kr` |
| KR | 주봉 | `stock_ohlcv_week_kr` | `stock_indicator_week_kr` | `stock_list_kr` |
| JP | 일봉 | `stock_ohlcv_jp` | `stock_indicator_jp` | `stock_list_jp` |
| JP | 주봉 | `stock_ohlcv_week_jp` | `stock_indicator_week_jp` | `stock_list_jp` |

근거: `src/dataset/split_dataset.py`의 `TABLES` 정의(30~35행).

### 3.2 OHLCV 행 계약

`_kr_rows`와 `_jp_rows`의 반환 행은 아래 순서를 사용한다.

```text
(date, open, high, low, close, volume)
```

`_upsert_ohlcv`는 각 반환 행 앞에 `code`를 추가하여 아래 payload를 만든다.

```text
(code, date, open, high, low, close, volume)
```

숫자 값은 `float`에서 Python `round()`로 정수화한다. 결측 OHLCV 값은 KR 변환에서 `dropna()`로 제외된다. 거래량이 0 이하인 행은 KR·JP 변환 모두에서 제외된다.

### 3.3 KR 주봉 집계 계약

동일한 `W-FRI` 구간의 원본 행에서 다음 값을 계산한다.

| 컬럼 | 집계 |
| --- | --- |
| `Open` | 첫 값 |
| `High` | 최댓값 |
| `Low` | 최솟값 |
| `Close` | 마지막 값 |
| `Volume` | 합계 |

주봉 결과의 `Close`가 결측이면 해당 주는 제외한다. 주봉 집계 뒤 거래량이 0이면 해당 행도 제외한다.

### 3.4 JP 날짜 계약

입력 Yahoo timestamp는 밀리초 단위다. 일봉은 JST 날짜 문자열을 사용한다. 주봉은 JST 기준 날짜에서 요일 수만큼 역산하여 월요일 날짜 문자열을 사용한다. 이 규칙은 주봉 데이터의 중복 날짜를 방지하기 위한 계약이며, 구현 근거는 `dataset_jp._ts_to_date(..., normalize_to_monday=True)` 호출이다.

### 3.5 지표 행 계약

`_indicator_rows` 입력 DataFrame에는 다음 컬럼이 필요하다.

```text
date, high, low, close
```

반환 행은 다음 순서를 사용한다.

```text
(date,
 ma_5, ma_20, ma_50, ma_60, ma_120, ma_240,
 bollinger_upper, bollinger_lower, bollinger_lower_3,
 conversion, base, span_a, span_b, lagging)
```

| 지표 | 계산 계약 |
| --- | --- |
| 이동평균 | `close.rolling(window=N).mean()`, N은 5·20·50·60·120·240 |
| 볼린저 상단/하단 | 60일 이동평균 ± 60일 표준편차 1배 |
| 볼린저 하단 3배 | 60일 이동평균 − 60일 표준편차 3배 |
| 전환선 | 9일 고가 최댓값과 저가 최솟값의 평균 |
| 기준선 | 26일 고가 최댓값과 저가 최솟값의 평균 |
| 선행스팬 A | 전환선·기준선 평균을 26행 뒤로 이동 |
| 선행스팬 B | 52일 고가/저가 평균을 26행 뒤로 이동 |
| 후행스팬 | 종가를 26행 앞으로 이동 |

계산 불가 구간의 `NaN`은 `_round_or_none`을 통해 `None`으로 변환한다.

## 4. 테스트 격리 설계

### 4.1 환경 준비

`function.static` import는 `STOCK_DB_PASSWORD`를 요구한다. 따라서 테스트 모듈 import **이전**에 다음 값을 설정한다.

```python
os.environ.setdefault("STOCK_DB_PASSWORD", "test-password")
```

이 값은 테스트 프로세스의 import 조건만 만족시키며 실제 DB 접속에 사용해서는 안 된다.

### 4.2 허용되는 test double

| 외부 경계 | 사용 방식 | 검증 목적 |
| --- | --- | --- |
| `psycopg.connect` | `unittest.mock.patch.object` | DB 접속 없이 호출 여부·인수·cursor 전달값 확인 |
| connection/cursor | `MagicMock` context manager | `executemany` SQL과 payload 확인 |
| pandas DataFrame | 메모리 fixture | KR 집계 및 지표 계산 확인 |
| JP raw dict | 메모리 fixture | timestamp, 가격, 거래량 변환 확인 |

### 4.3 금지되는 test double

- 실제 PostgreSQL 서버를 대체하는 로컬 DB를 단위 테스트에 사용해서는 안 된다.
- 실제 FinanceDataReader 응답을 단위 테스트 fixture로 사용해서는 안 된다.
- 실제 Selenium driver 또는 ChromeDriver를 생성해서는 안 된다.
- 시간·네트워크 실패를 숨기기 위한 무제한 재시도 mock을 추가해서는 안 된다.

## 5. TDD 테스트 매트릭스

### 5.1 현재 구현된 테스트

| ID | 테스트 함수 | 입력 fixture | 통과 기준 | 실패 시 확인할 계약 |
| --- | --- | --- | --- | --- |
| TDD-001 | `test_tables_selects_market_and_frequency_specific_tables` | KR 일봉, 소문자 JP 주봉, US 시장 | 정확한 테이블명 반환; US는 `ValueError` | 3.1 테이블 매핑 |
| TDD-002 | `test_kr_rows_excludes_zero_volume_and_aggregates_weekly` | 3일 OHLCV DataFrame, 중간 거래량 0 | 일봉의 0 거래량 제거; 주봉 OHLCV 집계 | 3.2·3.3 OHLCV/주봉 |
| TDD-003 | `test_jp_rows_filters_zero_volume_and_normalizes_weekly_date` | 2개 JP raw candle, 하나는 거래량 0 | 하나의 행만 반환; 날짜는 `2026-01-05` | 3.2·3.4 JP 날짜 |
| TDD-004 | `test_round_or_none_handles_missing_and_numeric_values` | `NaN`, `12.6` | `None`, `13` 반환 | 3.5 결측 반올림 |
| TDD-005 | `test_indicator_rows_preserve_dates_and_emit_nulls_before_windows_complete` | 80행 연속 가격 DataFrame | 날짜 보존; 첫 5일 평균은 `None`; 60일 평균·볼린저 상단 값 확인 | 3.5 지표 행 |
| TDD-006 | `test_upsert_skips_database_connection_for_empty_payload` | 빈 행 iterable | `psycopg.connect` 미호출 | 2.2 빈 payload |
| TDD-007 | `test_upsert_uses_market_table_and_code_prefixed_payload` | JP 주봉 1행과 mock connection | 주봉 JP 테이블, conflict 키, code-prepend payload 확인 | 2.2 upsert |

### 5.2 추가가 필요한 우선 테스트

아래 항목은 현재 테스트 파일에 없다. 소스 변경 전에 해당 테스트를 먼저 추가한다.

| ID | 우선순위 | 계약 | 최소 fixture | 기대 결과 |
| --- | --- | --- | --- | --- |
| TDD-008 | 구현됨 | `_kr_rows`가 `None`과 빈 DataFrame을 처리 | `None`, `pd.DataFrame()` | 빈 리스트 반환 |
| TDD-009 | 구현됨 | `_kr_rows`가 필수 OHLCV 컬럼 결측을 거부 | `Close` 또는 `Volume` 없는 DataFrame | 누락 컬럼명을 포함한 `ValueError` |
| TDD-010 | 구현됨 | `_jp_rows`가 `None` 가격 또는 거래량을 제외 | raw dict의 `None` 값 | 유효 candle만 반환 |
| TDD-011 | 구현됨 | `_upsert_ohlcv`의 KR 일봉 SQL 대상 | KR 일봉 1행 | `stock_ohlcv_kr` 및 payload 순서 확인 |
| TDD-012 | 구현됨 | `_upsert_ohlcv`가 모든 시장·주기 조합을 선택 | KR/JP × 일/주 | 4개 OHLCV 테이블에 대한 parameterized 검증 |
| TDD-013 | 구현됨 | `calculate_indicators` 일봉 전용 모드 | mocked SELECT/INSERT connection | 일봉만 조회·insert하고 주봉 SQL은 호출하지 않음 |
| TDD-014 | 구현됨 | `calculate_indicators` 빈 코드 목록 | mocked `fetchall()` = `[]` | 추가 DB 읽기·쓰기 없음 |
| TDD-015 | 구현됨 | `calculate_indicators` 빈 지표 payload | 코드 1개, `_indicator_rows` = `[]` | indicator `executemany` 미호출 |
| TDD-016 | 구현됨 | 지표 payload의 code prefix와 16개 값 순서 | 1개 코드와 결정적 1개 row | SQL placeholder 수와 payload 수가 일치 |
| TDD-017 | 구현됨 | Ichimoku 경계 | 8·9·25·26·51·52·77·78행 DataFrame | 각 창·shift 전후의 `None`/값 위치 고정 |
| TDD-018 | 구현됨 | `collect_kr` 실행 순서 | `save_stock_list`, `get_stock_list`, `fdr.DataReader`, `_upsert_ohlcv` mock | 종목 목록 갱신 후 코드별 일봉, 선택 시 주봉 upsert |
| TDD-019 | 구현됨 | `collect_jp` driver 정리 | webdriver·종목·fetch mock, fetch 예외 fixture | 성공·실패 모두 `driver.quit()` 1회 호출 |
| TDD-020 | 구현됨 | `collect_jp(weekly=False)` | JP 종목 1개 | 일봉만 upsert; 주봉 호출 없음 |
| TDD-021 | 구현됨 | 대량 입력 성능 관찰 | 10,000행 메모리 DataFrame | 결과 행 수 10,000을 확인하고 경과 시간을 관찰한다. CI 실패 임계값은 두지 않는다. |

`TDD-001`~`TDD-021`은 `tests/test_split_dataset.py`의 20개 테스트 함수로 구현되어 있다. `TDD-019`는 성공과 fetch 예외를 하나의 테스트 함수의 subtest로 분리한다. `TDD-021`은 환경 의존적인 실패를 피하기 위해 측정 결과를 기록하는 관찰 benchmark이며, CI 성능 임계값은 **검증되지 않음**이다.

## 6. 테스트 작성 순서

소스 동작을 바꾸는 변경은 아래 순서를 따른다.

1. 변경 대상 함수와 영향을 받는 TDD ID를 식별한다.
2. 입력, 출력, 오류, DB 호출 여부를 한 문장 계약으로 작성한다.
3. 해당 TDD ID의 실패 테스트를 먼저 작성하고, 실패 로그를 보존한다.
4. 테스트를 통과시키는 최소 소스 변경만 적용한다.
5. 영향 받은 개별 테스트와 `tests/test_split_dataset.py` 전체를 실행한다.
6. SQL, payload 순서, 외부 호출 금지 조건을 다시 확인한다.
7. 새 결함이면 재현 fixture와 회귀 TDD ID를 추가한다.

테스트 기대값을 맞추기 위해 검증 대상 동작을 삭제하거나 mock을 넓게 만들어서는 안 된다.

## 7. 실행 명령과 판정

### 7.1 빠른 단위 테스트

```powershell
$env:STOCK_DB_PASSWORD='test-password'
python -m unittest tests/test_split_dataset.py -v
```

### 7.2 소스 문법 검사

```powershell
python -m py_compile src/dataset/split_dataset.py tests/test_split_dataset.py
```

### 7.3 판정 기준

| 상태 | 기준 | 다음 조치 |
| --- | --- | --- |
| 통과 | 선택한 테스트가 모두 통과하고 실제 외부 호출이 없다. | 변경 결과를 기록한다. |
| 실패 | assertion 또는 예외가 발생한다. | 실패한 TDD ID, fixture, traceback을 보존하고 원인을 분리한다. |
| 중지 | 실제 DB·네트워크·브라우저 호출이 필요하거나 기대 계약이 미확인이다. | 외부 통합 테스트로 분리하거나 계약 결정을 요청한다. |

## 8. 현재 검증 기록

- 실행일: 2026-07-30
- 명령: `STOCK_DB_PASSWORD=test-password python -m unittest tests/test_split_dataset.py -v`
- 결과: TDD-001~TDD-021을 포함해 총 20개 테스트 함수 통과. TDD-019는 성공·실패 subtest를 모두 통과했다.
- 10,000행 관찰 benchmark: `_indicator_rows` 10,000행 처리 결과 10,000행, 측정값 0.118162초(2026-07-31 로컬 실행). 이 수치는 CI 예산이 아니며 다른 환경의 성능을 보장하지 않는다.
- 문법: `ast.parse`로 `tests/test_split_dataset.py` 파싱 성공
- 미실행: 실제 PostgreSQL, 네트워크, Selenium, FinanceDataReader, 전체 테스트 스위트

위 기록은 구현됨으로 표시한 단위·경계 TDD 항목에 한정된다. 실제 DB·네트워크·Selenium·FinanceDataReader 통합은 검증되지 않음이다.

## 9. 실패 분석 규칙

| 증상 | 분리 순서 |
| --- | --- |
| import 실패 | `STOCK_DB_PASSWORD` 설정 시점, `src/` 경로, import side effect를 확인 |
| DB mock 실패 | context manager 체인과 `cursor.executemany` call args를 확인 |
| 날짜 불일치 | 입력 timestamp 단위, JST 변환, 월요일 정규화를 확인 |
| 주봉 OHLCV 불일치 | `W-FRI` 구간, Open/High/Low/Close/Volume 집계를 확인 |
| 지표 값 불일치 | rolling window 길이, 표준편차, shift 방향, 반올림 시점을 확인 |
| 외부 호출 발생 | mock 대상 누락을 확인하고 테스트를 중지한 뒤 경계를 patch |

## 10. 완료 조건과 되돌리기

### 완료 조건

1. 변경된 소스 계약마다 구현된 또는 추가 필요 TDD ID가 연결되어 있다.
2. 구현된 테스트는 외부 서비스 없이 재현 가능하다.
3. 테스트 결과, 실패 조건, 증거가 이 문서에 구분되어 있다.
4. 미확인 계약은 검증됨으로 표기되지 않는다.
5. 실제 DB·네트워크 통합 검증은 단위 테스트 통과와 별도로 보고한다.

### 되돌리기

이 문서는 코드 실행 동작을 변경하지 않는다. 되돌리기는 `issue/split_dataset_tdd_spec.md`를 이전 Git 버전으로 복원하는 방식으로 수행한다.
