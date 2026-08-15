from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from langchain_cockroachdb import CockroachDBSaver


def get_cockroachdb_memory(
    connection_string: str,
    min_size: int = 1,
    max_size: int = 10,
) -> CockroachDBSaver:
    pool = ConnectionPool(
        conninfo=connection_string,
        min_size=min_size,
        max_size=max_size,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 5,
            "row_factory": dict_row,
        },
    )

    checkpointer = CockroachDBSaver(conn=pool)

    checkpointer.setup()

    return checkpointer