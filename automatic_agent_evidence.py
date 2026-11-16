"""Project the automatic snapshot into the existing evidence envelope and ref contract."""

from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from agent_evidence import AgentEvidence, EvidenceReadFailure
from automatic_evidence import AutomaticEvidenceRead, AutomaticEvidenceReadError, read_automatic_evidence
from models import utc_now


CONTEXT_REF = "context:investigation"
BEIJING = timezone(timedelta(hours=8))
TIME_FIELDS = (
    "observed_at", "received_at", "planned_start_at", "planned_end_at",
    "claimed_started_at", "claimed_finished_at", "recorded_at", "linked_at", "revoked_at",
)


def add_beijing_time_labels(record: dict) -> dict:
    """Add display-only labels; preserve original times and missingness, never infer times."""
    labels = {}
    for field in TIME_FIELDS:
        if field not in record:
            continue
        value = record[field]
        label = None
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value)
                if parsed.tzinfo is not None and parsed.utcoffset() is not None:
                    label = parsed.astimezone(BEIJING).strftime("%Y-%m-%d %H:%M:%S（北京时间）")
            except ValueError:
                pass
        labels[field] = label
    return {**record, "display_times_beijing": labels} if labels else dict(record)


def query_records(queries):
    records = {}
    for query in queries:
        suffix = ":" + query.scope["plan_ref"] if "plan_ref" in query.scope else ""
        records[f"search:{query.source}{suffix}"] = query.model_dump(mode="json")
    return records


def build_automatic_agent_evidence(source: AutomaticEvidenceRead) -> AgentEvidence:
    report_ref = f"report:{source.report.id}"
    # Replay tokens have no evidentiary meaning; all business fields and source versions remain.
    def original(row):
        return add_beijing_time_labels(row.model_dump(mode="json", exclude={"request_id", "revocation_request_id"}))

    records = {report_ref: {**original(source.report), "role": "selected_report"}}
    candidate_refs = []
    for item in source.related_reports:
        ref = f"report:{item.report.id}"
        candidate_refs.append(ref)
        records[ref] = {
            **original(item.report), "role": "candidate_report",
            "discovery": item.model_dump(mode="json", exclude={"report"}),
        }
    for item in source.plans:
        ref = f"plan:{item.plan.id}"
        candidate_refs.append(ref)
        records[ref] = {
            **original(item.plan), "role": "candidate_plan",
            "discovery": item.model_dump(mode="json", include={
                "automatic", "reason_codes", "manual_link_ids", "has_revoked_manual_link",
                "category_matches", "plan_location_matches", "warnings",
            }),
            "plan_checks": item.plan_checks.model_dump(mode="json"),
            "inspection_scope": {
                "report_location_id": source.report.location_id,
                "matched_inspection_count": sum(row.location_relation == "same_location" for row in item.inspections),
                "unknown_location_inspection_count": sum(row.location_relation == "location_unknown" for row in item.inspections),
                "background_inspection_count": sum(row.location_relation == "other_location" for row in item.inspections),
                # All other-location records remain clearly labelled background in this snapshot.
                "excluded_other_location_inspection_count": 0,
            },
        }
        for row in item.executions:
            records[f"execution:{row.record.id}"] = {
                **original(row.record), "plan_ref": ref, "location_relation": row.location_relation,
                "report_comparison": {"relation": row.report_relation_to_completion,
                                      "meaning": "居民观察相对于人员填报完成时间；仅时间比较，不代表已完成或已解决。"},
            }
        for row in item.inspections:
            records[f"inspection:{row.record.id}"] = {
                **original(row.record), "plan_ref": ref, "location_relation": row.location_relation,
                "observation_relation_to_report": row.observation_relation_to_report,
            }
    for row in source.manual_links:
        records[f"link:{row.id}"] = {**original(row), "plan_ref": f"plan:{row.plan_id}"}
    for row in source.locations:
        records[f"location:{row.id}"] = original(row)
    records.update(query_records(source.queries))
    records[CONTEXT_REF] = {
        "selected_report_ref": report_ref, "candidate_refs": candidate_refs,
        "warnings": source.warnings,
        "candidate_meaning": "自动发现的线索，尚未确认关联、同一事件、原因或解决。",
    }
    return AgentEvidence(report_ref=report_ref, source_rule_version=source.rule_version,
                         checked_at=source.checked_at, records=records)


def read_automatic_agent_evidence(db: Session, report_id: UUID) -> AgentEvidence:
    try:
        with db.begin():
            return build_automatic_agent_evidence(read_automatic_evidence(db, report_id))
    except AutomaticEvidenceReadError as error:
        # Only query statuses/scopes/refs, no partial business evidence or raw DB exception strings.
        records = query_records(error.queries)
        records[CONTEXT_REF] = {
            "selected_report_ref": f"report:{report_id}", "candidate_refs": [],
            "failure_source": error.source, "read_complete": False,
        }
        trace = AgentEvidence(
            report_ref=f"report:{report_id}", source_rule_version="automatic-evidence-failure-v1",
            checked_at=utc_now(), records=records,
        )
        raise EvidenceReadFailure(trace, error.code) from error
