import os

from dotenv import load_dotenv
load_dotenv()

import uvicorn

from src.web_app import build_app
from src.logging_config import configure_logging


app = build_app()


def main():
    configure_logging()
    uvicorn.run(
        app,
        host=os.getenv("PROJECT_X_HOST", "127.0.0.1"),
        port=int(os.getenv("PROJECT_X_PORT", "8000")),
    )


if __name__ == "__main__":
    main()
