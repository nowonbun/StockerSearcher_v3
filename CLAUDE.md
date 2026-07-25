# CLAUDE.md

이 문서는 `D:/work/StockerSearcher_v2` 변경에 대한 Claude 교차 리뷰 정책입니다.

## 검토 위치와 범위

- 검토 대상은 `src/`, `dags/`, `src/database/init/`, Docker Compose 구성 및 관련 운영 문서입니다.
- 현재 실행 범위는 데이터 수집, CPU 예측, 모델 파일 읽기입니다.
- PostgreSQL 컨테이너에는 Airflow 메타데이터용 `airflow` DB와 주식 데이터용 `stock` DB가 분리되어야 하며, `stock` 수집기는 전용 `STOCK_DB_USER` 자격증명만 사용해야 합니다.
- `migration/`은 현재 Airflow DAG의 실행 범위에 포함하지 않습니다.

## 핵심 검토 목표

1. JP/KR 수집 코드가 PostgreSQL의 연결·트랜잭션·`ON CONFLICT` 문법과 일치하는지 확인합니다.
2. `stock` 스키마와 수집 코드의 테이블·컬럼·PK·FK·upsert 대상이 일치하는지 확인합니다.
3. Airflow 메타데이터 DB와 `stock` DB의 연결 경계가 유지되는지 확인합니다.
4. 데이터 수집·예측의 중복 실행 방지, 실패 시 롤백, 비밀정보 환경 변수 처리를 확인합니다.

## 검토 결과 형식

- 새 위험 또는 모순만 보고하고, 문제 없으면 `OK|no-ng|합의된 항목 생략`을 반환합니다.
- 발견 항목은 `NG|<심각도>|<경로:줄>|<검토 축>|<수정>` 형식을 사용합니다.
- 심각도는 `Critical`, `High`, `Medium`, `Low`만 사용합니다.
- 검증할 수 없는 내용은 `미검증됨`으로 표시합니다.

## 금지 사항

- 실제 비밀번호·토큰·DB 연결 문자열을 응답이나 패치에 포함해서는 안 됩니다.
- 확인하지 않은 스키마·실행 결과·모델 성능을 사실로 단정해서는 안 됩니다.
- 요청 범위 밖의 웹앱·migration 변경을 제안하거나 적용해서는 안 됩니다.

## 문서 검토

- Markdown, YAML, SQL 및 Python 텍스트 파일은 UTF-8 무결성과 한국어 텍스트 보존을 확인합니다.
- 문서의 실행 명령은 현재 V2 Docker Compose 경로와 일치해야 합니다.
