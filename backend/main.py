import os

import uvicorn

from src.web_app import build_app


app = build_app()


def main():
    uvicorn.run(
        app,
        host=os.getenv("CATCLAW_HOST", "127.0.0.1"),
        port=int(os.getenv("CATCLAW_PORT", "8000")),
    )


if __name__ == "__main__":
    main()
