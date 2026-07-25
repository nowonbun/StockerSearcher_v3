from dataclasses import dataclass


@dataclass
class StockListNode:
    b: int
    code: str
    name: str
    stocktype: str
    industry33code: str
    industry33type: str
    industry17code: str
    industry17type: str
    scalecode: str
    scaletype: str

    def __getitem__(self, key):
        return getattr(self, key)


def generateSqlQuery(nodes: list[StockListNode], table_name: str = "STOCK_LIST_JP") -> tuple[str, list[tuple[object, ...]]]:
    if table_name != "STOCK_LIST_JP":
        raise ValueError(f"Unsupported stock list table: {table_name}")

    column = [
        "code",
        "name",
        "stocktype",
        "industry33code",
        "industry33type",
        "industry17code",
        "industry17type",
        "scalecode",
        "scaletype",
    ]
    placeholders = ", ".join(["%s"] * len(column))
    query = (
        f"INSERT INTO {table_name} ("
        + ", ".join(column)
        + ", create_date, update_date) "
        + f"VALUES ({placeholders}, now(), now()) "
        + "ON CONFLICT (code) DO UPDATE SET "
        + ", ".join([f"{value} = EXCLUDED.{value}" for value in column])
        + ", update_date = now()"
    )
    payload = [tuple(node[value] for value in column) for node in nodes]
    return query, payload
