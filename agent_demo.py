import json
import os
from datetime import datetime
from getpass import getpass
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from database import create_db_engine, get_db
from models import (
    ExecutionLocation, ExecutionRecord, InspectionRecord, PlanInstance, PlanLocation,
    RequestPlanLink, ServiceRequest,
)
from seed_demo_location_hierarchy import seed_demo_location_hierarchy


def migrate(engine, revision="0007_agent_runs"):
    config = Config(str(Path(__file__).with_name("alembic.ini")))
    config.set_main_option("script_location", str(Path(__file__).with_name("migrations")))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, revision)


def seed_a_lobby(engine):
    seed_demo_location_hierarchy(engine)

    def at(time):
        return datetime.fromisoformat(f"2026-11-01T{time}+08:00")

    point = "LOC-A-LOBBY"
    sessions = sessionmaker(engine)
    with sessions.begin() as db:
        report = ServiceRequest(
            request_id=uuid4(), service_category="cleaning",
            description="合成演示：居民 09:40 在 A 大堂又看到地面有垃圾。",
            source="web", risk_status="unknown", location_id=point,
            observed_at=at("09:40"), received_at=at("10:00"),
        )
        plan = PlanInstance(
            request_id=uuid4(), service_category="cleaning", title="合成 A、B 大堂保洁计划",
            requirements="分别清理并核对两个大堂地面。", owner_name="合成负责人",
            locations=[PlanLocation(location_id=point), PlanLocation(location_id="LOC-B-LOBBY")],
            planned_start_at=at("09:00"), planned_end_at=at("09:30"),
        )
        db.add_all([report, plan])
        db.flush()
        db.add_all([
            RequestPlanLink(
                request_id=uuid4(), report_id=report.id, plan_id=plan.id,
                linked_by="合成人员", reason="同类同地点，联合核实",
            ),
            ExecutionRecord(
                request_id=uuid4(), plan_id=plan.id, executor_name="合成执行人",
                description="执行人填报 A 大堂保洁完成。", completion_claim="completed",
                locations=[ExecutionLocation(location_id=point)],
                claimed_finished_at=at("09:20"),
            ),
            InspectionRecord(
                request_id=uuid4(), plan_id=plan.id, inspector_name="合成巡查人",
                description="09:45 巡查看到纸屑，来源待核实。", finding="issue_observed",
                location_id=point, observed_at=at("09:45"),
            ),
        ])
        return report.id


def main():
    from main import app

    entered_key = not os.environ.get("GEMINI_API_KEY", "").strip()
    if entered_key:
        key = getpass("Gemini API key（输入不回显）：").strip()
        if not key:
            raise SystemExit("缺少 Gemini API key，未发起请求。")
        os.environ["GEMINI_API_KEY"] = key

    try:
        with TemporaryDirectory() as folder:
            engine = create_db_engine(Path(folder) / "synthetic.db")
            sessions = sessionmaker(engine, expire_on_commit=False)

            def synthetic_db():
                with sessions() as db:
                    yield db

            try:
                migrate(engine)
                report_id = seed_a_lobby(engine)
                endpoint = f"/api/service-requests/{report_id}/agent-runs"
                request_id = str(uuid4())
                app.dependency_overrides[get_db] = synthetic_db

                with TestClient(app) as client:
                    created = client.post(endpoint, json={"request_id": request_id})
                    created.raise_for_status()
                    loaded = client.get(endpoint)
                    loaded.raise_for_status()
                    saved = next(item for item in loaded.json() if item["id"] == request_id)

                if created.json() != saved:
                    raise RuntimeError("保存后的记录与回执不一致")

                relations = [
                    record["report_comparison"]["relation"]
                    for ref, record in (saved["evidence"] or {}).get("records", {}).items()
                    if ref.startswith("execution:") and record["report_comparison"] is not None
                ]
                print(json.dumps({
                    "synthetic": True,
                    "read_back": True,
                    "run_id": request_id,
                    "model": saved["model_name"],
                    "status": saved["status"],
                    "program_relations": relations,
                    "suggestion": saved["suggestion"],
                    "failure_stage": saved["failure_stage"],
                    "error_code": saved["error_code"],
                }, ensure_ascii=False, indent=2))
                return 0 if saved["status"] == "pending_review" and "after" in relations else 1
            finally:
                app.dependency_overrides.pop(get_db, None)
                engine.dispose()
    finally:
        if entered_key:
            os.environ.pop("GEMINI_API_KEY", None)


if __name__ == "__main__":
    raise SystemExit(main())