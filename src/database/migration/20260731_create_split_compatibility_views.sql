-- 분리 테이블(OHLCV + indicator)을 조회하기 위한 읽기 전용 뷰입니다.
-- 기존 테이블과 데이터를 변경하지 않습니다. 실행 전 indicators 배치가 완료되어야 합니다.
-- 기존 뷰의 NULL DI 컬럼을 제거하므로, 뷰를 삭제한 뒤 새 스키마로 다시 만듭니다.

DROP VIEW IF EXISTS stock_data_split_kr;
DROP VIEW IF EXISTS stock_data_split_jp;
DROP VIEW IF EXISTS stock_data_split_week_kr;
DROP VIEW IF EXISTS stock_data_split_week_jp;

CREATE VIEW stock_data_split_kr AS
SELECT o.code, o.date, o.open, o.high, o.low, o.close, o.volume,
       (o.close::numeric * o.volume::numeric) AS transamnt,
       i."5mvavg", i."20mvavg", i."50mvavg", i."60mvavg", i."120mvavg", i."240mvavg",
       i.bollinger_upper_60_1 AS upperband60_1, i.bollinger_lower_60_1 AS lowerband60_1,
       i.bollinger_lower_60_3 AS lowerband60_3,
       i.ichimoku_conversion, i.ichimoku_base, i.ichimoku_span_a, i.ichimoku_span_b,
       i.ichimoku_lagging,
       o.create_date, o.update_date
FROM stock_ohlcv_kr o JOIN stock_indicator_kr i USING (code, date);

CREATE VIEW stock_data_split_jp AS
SELECT o.code, o.date, o.open, o.high, o.low, o.close, o.volume,
       (o.close::numeric * o.volume::numeric) AS transamnt,
       i."5mvavg", i."20mvavg", i."50mvavg", i."60mvavg", i."120mvavg", i."240mvavg",
       i.bollinger_upper_60_1 AS upperband60_1, i.bollinger_lower_60_1 AS lowerband60_1,
       i.bollinger_lower_60_3 AS lowerband60_3,
       i.ichimoku_conversion, i.ichimoku_base, i.ichimoku_span_a, i.ichimoku_span_b,
       i.ichimoku_lagging,
       o.create_date, o.update_date
FROM stock_ohlcv_jp o JOIN stock_indicator_jp i USING (code, date);

CREATE VIEW stock_data_split_week_kr AS
SELECT o.code, o.date, o.open, o.high, o.low, o.close, o.volume,
       (o.close::numeric * o.volume::numeric) AS transamnt,
       i."5mvavg", i."20mvavg", i."50mvavg", i."60mvavg", i."120mvavg", i."240mvavg",
       i.bollinger_upper_60_1 AS upperband60_1, i.bollinger_lower_60_1 AS lowerband60_1,
       i.bollinger_lower_60_3 AS lowerband60_3,
       i.ichimoku_conversion, i.ichimoku_base, i.ichimoku_span_a, i.ichimoku_span_b,
       i.ichimoku_lagging,
       o.create_date, o.update_date
FROM stock_ohlcv_week_kr o JOIN stock_indicator_week_kr i USING (code, date);

CREATE VIEW stock_data_split_week_jp AS
SELECT o.code, o.date, o.open, o.high, o.low, o.close, o.volume,
       (o.close::numeric * o.volume::numeric) AS transamnt,
       i."5mvavg", i."20mvavg", i."50mvavg", i."60mvavg", i."120mvavg", i."240mvavg",
       i.bollinger_upper_60_1 AS upperband60_1, i.bollinger_lower_60_1 AS lowerband60_1,
       i.bollinger_lower_60_3 AS lowerband60_3,
       i.ichimoku_conversion, i.ichimoku_base, i.ichimoku_span_a, i.ichimoku_span_b,
       i.ichimoku_lagging,
       o.create_date, o.update_date
FROM stock_ohlcv_week_jp o JOIN stock_indicator_week_jp i USING (code, date);
