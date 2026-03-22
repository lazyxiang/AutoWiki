import pytest
from httpx import AsyncClient, ASGITransport


@pytest.fixture
async def client(tmp_path):
    import os
    os.environ["DATABASE_PATH"] = str(tmp_path / "test.db")
    os.environ["AUTOWIKI_DATA_DIR"] = str(tmp_path)
    from shared.config import reset_config
    reset_config()
    from shared.database import init_db
    await init_db(str(tmp_path / "test.db"))
    from api.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    from shared.database import dispose_db
    await dispose_db(str(tmp_path / "test.db"))
    reset_config()
