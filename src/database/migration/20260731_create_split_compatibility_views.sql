-- 분리 테이블(OHLCV + indicator)을 기존 stock_data_*와 비교하기 위한 읽기 전용 호환 뷰입니다.
-- 기존 테이블과 데이터를 변경하지 않습니다. 실행 전 indicators 배치가 완료되어야 합니다.

CREATE OR REPLACE VIEW stock_data_split_kr AS
SELECT o.code, o.date, o.open, o.high, o.low, o.close, o.volume,
       (o.close::numeric * o.volume::numeric) AS transamnt,
       i."5mvavg", i."20mvavg", i."50mvavg", i."60mvavg", i."120mvavg", i."240mvavg",
       i.bollinger_upper_60_1 AS upperband60_1, i.bollinger_lower_60_1 AS lowerband60_1,
       i.bollinger_lower_60_3 AS lowerband60_3,
       NULL::BIGINT AS di_plus, NULL::BIGINT AS di_minus, NULL::BIGINT AS adx,
       o.create_date, o.update_date
FROM stock_ohlcv_kr o JOIN stock_indicator_kr i USING (code, date);

CREATE OR REPLACE VIEW stock_data_split_jp AS
SELECT o.code, o.date, o.open, o.high, o.low, o.close, o.volume,
       (o.close::numeric * o.volume::numeric) AS transamnt,
       i."5mvavg", i."20mvavg", i."50mvavg", i."60mvavg", i."120mvavg", i."240mvavg",
       i.bollinger_upper_60_1 AS upperband60_1, i.bollinger_lower_60_1 AS lowerband60_1,
       i.bollinger_lower_60_3 AS lowerband60_3,
       NULL::BIGINT AS di_plus, NULL::BIGINT AS di_minus, NULL::BIGINT AS adx,
       o.create_date, o.update_date
FROM stock_ohlcv_jp o JOIN stock_indicator_jp i USING (code, date);

CREATE OR REPLACE VIEW stock_data_split_week_kr AS
SELECT o.code, o.date, o.open, o.high, o.low, o.close, o.volume,
       (o.close::numeric * o.volume::numeric) AS transamnt,
       i."5mvavg", i."20mvavg", i."50mvavg", i."60mvavg", i."120mvavg", i."240mvavg",
       i.bollinger_upper_60_1 AS upperband60_1, i.bollinger_lower_60_1 AS lowerband60_1,
       i.bollinger_lower_60_3 AS lowerband60_3,
       NULL::BIGINT AS di_plus, NULL::BIGINT AS di_minus, NULL::BIGINT AS adx,
       o.create_date, o.update_date
FROM stock_ohlcv_week_kr o JOIN stock_indicator_week_kr i USING (code, date);

CREATE OR REPLACE VIEW stock_data_split_week_jp AS
SELECT o.code, o.date, o.open, o.high, o.low, o.close, o.volume,
       (o.close::numeric * o.volume::numeric) AS transamnt,
       i."5mvavg", i."20mvavg", i."50mvavg", i."60mvavg", i."120mvavg", i."240mvavg",
       i.bollinger_upper_60_1 AS upperband60_1, i.bollinger_lower_60_1 AS lowerband60_1,
       i.bollinger_lower_60_3 AS lowerband60_3,
       NULL::BIGINT AS di_plus, NULL::BIGINT AS di_minus, NULL::BIGINT AS adx,
       o.create_date, o.update_date
FROM stock_ohlcv_week_jp o JOIN stock_indicator_week_jp i USING (code, date);

-- 비교 예시:
-- SELECT * FROM stock_data_jp EXCEPT ALL SELECT * FROM stock_data_split_jp;
-- SELECT * FROM stock_data_split_jp EXCEPT ALL SELECT * FROM stock_data_jp;
