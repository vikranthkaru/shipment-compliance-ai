from config.settings import settings
from memory.mongodb import get_mongodb_memory
from memory.cockroachdb import get_cockroachdb_memory

def get_memory():
    """
    Returns the configured LangGraph memory implementation.
    """

    if settings.memory_provider == "cockroachdb":
        return get_cockroachdb_memory(
            connection_string=settings.cockroachdb_uri,
            min_size=settings.cockroachdb_min_size,
            max_size=settings.cockroachdb_max_size,
        )

    if settings.memory_provider == "mongodb":
        return get_mongodb_memory(
            connection_string=settings.mongodb_uri,
            database_name=settings.mongodb_database,
        )
    raise ValueError(
        f"Unsupported memory provider: {settings.memory_provider}"
    )