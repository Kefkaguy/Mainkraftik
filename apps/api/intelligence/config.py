from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    database_url: str = "postgresql://intelligence:intelligence@127.0.0.1:5543/intelligence"
    admin_username: str = "admin"
    admin_password_hash: str = ""
    origin: str = "http://127.0.0.1:3100"
    sec_user_agent: str = ""
    alpaca_key: str = ""
    alpaca_secret: str = ""
    feed: Literal["sip", "iex"] = "sip"
    environment: Literal["development", "production", "test"] = "development"
    data_dir: str = ".local/data"
    min_free_gb: float = 10
    vision_max_bytes: int = 2 * 1024**3
    paper_service_url: str='http://127.0.0.1:8200'
    paper_internal_token: str=''
    model_config = SettingsConfigDict(env_prefix="INTELLIGENCE_", env_file=ROOT / ".env", extra="ignore")

    @property
    def storage(self) -> Path:
        p = Path(self.data_dir)
        target = p if p.is_absolute() else ROOT / p
        # Lexical normalization avoids inspecting any external directory.
        import os

        target = Path(os.path.abspath(target))
        allowed = Path(os.path.abspath(ROOT / ".local"))
        if not target.is_relative_to(allowed):
            raise ValueError("Data storage must remain inside this project's .local directory")
        return target


settings = Settings()
