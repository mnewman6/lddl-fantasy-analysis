"""Runtime configuration loaded from .env via pydantic-settings."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    sleeper_league_id: str = Field(
        default="",
        description="Current-season Sleeper league ID; the ingest pipeline walks "
        "previous_league_id from this to fetch every historical season.",
    )
    league_name: str = Field(default="LDDL")

    data_dir: Path = Field(default=PROJECT_ROOT / "data")
    output_dir: Path = Field(default=PROJECT_ROOT / "lddl" / "output")

    @property
    def duckdb_path(self) -> Path:
        return self.data_dir / "lddl.duckdb"

    @property
    def raw_cache_dir(self) -> Path:
        return self.data_dir / "raw"


def get_settings() -> Settings:
    return Settings()
