import argparse
import json
import os
import sys
from getpass import getpass
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from agent_demo import migrate
from agent_evidence import build_agent_evidence
from agent_runner import run_agent
from database import create_db_engine, get_db
from gemini_agent import GeminiEvidenceModel
from models import Location
from report_evidence_schemas import ReportEvidenceRead
from seed_demo_location_hierarchy import seed_demo_location_hierarchy


BUILDING = "LOC-A-B03"
ROOM = "LOC-A-B03-R502"
HALL = "LOC-A-B03-HALL"


def seed_room_locations(engine):
    seed_demo_location_hierarchy(engine)
    sessions = sessionmaker(engine)
    with sessions.begin() as db:
        db.add(Location(id=BUILDING, name="3栋", kind="building", parent_id="LOC-A"))
        db.flush()
        db.add_all([
            Location(id=ROOM, name="502室", kind="room", parent_id=BUILDING),
            Location(id=HALL, name="公共走廊", kind="public_space", parent_id=BUILDING),
        ])


def read_json(response):
    response.raise_for_status()
    return response.json()


def prepare_case(client, include_room_inspection=False):
    locations = read_json(client.get("/api/locations"))
    room = next(item for item in locations if item["id"] == ROOM)
    if room["kind"] != "room" or room["display_path"] != "合成小区 / A区 / 3栋 / 502室":
        raise RuntimeError("地点台账没有正确显示 3 栋 502 室")

    def create(path, **fields):
        return read_json(client.post(path, json={"request_id": str(uuid4()), **fields}))

    report = create(
        "/api/service-requests", service_category="water_supply",
        description="合成报事：3栋502室住户称今天早上全屋没有水。",
        source="web", risk_status="unknown", location_id=ROOM,
        observed_at="2026-11-01T08:00:00+08:00",
    )
    report_path = f"/api/service-requests/{report['id']}"
    plan = create(
        "/api/plans", service_category="water_supply", title="合成3栋供水核查计划",
        requirements="分别核实502室室内供水和3栋公共走廊龙头供水。",
        owner_name="合成负责人", location_ids=[ROOM, HALL],
        planned_start_at="2026-11-01T08:10:00+08:00",
        planned_end_at="2026-11-01T10:00:00+08:00",
    )
    public_inspection = create(
        "/api/inspections", plan_id=plan["id"], inspector_name="合成巡查人",
        description="3栋公共走廊的公共龙头出水正常，未入户。",
        finding="no_issue_observed", location_id=HALL,
        observed_at="2026-11-01T08:30:00+08:00",
    )
    if include_room_inspection:
        create(
            "/api/inspections", plan_id=plan["id"], inspector_name="合成巡查人",
            description="入户检查502室厨房龙头，现场未出水。",
            finding="issue_observed", location_id=ROOM,
            observed_at="2026-11-01T08:40:00+08:00",
        )
    create(
        f"{report_path}/plan-links", plan_id=plan["id"], linked_by="合成人员",
        reason="计划明确覆盖502室，关联后核实住户报事。",
    )

    evidence = read_json(client.get(f"{report_path}/evidence-check"))
    scope = evidence["linked_plans"][0]
    if (report["location_id"] != ROOM or evidence["report"]["location_id"] != ROOM
            or not scope["scope_matches"]
            or len(scope["inspections"]) != int(include_room_inspection)
            or any(row["location_id"] != ROOM for row in scope["inspections"])
            or scope["excluded_inspection_count"] != 1):
        raise RuntimeError("502室地点绑定或公共区域巡查排除检查失败")

    output = {
        "synthetic": True,
        "report_location": room["display_path"],
        "excluded_public_inspections": scope["excluded_inspection_count"],
        "matched_room_inspections": len(scope["inspections"]),
        "warnings": scope["warnings"],
    }
    return report_path, f"inspection:{public_inspection['id']}", output, evidence


def require_key():
    if not os.environ.get("GEMINI_API_KEY", "").strip():
        key = getpass("Gemini API key（输入不回显）：").strip()
        if not key:
            raise SystemExit("缺少 Gemini API key。")
        os.environ["GEMINI_API_KEY"] = key


def run_case(client, evidence_only):
    report_path, public_ref, output, _ = prepare_case(client)
    if evidence_only:
        return output

    require_key()
    run_path = f"{report_path}/agent-runs"
    created = read_json(client.post(run_path, json={"request_id": str(uuid4())}))
    saved = next(item for item in read_json(client.get(run_path)) if item["id"] == created["id"])
    if saved != created:
        raise RuntimeError("保存后读回的记录与回执不一致")
    records = (saved["evidence"] or {}).get("records", {})
    if public_ref in records:
        raise RuntimeError("公共区域巡查混入了502室的模型证据")
    return {
        **output,
        "read_back": True,
        "model": saved["model_name"],
        "status": saved["status"],
        "evidence_refs": list(records),
        "suggestion": saved["suggestion"],
        "failure_stage": saved["failure_stage"],
        "error_code": saved["error_code"],
    }


def compare_scope(client, repeats):
    cases = []
    for include_room_inspection, label in ((False, "hall_only"), (True, "hall_and_room")):
        _, public_ref, _, source = prepare_case(client, include_room_inspection)
        candidate = build_agent_evidence(ReportEvidenceRead.model_validate(source))
        baseline = candidate.model_copy(deep=True)
        plan_ref = f"plan:{source['linked_plans'][0]['plan']['id']}"
        scope = baseline.records[plan_ref].pop("inspection_scope")
        if scope != {
            "report_location_id": ROOM,
            "matched_inspection_count": int(include_room_inspection),
            "excluded_other_location_inspection_count": 1,
        } or public_ref in candidate.records:
            raise RuntimeError("对比样本的巡查范围不符合预期")
        cases.append((label, scope, baseline, candidate))

    require_key()
    model = GeminiEvidenceModel()
    runs = []
    for pair in range(1, repeats + 1):
        for case, scope, baseline, candidate in cases:
            variants = (("baseline", baseline), ("candidate", candidate))
            for variant, evidence in variants if pair % 2 else reversed(variants):
                print(f"{case} pair {pair} {variant}", file=sys.stderr, flush=True)
                result = run_agent(model, lambda: evidence)
                runs.append({
                    "case": case,
                    "pair": pair,
                    "variant": variant,
                    "inspection_scope": scope if variant == "candidate" else None,
                    "status": result.status,
                    "suggestion": result.suggestion.model_dump() if result.suggestion else None,
                    "failure_stage": result.failure_stage,
                    "error_code": result.error_code,
                })

    return {"synthetic": True, "model": model.model_name, "runs": runs}


def main():
    from main import app

    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--evidence-only", action="store_true")
    mode.add_argument("--compare-scope", action="store_true")
    parser.add_argument("--repeats", type=int, choices=range(1, 6), default=2)
    args = parser.parse_args()

    with TemporaryDirectory() as folder:
        engine = create_db_engine(Path(folder) / "room502.db")
        sessions = sessionmaker(engine)

        def temporary_db():
            with sessions() as db:
                yield db

        try:
            migrate(engine)
            seed_room_locations(engine)
            with patch.dict(app.dependency_overrides, {get_db: temporary_db}), TestClient(app) as client:
                if args.compare_scope:
                    output = compare_scope(client, args.repeats)
                else:
                    output = run_case(client, args.evidence_only)
        finally:
            engine.dispose()

    print(json.dumps(output, ensure_ascii=False, indent=2))
    if args.compare_scope:
        return 0 if all(run["status"] == "pending_review" for run in output["runs"]) else 1
    return 0 if args.evidence_only or output["status"] == "pending_review" else 1


if __name__ == "__main__":
    raise SystemExit(main())