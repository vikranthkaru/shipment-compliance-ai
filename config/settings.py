from pydantic_settings import BaseSettings, SettingsConfigDict
class Settings(BaseSettings):
    chat_provider: str = "qwen"
    chat_model: str
    chat_api_key: str
    chat_base_url: str = ""

    embedding_provider: str = "qwen"
    embedding_model: str
    embedding_api_key: str
    embedding_base_url: str = ""

    memory_provider: str = "cockroachdb"
    cockroachdb_uri: str
    cockroachdb_min_size: int = 10
    cockroachdb_max_size: int = 100
    mongodb_uri: str
    mongodb_database: str = "shipment_agent_memory"


    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        extra="ignore"
    )

settings = Settings()