import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    port: int
    secret_key: str | None


def load_settings() -> Settings:
    return Settings(
        data_dir=Path(os.environ.get("DATA_DIR", "/data")),
        port=int(os.environ.get("PORT", "8080")),
        secret_key=os.environ.get("SECRET_KEY") or None,
    )
