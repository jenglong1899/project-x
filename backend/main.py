import os

import uvicorn

from src.web_app import build_app


app = build_app()


def main():
    uvicorn.run(
        app,
        host=os.getenv("PROJECT_X_HOST", "127.0.0.1"),
        port=int(os.getenv("PROJECT_X_PORT", "8000")),
    )


if __name__ == "__main__":
    main()
