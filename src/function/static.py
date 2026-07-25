"""PostgreSQL runtime configuration for Docker and local model execution."""

import configparser
import os
from pathlib import Path


CONFIG_PATH = Path(
    os.getenv("STOCK_CONFIG_PATH", Path(__file__).resolve().parents[1] / "env" / "config.ini")
)
config = configparser.ConfigParser()
config.read(CONFIG_PATH, encoding="utf-8")


def setting(section: str, key: str, environment: str, default: str | None = None) -> str:
    value = os.getenv(environment) or config.get(section, key, fallback=default)
    if value is None or value == "":
        raise RuntimeError(f"Missing {environment} or [{section}] {key} in {CONFIG_PATH}")
    return value


dir = setting("default", "output_dir", "OUTPUT_DIR", str(Path.cwd() / "data"))

db_config = {
    "host": setting("database", "host", "STOCK_DB_HOST", "postgres"),
    "port": int(setting("database", "port", "STOCK_DB_PORT", "5432")),
    "user": setting("database", "user", "STOCK_DB_USER", "stock_app"),
    "password": setting("database", "password", "STOCK_DB_PASSWORD"),
    "dbname": setting("database", "database", "STOCK_DB_NAME", "stock"),
}

db_config_jp = db_config.copy()
db_config_kr = db_config.copy()

start_date = setting("default", "start_date", "STOCK_START_DATE", "2023-01-01")
end_date = setting("default", "end_date", "STOCK_END_DATE", "2099-12-31")
period = int(setting("default", "period", "STOCK_PERIOD", "3"))
