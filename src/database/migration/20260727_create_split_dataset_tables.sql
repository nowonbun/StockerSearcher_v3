-- 분리된 시세 수집/지표 계산 배치용 테이블 생성 스크립트
-- 기존 stock_data_* 테이블은 변경하거나 삭제하지 않습니다.

-- ================================================================
-- 1. 기초 시세(OHLCV) 테이블
--    배치: base
--    데이터: 종목, 날짜, 시가, 고가, 저가, 종가, 거래량
-- ================================================================

-- 한국 일봉
CREATE TABLE IF NOT EXISTS stock_ohlcv_kr (
    code        VARCHAR(12) NOT NULL REFERENCES stock_list_kr (code),
    date        DATE NOT NULL,
    open        BIGINT,
    high        BIGINT,
    low         BIGINT,
    close       BIGINT,
    volume      BIGINT,
    create_date TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    update_date TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (code, date)
);

-- 한국 주봉
CREATE TABLE IF NOT EXISTS stock_ohlcv_week_kr (
    code        VARCHAR(12) NOT NULL REFERENCES stock_list_kr (code),
    date        DATE NOT NULL,
    open        BIGINT,
    high        BIGINT,
    low         BIGINT,
    close       BIGINT,
    volume      BIGINT,
    create_date TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    update_date TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (code, date)
);

-- 일본 일봉
CREATE TABLE IF NOT EXISTS stock_ohlcv_jp (
    code        VARCHAR(12) NOT NULL REFERENCES stock_list_jp (code),
    date        DATE NOT NULL,
    open        BIGINT,
    high        BIGINT,
    low         BIGINT,
    close       BIGINT,
    volume      BIGINT,
    create_date TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    update_date TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (code, date)
);

-- 일본 주봉
CREATE TABLE IF NOT EXISTS stock_ohlcv_week_jp (
    code        VARCHAR(12) NOT NULL REFERENCES stock_list_jp (code),
    date        DATE NOT NULL,
    open        BIGINT,
    high        BIGINT,
    low         BIGINT,
    close       BIGINT,
    volume      BIGINT,
    create_date TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    update_date TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (code, date)
);

-- ================================================================
-- 2. 기술 지표 테이블
--    배치: indicators
--    원본: stock_ohlcv_* 테이블
-- ================================================================

-- 공통 컬럼:
--   5/20/50/60/120/240일(주) 이동평균
--   60기간 볼린저 밴드: 상단 1σ, 하단 1σ, 하단 3σ
--   일목균형표: 전환선, 기준선, 선행스팬 A/B, 후행스팬

-- 한국 일봉 지표
CREATE TABLE IF NOT EXISTS stock_indicator_kr (
    code                     VARCHAR(12) NOT NULL REFERENCES stock_list_kr (code),
    date                     DATE NOT NULL,
    "5mvavg"                 BIGINT,
    "20mvavg"                BIGINT,
    "50mvavg"                BIGINT,
    "60mvavg"                BIGINT,
    "120mvavg"               BIGINT,
    "240mvavg"               BIGINT,
    bollinger_upper_60_1     BIGINT,
    bollinger_lower_60_1     BIGINT,
    bollinger_lower_60_3     BIGINT,
    ichimoku_conversion      BIGINT,
    ichimoku_base            BIGINT,
    ichimoku_span_a          BIGINT,
    ichimoku_span_b          BIGINT,
    ichimoku_lagging         BIGINT,
    create_date              TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    update_date              TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (code, date)
);

-- 한국 주봉 지표
CREATE TABLE IF NOT EXISTS stock_indicator_week_kr (
    code                     VARCHAR(12) NOT NULL REFERENCES stock_list_kr (code),
    date                     DATE NOT NULL,
    "5mvavg"                 BIGINT,
    "20mvavg"                BIGINT,
    "50mvavg"                BIGINT,
    "60mvavg"                BIGINT,
    "120mvavg"               BIGINT,
    "240mvavg"               BIGINT,
    bollinger_upper_60_1     BIGINT,
    bollinger_lower_60_1     BIGINT,
    bollinger_lower_60_3     BIGINT,
    ichimoku_conversion      BIGINT,
    ichimoku_base            BIGINT,
    ichimoku_span_a          BIGINT,
    ichimoku_span_b          BIGINT,
    ichimoku_lagging         BIGINT,
    create_date              TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    update_date              TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (code, date)
);

-- 일본 일봉 지표
CREATE TABLE IF NOT EXISTS stock_indicator_jp (
    code                     VARCHAR(12) NOT NULL REFERENCES stock_list_jp (code),
    date                     DATE NOT NULL,
    "5mvavg"                 BIGINT,
    "20mvavg"                BIGINT,
    "50mvavg"                BIGINT,
    "60mvavg"                BIGINT,
    "120mvavg"               BIGINT,
    "240mvavg"               BIGINT,
    bollinger_upper_60_1     BIGINT,
    bollinger_lower_60_1     BIGINT,
    bollinger_lower_60_3     BIGINT,
    ichimoku_conversion      BIGINT,
    ichimoku_base            BIGINT,
    ichimoku_span_a          BIGINT,
    ichimoku_span_b          BIGINT,
    ichimoku_lagging         BIGINT,
    create_date              TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    update_date              TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (code, date)
);

-- 일본 주봉 지표
CREATE TABLE IF NOT EXISTS stock_indicator_week_jp (
    code                     VARCHAR(12) NOT NULL REFERENCES stock_list_jp (code),
    date                     DATE NOT NULL,
    "5mvavg"                 BIGINT,
    "20mvavg"                BIGINT,
    "50mvavg"                BIGINT,
    "60mvavg"                BIGINT,
    "120mvavg"               BIGINT,
    "240mvavg"               BIGINT,
    bollinger_upper_60_1     BIGINT,
    bollinger_lower_60_1     BIGINT,
    bollinger_lower_60_3     BIGINT,
    ichimoku_conversion      BIGINT,
    ichimoku_base            BIGINT,
    ichimoku_span_a          BIGINT,
    ichimoku_span_b          BIGINT,
    ichimoku_lagging         BIGINT,
    create_date              TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    update_date              TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (code, date)
);

-- ================================================================
-- 3. 기존 stock_data_* 데이터 이관
--    일목균형표 컬럼은 기존 테이블에 없으므로 NULL로 둡니다.
--    indicators 배치를 실행하면 해당 값까지 다시 계산하여 채웁니다.
-- ================================================================

-- 기존 시세 → 기초 시세(OHLCV)
INSERT INTO stock_ohlcv_kr (code, date, open, high, low, close, volume, create_date, update_date)
SELECT code, date, open, high, low, close, volume, create_date, update_date
FROM stock_data_kr
ON CONFLICT (code, date) DO UPDATE SET
    open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
    close = EXCLUDED.close, volume = EXCLUDED.volume, update_date = now();

INSERT INTO stock_ohlcv_week_kr (code, date, open, high, low, close, volume, create_date, update_date)
SELECT code, date, open, high, low, close, volume, create_date, update_date
FROM stock_data_week_kr
ON CONFLICT (code, date) DO UPDATE SET
    open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
    close = EXCLUDED.close, volume = EXCLUDED.volume, update_date = now();

INSERT INTO stock_ohlcv_jp (code, date, open, high, low, close, volume, create_date, update_date)
SELECT code, date, open, high, low, close, volume, create_date, update_date
FROM stock_data_jp
ON CONFLICT (code, date) DO UPDATE SET
    open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
    close = EXCLUDED.close, volume = EXCLUDED.volume, update_date = now();

INSERT INTO stock_ohlcv_week_jp (code, date, open, high, low, close, volume, create_date, update_date)
SELECT code, date, open, high, low, close, volume, create_date, update_date
FROM stock_data_week_jp
ON CONFLICT (code, date) DO UPDATE SET
    open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
    close = EXCLUDED.close, volume = EXCLUDED.volume, update_date = now();

-- 기존 지표 → 기술 지표
-- 기존 일봉에는 120/240 이동평균 및 하단 3σ 값이 있습니다.
INSERT INTO stock_indicator_kr (
    code, date, "5mvavg", "20mvavg", "50mvavg", "60mvavg", "120mvavg", "240mvavg",
    bollinger_upper_60_1, bollinger_lower_60_1, bollinger_lower_60_3,
    create_date, update_date
)
SELECT
    code, date, "5mvavg", "20mvavg", "50mvavg", "60mvavg", "120mvavg", "240mvavg",
    upperband60_1, lowerband60_1, lowerband60_3,
    create_date, update_date
FROM stock_data_kr
ON CONFLICT (code, date) DO UPDATE SET
    "5mvavg" = EXCLUDED."5mvavg", "20mvavg" = EXCLUDED."20mvavg",
    "50mvavg" = EXCLUDED."50mvavg", "60mvavg" = EXCLUDED."60mvavg",
    "120mvavg" = EXCLUDED."120mvavg", "240mvavg" = EXCLUDED."240mvavg",
    bollinger_upper_60_1 = EXCLUDED.bollinger_upper_60_1,
    bollinger_lower_60_1 = EXCLUDED.bollinger_lower_60_1,
    bollinger_lower_60_3 = EXCLUDED.bollinger_lower_60_3,
    update_date = now();

INSERT INTO stock_indicator_jp (
    code, date, "5mvavg", "20mvavg", "50mvavg", "60mvavg", "120mvavg", "240mvavg",
    bollinger_upper_60_1, bollinger_lower_60_1, bollinger_lower_60_3,
    create_date, update_date
)
SELECT
    code, date, "5mvavg", "20mvavg", "50mvavg", "60mvavg", "120mvavg", "240mvavg",
    upperband60_1, lowerband60_1, lowerband60_3,
    create_date, update_date
FROM stock_data_jp
ON CONFLICT (code, date) DO UPDATE SET
    "5mvavg" = EXCLUDED."5mvavg", "20mvavg" = EXCLUDED."20mvavg",
    "50mvavg" = EXCLUDED."50mvavg", "60mvavg" = EXCLUDED."60mvavg",
    "120mvavg" = EXCLUDED."120mvavg", "240mvavg" = EXCLUDED."240mvavg",
    bollinger_upper_60_1 = EXCLUDED.bollinger_upper_60_1,
    bollinger_lower_60_1 = EXCLUDED.bollinger_lower_60_1,
    bollinger_lower_60_3 = EXCLUDED.bollinger_lower_60_3,
    update_date = now();

-- 기존 주봉에는 120/240 이동평균과 하단 3σ 값이 없으므로 NULL로 이관합니다.
INSERT INTO stock_indicator_week_kr (
    code, date, "5mvavg", "20mvavg", "50mvavg", "60mvavg",
    bollinger_upper_60_1, bollinger_lower_60_1, create_date, update_date
)
SELECT
    code, date, "5mvavg", "20mvavg", "50mvavg", "60mvavg",
    upperband60_1, lowerband60_1, create_date, update_date
FROM stock_data_week_kr
ON CONFLICT (code, date) DO UPDATE SET
    "5mvavg" = EXCLUDED."5mvavg", "20mvavg" = EXCLUDED."20mvavg",
    "50mvavg" = EXCLUDED."50mvavg", "60mvavg" = EXCLUDED."60mvavg",
    bollinger_upper_60_1 = EXCLUDED.bollinger_upper_60_1,
    bollinger_lower_60_1 = EXCLUDED.bollinger_lower_60_1,
    update_date = now();

INSERT INTO stock_indicator_week_jp (
    code, date, "5mvavg", "20mvavg", "50mvavg", "60mvavg",
    bollinger_upper_60_1, bollinger_lower_60_1, create_date, update_date
)
SELECT
    code, date, "5mvavg", "20mvavg", "50mvavg", "60mvavg",
    upperband60_1, lowerband60_1, create_date, update_date
FROM stock_data_week_jp
ON CONFLICT (code, date) DO UPDATE SET
    "5mvavg" = EXCLUDED."5mvavg", "20mvavg" = EXCLUDED."20mvavg",
    "50mvavg" = EXCLUDED."50mvavg", "60mvavg" = EXCLUDED."60mvavg",
    bollinger_upper_60_1 = EXCLUDED.bollinger_upper_60_1,
    bollinger_lower_60_1 = EXCLUDED.bollinger_lower_60_1,
    update_date = now();

-- ================================================================
-- 4. 날짜 조회용 인덱스
-- ================================================================
CREATE INDEX IF NOT EXISTS idx_stock_ohlcv_kr_date
    ON stock_ohlcv_kr (date);
CREATE INDEX IF NOT EXISTS idx_stock_ohlcv_week_kr_date
    ON stock_ohlcv_week_kr (date);
CREATE INDEX IF NOT EXISTS idx_stock_ohlcv_jp_date
    ON stock_ohlcv_jp (date);
CREATE INDEX IF NOT EXISTS idx_stock_ohlcv_week_jp_date
    ON stock_ohlcv_week_jp (date);

CREATE INDEX IF NOT EXISTS idx_stock_indicator_kr_date
    ON stock_indicator_kr (date);
CREATE INDEX IF NOT EXISTS idx_stock_indicator_week_kr_date
    ON stock_indicator_week_kr (date);
CREATE INDEX IF NOT EXISTS idx_stock_indicator_jp_date
    ON stock_indicator_jp (date);
CREATE INDEX IF NOT EXISTS idx_stock_indicator_week_jp_date
    ON stock_indicator_week_jp (date);
