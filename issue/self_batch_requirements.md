# 자체 배치 전환 계약

## 범위

Prefect 서비스·Python 의존성·전용 flow를 제거하고 기존 러너에 영속 큐·예약·로컬 관리 API와 Python 서버 렌더링 관리 화면을 연결합니다. 기존 Nuxt 관리 화면/API는 레거시 호환용으로 유지합니다. 주식 DB·모델은 유지하며 공개 작업 모드는 아래의 최신 세 패턴 통합 계약을 적용합니다.

## 테스트 계약

- `tests/test_batch_web.py`: Python 렌더링·메뉴·빈 상태·HTML 이스케이프·로그인 없는 직접 접속·서버 재시작 후 CSRF 유지·CSRF·출처 검증·폼 명령 제한·예약·취소·API 장애·정적 CSS·Compose 웹 노출 및 내부 API 비공개를 mock으로 검사합니다. 새 모듈 부재·새 Compose 서비스 부재의 실패를 먼저 확인했습니다.
- 전체 이력 집계는 `tests/test_batch_service.py`에서 서울 자정·이중 기록 중복 제거·7일 범위를 검증합니다. 목록 100건 제한과 집계는 분리합니다.

- `tests/test_batch_service.py`: cron 경계·서울 시간·입력 거부, 큐 영속성·중복 방지, 예약 분 단위 중복 방지, 단일 worker, 시작 실패, 취소, 재시작 상태 복구, 로그 경로·크기, API 무인증 접속·출처 검증·입력·충돌 응답, Compose 전환을 격리 테스트합니다.
- `src/viewer/tests/batch-api.test.ts`: H3와 fetch를 mock하여 고정 내부 경로·무인증 호출·로컬 Host·출처 검증·허용 메서드·임의 필드 거부·오류 전달을 검사합니다.
- 기존 Python·Vitest 회귀 테스트는 삭제하거나 완화하지 않습니다. 새 모듈이 없는 상태의 실패를 먼저 확인한 뒤 구현합니다.

## 실제 통합 검증: 검증되지 않음

운영자 승인 후 Linux 별도 환경에서 다음을 검증해야 합니다.

1. 기존 Prefect 실행이 끝났는지 확인하고 기존 서비스를 중지합니다. 동일 DB를 쓰는 이중 스케줄러를 허용하지 않습니다.
2. 새 이미지를 빌드하고 DB·모델·상태·로그 볼륨을 유지하여 기동합니다.
3. 기존 주식 조회·MCP와 배치 관리 화면의 로그인 없는 접속·외부 출처 차단, 새로고침·로그·오류 표시를 확인합니다.
4. JP/KR 작업을 등록해 순차 실행·중복 거부·실패 시 후속 작업 중단·취소 시 하위 프로세스 종료를 확인합니다.
5. 예약 저장·활성/비활성·컨테이너 재시작 후 설정/대기열 유지 및 running → interrupted를 확인합니다.
6. 실제 PostgreSQL 수집·분리 데이터 최신성·모델 파일 호환성은 별도 검증합니다. collect/full은 분리 데이터 갱신을 보장하지 않습니다.

## 남은 한계

- CLI 직접 실행은 전체 큐를 우회하며 시장별 잠금만 적용합니다. 관리 중 CLI를 별도로 실행하지 않습니다.
- 단일 Linux 서비스·공유 상태 볼륨 전용입니다. 다중 호스트 분산 큐가 아닙니다.
- 중단 중 누락된 예약을 재생하지 않습니다. 예약 중복/큐 초과는 서비스 로그에 남고 추가 실행을 생성하지 않습니다.
- 이력·로그·예약 키는 자동 정리하지 않습니다. 디스크 보관 정책은 운영자가 관리합니다.
- 취소는 진행 중 작업을 중단할 뿐 이미 커밋한 적재를 롤백하지 않습니다.
- Python·레거시 웹·배치 API에는 인증이 없습니다. 루프백 바인딩을 유지하고 외부에 공개하지 않습니다. Python 폼의 HttpOnly·SameSite 쿠키와 숨김 값은 인증이 아닌 CSRF 보호입니다.

## 복구

자체 batch를 중지한 뒤 이전 소스·이미지·Compose로 복구합니다. DB·상태·로그·모델 볼륨은 삭제하지 않습니다. 기존 Prefect와 신규 서비스가 동시에 실행되지 않도록 확인합니다. 실패 테스트·입력 fixture와 실행 로그를 보존하고 소스 또는 테스트 설정이 바뀐 경우 재실행합니다.

## 로컬 검증 기록 (2026-10-29)

- 신규 Python 모듈·웹 프록시가 없는 상태에서 각각 import 실패를 확인한 뒤 구현했습니다. Compose 교체 계약도 기존 Prefect 서비스 때문에 실패하는 것을 먼저 확인했습니다.
- `PYTHONPATH=src`, `STOCK_DB_PASSWORD=test-password`로 `python -W error::ResourceWarning -m unittest discover -s tests -v`: 94개 통과. 테스트 예약의 실행 시각 의존성을 제거한 뒤 변경 모듈을 별도로 재실행했습니다.
- CI 필수 Python 게이트에 신규 모듈을 포함하여 80개 통과했습니다. 실패 테스트 이름: 없음.
- `src/viewer`에서 `npm test`: Vitest 11개 통과.
- `src/viewer`에서 `npm run build`: 성공. 기존 대형 JS 청크 경고와 의존 패키지 exports 사용 중단 경고가 남았습니다.
- Python AST 문법, 로컬·서버 Compose `config --quiet`, package/lockfile 의존성 일치, UTF-8 디코딩·재인코딩·한국어 보존 및 `git diff --check`를 확인했습니다.
- Windows 임시 폴더 ACL과 빌드 하위 프로세스의 `spawn EPERM`은 권한 검토 후 같은 검증을 재실행하여 통과했습니다. Docker 기동·이미지 빌드·운영 DB·실제 브라우저·외부 수집 통합은 실행하지 않았으며 검증되지 않음입니다.

## Python 디자인 웹 검증 기록 (2026-10-29)

- 참조: `design/sample.png`. 짙은 사이드바·흰색 헤더·4개 요약 카드·7일 차트·빠른 실행·탭 이력 표를 Python HTML/CSS로 구현했습니다. 샘플의 임의 업무·수치는 실제 JP/KR 허용 작업·이력으로 교체합니다.
- Node/TypeScript를 추가하지 않고 별도 `batch-web` Python 서비스를 노출합니다. 기존 배치 API의 비공개 테스트는 유지합니다.
- 현재 정적 미리보기 `design/batch_preview.html`에는 예시 데이터임을 명시하고 실행 버튼을 비활성화했습니다.
- 브라우저의 file URL 접근 정책으로 정적 미리보기 렌더링 확인이 차단되었습니다. 우회하지 않았으며 픽셀 단위 외형·반응형 브라우저 렌더링은 검증되지 않음입니다.
- 실제 웹 ↔ 배치 API 통신·CSRF 쿠키·운영 예약·DB·수집·취소 통합은 검증되지 않음입니다. 운영 전 별도 환경에서 확인합니다.
- 최종 검증: `PYTHONPATH=src`, `STOCK_DB_PASSWORD=test-password`, `python -W error::ResourceWarning -m unittest discover -s tests -q`로 Python 108개 통과. 실패 테스트 이름: 없음. `src/viewer`의 `npm test`로 기존 Vitest 11개 통과. 로컬·서버 Compose `config --quiet`, UTF-8·한국어 보존, Python 문법, lockfile 일치, `git diff --check` 통과.
- 시각 확인 제한 보고: 원인 — 브라우저 file URL 정책 거부. 증거 — 로컬 정적 미리보기 탐색 요청의 프로토콜 거부 응답. 다음 조치 — 사용자가 `design/batch_preview.html`을 수동으로 열어 확인하거나, 별도 승인된 통합 환경에서 Python 웹의 실제 렌더링을 확인합니다. 기존 차단을 우회하는 재시도는 하지 않았습니다.

## 로컬 무인증 전환 계약 (2026-10-30)

- 사용자가 로컬 단독 사용을 위해 인증 제거를 명시적으로 요청했습니다. 기존 인증 실패 테스트는 그 계약에 해당하는 부분만 직접 접속·무인증 호출 검증으로 전환했습니다. 입력 검증·중복 방지·CSRF·HTML 이스케이프·로그 격리·메서드 제한은 유지합니다. 과거 검증 기록은 당시 버전 기준입니다.
- `tests/test_batch_local_access.py`에서 토큰 없는 API·웹·클라이언트 생성, 대시보드 직접 접속, 외부 Host 거부, 예제·Compose 인증 설정 제거 및 루프백 노출 계약을 먼저 추가하고 실패를 확인했습니다.
- Python 로그인 화면·로그인/로그아웃 처리·인증 세션·API Bearer 검사·환경 변수 요구 및 레거시 Nuxt 인증 입력·전달을 제거합니다. 기존 로그인 경로는 404입니다.
- Python의 CSRF는 로그인과 무관한 자동 이중 제출 쿠키로 유지합니다. 레거시 프록시는 동일 출처 JSON 쓰기만 허용하고 내부 API는 브라우저 Origin을 동반한 쓰기를 거부합니다.
- 실제 컨테이너 재빌드·브라우저·DB·예약·취소 통합은 검증되지 않음입니다. 무인증이므로 인터넷·LAN 공개를 지원하지 않습니다.
- 검증 결과: `PYTHONPATH=src`, `STOCK_DB_PASSWORD=test-password`에서 `python -W error::ResourceWarning -m unittest discover -s tests -q` 112개 통과. 실패 테스트 이름: 없음. `src/viewer`의 `npm test`에서 Vitest 13개 통과, `npm run build` 성공. 기존 대형 청크·의존 패키지 exports 경고는 남았습니다.
- 관리 토큰 환경 변수 없이 로컬·서버 Compose `config --quiet`가 통과했습니다. Python 문법, package/lockfile 일치, UTF-8 재인코딩·한국어 보존, `git diff --check`도 통과했습니다. DB 인증 정보와 실제 `.env`의 비밀 값은 수정하지 않았으며, 이전 배치 인증 변수는 더 이상 사용하지 않습니다.

## 수동 실행 선택창 단순화 (2026-10-30, 이전 단계 기록)

- Python의 작업 관리 16개 실행 카드와 대시보드 4개 빠른 실행 카드를 각각 시장·작업 선택창과 실행 등록 버튼 한 개로 변경합니다. 기본 선택은 빈 값이며 두 선택 모두 필수입니다. 로그인·JavaScript를 추가하지 않습니다.
- 옵션은 데이터 갱신·예측·기존 방식 세 그룹입니다. 기존 8개 모드·시장 조합, 작업 실행 순서, CLI·예약·중복 방지·CSRF는 유지합니다. 서버의 모드 값을 이름 변경 없이 제출합니다.
- 지표 계산은 저장된 시세를 이용한 계산이며 새로운 시세 수집이 아님을 표시합니다. split은 원본 수집 → 지표 계산이고 예측이 포함되지 않음을 표시합니다. 기존 통합 테이블과 분리 데이터 경로를 구분합니다.
- `tests/test_batch_web.py`에 두 페이지의 실행 폼 개수·필수 선택·시장/모드 전체 옵션·세 그룹·CSRF 유지·작업 설명 테스트를 먼저 추가했습니다. 기존 화면에서 실행 폼 개수 16개/4개와 설명 부재로 실패함을 확인한 뒤 구현했습니다.
- 정적 미리보기의 빠른 실행 부분도 같은 선택창으로 갱신하고, 예시 데이터 표기와 실행 비활성화·읽기 쉬운 들여쓰기는 유지합니다. 실제 브라우저·서버·수집·DB 통합은 검증되지 않음입니다.
- 검증: `PYTHONPATH=src`, `STOCK_DB_PASSWORD=test-password`, `python -W error::ResourceWarning -m unittest discover -s tests -q` 114개 통과. `src/viewer`의 `npm test` Vitest 13개 통과. 실패 테스트 이름: 없음. Python 문법·두 Compose `config --quiet`·UTF-8/한국어 보존·`git diff --check` 통과. UI DOM 구조·폼 계약만 검증했으며 실제 브라우저 외형과 운영 반영은 검증되지 않음입니다.

## 세 패턴 통합 계약 (2026-10-30, 현재 적용)

- 공개 입력은 collect(통합 데이터 수집), predict(예측), full(수집·예측) 세 가지입니다. CLI·API·수동/예약 선택창과 Python/Nuxt 표시를 동일하게 제한합니다. 이전 단계의 8개 모드 보존 계약은 사용자의 후속 요청에 따라 대체합니다.
- collect는 원본 시세 수집 → 지표 자동 계산, predict는 일봉 예측 → 주봉 예측, full은 원본 수집 → 지표 자동 계산 → 일봉 예측 → 주봉 예측입니다. 내부 단계를 별도 공개 모드로 노출하지 않습니다. 주식 테이블·스키마는 변경하지 않습니다.
- `tests/test_batch_patterns.py`에서 공개 모드 수·작업 순서·CLI/관리 API 개별 모드 거부를 검증합니다. 실제 러너에 mock 프로세스를 연결해 단계 순서·수집/지표 실패 시 예측 미실행을 검증합니다. 기존 MODES 8개와 collect가 구형 경로만 실행하는 실패를 먼저 확인했습니다.
- 이전 모드의 저장된 예약/대기 작업은 단일 서비스 잠금 아래 recover 단계에서 base/indicators/split → collect, daily/weekly → predict로 전환합니다. 동일 시장·작업으로 겹친 대기열은 가장 오래된 한 건을 유지하고 나머지는 사유와 함께 cancelled 처리합니다. 완료 이력은 수정하지 않습니다. 전환 멱등성도 테스트합니다.
- 이전 테스트 중 의도적으로 바뀐 모드·순서·옵션 계약만 새 요구사항에 맞춰 대체했습니다. 역사 이력 fixture, 중복 방지·순차 처리·실패 전파·취소·로그 격리·입력 검증·CSRF는 유지합니다.
- 실제 Docker 기동·운영 SQLite 전환·브라우저·수집·모델·PostgreSQL 통합은 검증되지 않음입니다. 전환 전에 실행 중 작업을 확인하고 기존 상태 볼륨을 보존해야 합니다.
- 검증 결과: `PYTHONPATH=src`, `STOCK_DB_PASSWORD=test-password`, `python -W error::ResourceWarning -m unittest discover -s tests -q` 122개 통과. `src/viewer`의 `npm test` Vitest 13개 통과 및 `npm run build` 성공. 두 Compose 구성 검사·Python 문법·UTF-8/한국어 보존·lockfile 일치·`git diff --check` 통과. 실제 수집·DB·예약 전환 통합은 검증되지 않음입니다.
- 첫 전체 검사에서 `test_cli_parser_requires_supported_market_mode_and_history_limit`의 정상 입력 fixture가 옛 `split`을 사용해 실패했습니다. 해당 정상 입력은 collect로 전환했고, 폐기 모드 거부 검사는 신규 패턴 테스트에서 유지한 뒤 전체 검사를 재실행했습니다. 실패를 숨기거나 입력 검증을 삭제하지 않았습니다.
