import os

import uvicorn

from bench.api.app import create_app
from bench.config import load_settings


def main() -> None:
    settings = load_settings()
    uvicorn.run(create_app(settings.data_dir, settings.secret_key),
                host="0.0.0.0", port=settings.port,
                log_level=os.environ.get("LOG_LEVEL", "info"))


if __name__ == "__main__":
    main()
