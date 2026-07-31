\set ON_ERROR_STOP on

\getenv stock_db_user STOCK_DB_USER
\getenv stock_db_password STOCK_DB_PASSWORD
\getenv stock_db_name STOCK_DB_NAME
CREATE ROLE :"stock_db_user" LOGIN PASSWORD :'stock_db_password';
CREATE DATABASE :"stock_db_name" OWNER :"stock_db_user";
\connect :"stock_db_name"
SET ROLE :"stock_db_user";

CREATE TABLE stock_list_kr (
    code VARCHAR(12) PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    market VARCHAR(50) NOT NULL,
    order_no INTEGER,
    create_date TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    update_date TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_stock_list_kr_order ON stock_list_kr (order_no);

CREATE TABLE stock_list_jp (
    code VARCHAR(12) PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    stocktype VARCHAR(200),
    industry33code VARCHAR(50),
    industry33type VARCHAR(200),
    industry17code VARCHAR(50),
    industry17type VARCHAR(200),
    scalecode VARCHAR(50),
    scaletype VARCHAR(200),
    create_date TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    update_date TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_stock_list_jp_stocktype ON stock_list_jp (stocktype);

CREATE TABLE stock_data_kr (
    code VARCHAR(12) NOT NULL REFERENCES stock_list_kr (code),
    date DATE NOT NULL,
    open BIGINT, high BIGINT, low BIGINT, close BIGINT, volume BIGINT,
    transamnt BIGINT, "5mvavg" BIGINT, "20mvavg" BIGINT, "50mvavg" BIGINT,
    "60mvavg" BIGINT, "120mvavg" BIGINT, "240mvavg" BIGINT,
    upperband60_1 BIGINT, lowerband60_1 BIGINT, lowerband60_3 BIGINT,
    di_plus BIGINT, di_minus BIGINT, adx BIGINT,
    create_date TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    update_date TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (code, date)
);
CREATE INDEX idx_stock_data_kr_date ON stock_data_kr (date);

CREATE TABLE stock_data_week_kr (
    code VARCHAR(12) NOT NULL REFERENCES stock_list_kr (code),
    date DATE NOT NULL,
    open BIGINT, high BIGINT, low BIGINT, close BIGINT, volume BIGINT,
    transamnt BIGINT, "5mvavg" BIGINT, "20mvavg" BIGINT, "50mvavg" BIGINT,
    "60mvavg" BIGINT, upperband60_1 BIGINT, lowerband60_1 BIGINT,
    lowerband60_3 BIGINT, di_plus BIGINT, di_minus BIGINT, adx BIGINT,
    create_date TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    update_date TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (code, date)
);
CREATE INDEX idx_stock_data_week_kr_date ON stock_data_week_kr (date);

CREATE TABLE stock_data_jp (
    code VARCHAR(12) NOT NULL REFERENCES stock_list_jp (code),
    date DATE NOT NULL,
    open BIGINT, high BIGINT, low BIGINT, close BIGINT, volume BIGINT,
    transamnt BIGINT, "5mvavg" BIGINT, "20mvavg" BIGINT, "50mvavg" BIGINT,
    "60mvavg" BIGINT, "120mvavg" BIGINT, "240mvavg" BIGINT,
    upperband60_1 BIGINT, lowerband60_1 BIGINT, lowerband60_3 BIGINT,
    di_plus BIGINT, di_minus BIGINT, adx BIGINT,
    create_date TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    update_date TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (code, date)
);
CREATE INDEX idx_stock_data_jp_date ON stock_data_jp (date);

CREATE TABLE stock_data_week_jp (
    code VARCHAR(12) NOT NULL REFERENCES stock_list_jp (code),
    date DATE NOT NULL,
    open BIGINT, high BIGINT, low BIGINT, close BIGINT, volume BIGINT,
    transamnt BIGINT, "5mvavg" BIGINT, "20mvavg" BIGINT, "50mvavg" BIGINT,
    "60mvavg" BIGINT, upperband60_1 BIGINT, lowerband60_1 BIGINT,
    lowerband60_3 BIGINT, di_plus BIGINT, di_minus BIGINT, adx BIGINT,
    create_date TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    update_date TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (code, date)
);
CREATE INDEX idx_stock_data_week_jp_date ON stock_data_week_jp (date);

-- Split dataset pipeline: raw OHLCV is stored separately from technical indicators.
CREATE TABLE stock_ohlcv_kr (
    code VARCHAR(12) NOT NULL REFERENCES stock_list_kr (code),
    date DATE NOT NULL,
    open BIGINT, high BIGINT, low BIGINT, close BIGINT, volume BIGINT,
    create_date TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    update_date TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (code, date)
);
CREATE TABLE stock_ohlcv_week_kr (
    code VARCHAR(12) NOT NULL REFERENCES stock_list_kr (code), date DATE NOT NULL,
    open BIGINT, high BIGINT, low BIGINT, close BIGINT, volume BIGINT,
    create_date TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, update_date TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (code, date)
);
CREATE TABLE stock_ohlcv_jp (
    code VARCHAR(12) NOT NULL REFERENCES stock_list_jp (code),
    date DATE NOT NULL,
    open BIGINT, high BIGINT, low BIGINT, close BIGINT, volume BIGINT,
    create_date TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    update_date TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (code, date)
);
CREATE TABLE stock_ohlcv_week_jp (
    code VARCHAR(12) NOT NULL REFERENCES stock_list_jp (code), date DATE NOT NULL,
    open BIGINT, high BIGINT, low BIGINT, close BIGINT, volume BIGINT,
    create_date TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, update_date TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (code, date)
);

CREATE TABLE stock_indicator_kr (
    code VARCHAR(12) NOT NULL REFERENCES stock_list_kr (code),
    date DATE NOT NULL,
    "5mvavg" BIGINT, "20mvavg" BIGINT, "50mvavg" BIGINT,
    "60mvavg" BIGINT, "120mvavg" BIGINT, "240mvavg" BIGINT,
    bollinger_upper_60_1 BIGINT, bollinger_lower_60_1 BIGINT,
    bollinger_lower_60_3 BIGINT,
    ichimoku_conversion BIGINT, ichimoku_base BIGINT,
    ichimoku_span_a BIGINT, ichimoku_span_b BIGINT, ichimoku_lagging BIGINT,
    create_date TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    update_date TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (code, date)
);
CREATE TABLE stock_indicator_week_kr (
    code VARCHAR(12) NOT NULL REFERENCES stock_list_kr (code), date DATE NOT NULL,
    "5mvavg" BIGINT, "20mvavg" BIGINT, "50mvavg" BIGINT, "60mvavg" BIGINT, "120mvavg" BIGINT, "240mvavg" BIGINT,
    bollinger_upper_60_1 BIGINT, bollinger_lower_60_1 BIGINT, bollinger_lower_60_3 BIGINT,
    ichimoku_conversion BIGINT, ichimoku_base BIGINT, ichimoku_span_a BIGINT, ichimoku_span_b BIGINT, ichimoku_lagging BIGINT,
    create_date TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, update_date TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (code, date)
);
CREATE TABLE stock_indicator_jp (
    code VARCHAR(12) NOT NULL REFERENCES stock_list_jp (code),
    date DATE NOT NULL,
    "5mvavg" BIGINT, "20mvavg" BIGINT, "50mvavg" BIGINT,
    "60mvavg" BIGINT, "120mvavg" BIGINT, "240mvavg" BIGINT,
    bollinger_upper_60_1 BIGINT, bollinger_lower_60_1 BIGINT,
    bollinger_lower_60_3 BIGINT,
    ichimoku_conversion BIGINT, ichimoku_base BIGINT,
    ichimoku_span_a BIGINT, ichimoku_span_b BIGINT, ichimoku_lagging BIGINT,
    create_date TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    update_date TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (code, date)
);
CREATE TABLE stock_indicator_week_jp (
    code VARCHAR(12) NOT NULL REFERENCES stock_list_jp (code), date DATE NOT NULL,
    "5mvavg" BIGINT, "20mvavg" BIGINT, "50mvavg" BIGINT, "60mvavg" BIGINT, "120mvavg" BIGINT, "240mvavg" BIGINT,
    bollinger_upper_60_1 BIGINT, bollinger_lower_60_1 BIGINT, bollinger_lower_60_3 BIGINT,
    ichimoku_conversion BIGINT, ichimoku_base BIGINT, ichimoku_span_a BIGINT, ichimoku_span_b BIGINT, ichimoku_lagging BIGINT,
    create_date TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, update_date TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (code, date)
);

CREATE INDEX idx_stock_ohlcv_kr_date ON stock_ohlcv_kr (date);
CREATE INDEX idx_stock_ohlcv_week_kr_date ON stock_ohlcv_week_kr (date);
CREATE INDEX idx_stock_ohlcv_jp_date ON stock_ohlcv_jp (date);
CREATE INDEX idx_stock_ohlcv_week_jp_date ON stock_ohlcv_week_jp (date);
CREATE INDEX idx_stock_indicator_kr_date ON stock_indicator_kr (date);
CREATE INDEX idx_stock_indicator_week_kr_date ON stock_indicator_week_kr (date);
CREATE INDEX idx_stock_indicator_jp_date ON stock_indicator_jp (date);
CREATE INDEX idx_stock_indicator_week_jp_date ON stock_indicator_week_jp (date);

CREATE TABLE stock_predict_jp (
    data_cutoff DATE NOT NULL,
    code VARCHAR(12) NOT NULL REFERENCES stock_list_jp (code),
    probability DOUBLE PRECISION NOT NULL,
    run_name VARCHAR(255) NOT NULL,
    seq_len INTEGER NOT NULL,
    horizon_days INTEGER NOT NULL,
    rise_threshold DOUBLE PRECISION NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (data_cutoff, code, run_name)
);

CREATE TABLE stock_predict_kr (
    data_cutoff DATE NOT NULL,
    code VARCHAR(12) NOT NULL REFERENCES stock_list_kr (code),
    probability DOUBLE PRECISION NOT NULL,
    run_name VARCHAR(255) NOT NULL,
    seq_len INTEGER NOT NULL,
    horizon_days INTEGER NOT NULL,
    rise_threshold DOUBLE PRECISION NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (data_cutoff, code, run_name)
);

CREATE TABLE stock_predict_week_jp (
    data_cutoff DATE NOT NULL,
    code VARCHAR(12) NOT NULL REFERENCES stock_list_jp (code),
    probability DOUBLE PRECISION NOT NULL,
    run_name VARCHAR(255) NOT NULL,
    seq_len INTEGER NOT NULL,
    horizon_days INTEGER NOT NULL,
    rise_threshold DOUBLE PRECISION NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (data_cutoff, code, run_name)
);

CREATE TABLE stock_predict_week_kr (
    data_cutoff DATE NOT NULL,
    code VARCHAR(12) NOT NULL REFERENCES stock_list_kr (code),
    probability DOUBLE PRECISION NOT NULL,
    run_name VARCHAR(255) NOT NULL,
    seq_len INTEGER NOT NULL,
    horizon_days INTEGER NOT NULL,
    rise_threshold DOUBLE PRECISION NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (data_cutoff, code, run_name)
);

RESET ROLE;
