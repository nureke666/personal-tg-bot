from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", env_ignore_empty=True, extra="ignore")

    bot_token: str
    owner_id: int | None = None
    default_tz: str = "Asia/Almaty"
    db_path: str = "data/bot.db"
    tick_seconds: int = 30
