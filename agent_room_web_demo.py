import os
from getpass import getpass
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import uvicorn
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from agent_demo import migrate
from agent_room_demo import prepare_case, seed_room_locations
from database import create_db_engine, get_db
from main import app


def main():
    if not os.environ.get("GEMINI_API_KEY", "").strip():
        key = getpass("Gemini API key（输入不回显）：").strip()
        if not key:
            raise SystemExit("缺少 Gemini API key。")
        os.environ["GEMINI_API_KEY"] = key

    with TemporaryDirectory() as folder:
        engine = create_db_engine(Path(folder) / "room502-web.db")
        sessions = sessionmaker(engine)

        def demo_db():
            with sessions() as db:
                yield db

        try:
            migrate(engine)
            seed_room_locations(engine)
            with patch.dict(app.dependency_overrides, {get_db: demo_db}):
                with TestClient(app) as client:
                    report_path, _, _, _ = prepare_case(client)
                report_id = report_path.rsplit("/", 1)[-1]
                print("报事列表：http://127.0.0.1:8011/reports", flush=True)
                print(f"本次合成报事编号：{report_id}", flush=True)
                uvicorn.run(app, host="127.0.0.1", port=8011)
        finally:
            engine.dispose()


if __name__ == "__main__":
    main()