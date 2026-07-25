# 디렉토리 존재 여부를 확인하고 없으면 생성한다.
def check_directory(dir_path):
    import os

    if not os.path.exists(dir_path):
        os.makedirs(dir_path)


def createQuery(table, columns, values):
    """INSERT 쿼리를 문자열로 생성한다."""
    query = f"INSERT INTO {table} ("
    query += ", ".join(columns)
    query += ") VALUES ("
    query += ", ".join([f"'{v}'" for v in values])
    query += ")"
    return query


def setup_custom_logger(dir, name):
    """파일 핸들러가 붙은 로거를 초기화한다."""
    import logging
    import os
    from datetime import datetime

    formatter = logging.Formatter(
        fmt="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    date_str = datetime.now().strftime("%Y-%m-%d")
    handler = logging.FileHandler(
        os.path.join(dir, "log", f"logfile_{name}_{date_str}.log")
    )  # 로그 파일 이름 및 경로 지정
    handler.setFormatter(formatter)

    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)

    return logger


def write_log(logger, msg):
    """로거에 메시지를 기록하고 콘솔에도 출력한다."""
    logger.info(msg)
    print(msg)


def write_data(filename, msg):
    """지정한 텍스트 파일에 한 줄씩 메시지를 기록한다."""
    with open(filename, mode="a", newline="", encoding="utf-8") as file:
        file.write(msg)
        file.write("\n")


def create_sequences(data_input, data_target, seq_length, predict_days):
    """시계열 모델 학습을 위해 입력/타깃 시퀀스를 생성한다."""
    import numpy as np

    x = []
    y = []
    for i in range(len(data_input) - seq_length - predict_days + 1):
        x.append(data_input[i : i + seq_length])
        y.append(data_target[i + seq_length : i + seq_length + predict_days])
    return np.array(x), np.array(y)


def save_list_to_csv(file_path, data):
    """2차원 리스트 데이터를 CSV 파일로 저장한다."""
    import os

    if os.path.exists(file_path):
        os.remove(file_path)

    with open(file_path, mode="w", newline="", encoding="utf-8") as file:
        import csv

        writer = csv.writer(file)
        writer.writerows(data)


def get_date_2year_ago():
    """오늘 기준으로 2년 전 날짜(YYYY-MM-DD)를 반환한다."""
    from datetime import datetime, timedelta

    two_years_ago = datetime.today() - timedelta(days=365 * 2)
    return two_years_ago.strftime("%Y-%m-%d")


def execute_query(db_config, query):
    """데이터베이스에 연결해 단일 쿼리를 실행한다."""
    import psycopg

    with psycopg.connect(**db_config) as conn:
        with conn.cursor() as cursor:
            cursor.execute(query)


def execute_many(db_config, query, rows):
    """파라미터 바인딩을 사용해 여러 행을 하나의 트랜잭션으로 실행한다."""
    import psycopg

    with psycopg.connect(**db_config) as conn:
        with conn.cursor() as cursor:
            cursor.executemany(query, rows)
