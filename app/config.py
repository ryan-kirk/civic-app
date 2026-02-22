from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    civicweb_base_url: str = "https://urbandale.civicweb.net"


settings = Settings()