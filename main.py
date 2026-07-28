import uvicorn

from app.main import create_app
from settings import Settings


app = create_app()


if __name__ == "__main__":
    settings = Settings.load()
    uvicorn.run(app, host=settings.app_host, port=settings.app_port)
