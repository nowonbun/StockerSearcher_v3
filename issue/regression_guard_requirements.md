# 지속 회귀 게이트 요구사항

## 목적

이 게이트는 이후 기능 개발과 AI 세션 변경에서 기존 계약의 회귀, 범위 밖 수정, 테스트 약화를 조기에 검출한다.

## 제어 모드

| 단계 | 실행 담당자 | 실행 조건 | 제어 |
| --- | --- | --- | --- |
| 로컬 TDD | 개발자 또는 AI | 소스·테스트·구성 변경 전후 | 필수 |
| CI 회귀 게이트 | GitHub Actions | push 또는 pull request | 자동 |
| 실제 통합 검증 | 운영자 | DB·네트워크·브라우저 동작 변경 | 운영자 승인 필요 |

## TDD 계약

1. 새 동작과 결함 수정은 구현 또는 수정 전에 대응 테스트를 추가한다.
2. 테스트는 정상 경로, 경계값, 오류 경로 또는 이전 동작 보존 중 적용 가능한 계약을 검증한다.
3. 테스트 삭제 또는 완화는 회귀 게이트 통과 수단으로 사용할 수 없다.
4. 변경 후 Python 게이트와 Vitest 게이트를 실행한다.
5. 테스트가 실행되지 않았거나 외부 통합이 필요한 항목은 검증되지 않음으로 기록한다.

## 필수 명령

```powershell
$env:PYTHONPATH = "$PWD\src"
$env:STOCK_DB_PASSWORD = 'test-password'
python -W error::ResourceWarning -m unittest tests/test_batch_runner.py tests/test_batch_process_wrapper.py tests/test_prediction_filters.py tests/test_viewer_static_contracts.py tests/test_split_dataset.py tests/test_regression_guard_contract.py -v

Set-Location src/viewer
npm test
```

## 중지 조건

- 변경 계약에 대응하는 테스트가 없다.
- 기존 테스트가 실패한다.
- 테스트가 실제 DB, 네트워크, 브라우저 또는 비밀정보를 요구한다.
- `package-lock.json`과 `package.json`의 의존성 상태가 일치하지 않는다.
- 테스트 결과를 통과로 바꾸기 위해 검증 대상 계약을 삭제하거나 완화한다.

## 복구 절차

1. 실패한 테스트 이름, 입력 fixture, 명령 출력과 traceback을 보존한다.
2. 마지막 변경을 분리하고 계약·테스트 기대값·구현 중 원인을 식별한다.
3. 원인이 확인되면 재현 테스트를 유지한 상태로 최소 수정한다.
4. 영향을 받은 테스트와 전체 필수 명령을 다시 실행한다.
5. CI가 실패하면 로컬에서 같은 명령을 재현한 후 수정본을 다시 제출한다.

## 한계

- Python과 Vitest 단위 테스트는 실제 PostgreSQL, 외부 시세 제공자, Nuxt HTTP 서버, 브라우저, Prefect 서비스의 통합 동작을 검증하지 않는다.
- 위 통합 범위는 검증되지 않음이며, 운영자 승인 아래 별도 환경에서 검증한다.
- CI는 테스트·정적 검증만 수행하며 배포, DB 변경, 비밀정보 접근을 수행하지 않는다.
