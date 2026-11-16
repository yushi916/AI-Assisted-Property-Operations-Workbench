from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue, StringConstraints

from report_evidence_schemas import ReportEvidenceRead
from source_state_rules import find_source_state_conflicts


class AgentEvidence(BaseModel):
    report_ref: str
    source_rule_version: str
    checked_at: AwareDatetime
    records: dict[str, dict[str, JsonValue]]


def build_agent_evidence(source: ReportEvidenceRead) -> AgentEvidence:
    report = source.report
    report_ref = f"report:{report.id}"
    records = {report_ref: report.model_dump(mode="json", include={
        "service_category", "description", "risk_status", "location_id", "observed_at",
    })}
    active_links = {link.id: link for link in source.links if link.status == "active"}

    for item in source.linked_plans:
        link = active_links[item.link_id]
        plan_ref = f"plan:{item.plan.id}"
        records[f"link:{link.id}"] = {"plan_ref": plan_ref, "reason": link.reason}
        records[plan_ref] = {
            **item.plan.model_dump(mode="json", include={
                "title", "service_category", "requirements", "location_ids",
                "planned_start_at", "planned_end_at",
            }),
            "scope_matches": item.scope_matches,
            "warnings": item.warnings,
        }
        if not item.scope_matches:
            continue

        records[plan_ref]["inspection_scope"] = {
            "report_location_id": report.location_id,
            "matched_inspection_count": sum(
                row.location_id == report.location_id for row in item.inspections
            ),
            "excluded_other_location_inspection_count": item.excluded_inspection_count,
        }

        comparisons = {row.execution_id: row for row in item.report_comparisons}
        for row in item.executions:
            comparison = comparisons.get(row.id)
            records[f"execution:{row.id}"] = {
                **row.model_dump(mode="json", include={
                    "description", "completion_claim", "location_ids",
                    "claimed_started_at", "claimed_finished_at",
                }),
                "plan_ref": plan_ref,
                "report_comparison": (
                    comparison.model_dump(mode="json", include={"relation", "message"})
                    if comparison is not None else None
                ),
            }
        for row in item.inspections:
            records[f"inspection:{row.id}"] = {
                **row.model_dump(mode="json", include={
                    "description", "finding", "location_id", "observed_at",
                }),
                "plan_ref": plan_ref,
            }

    return AgentEvidence(
        report_ref=report_ref, source_rule_version=source.rule_version,
        checked_at=source.checked_at, records=records,
    )


SuggestionText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]


class AgentObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: SuggestionText
    evidence_refs: list[str] = Field(min_length=1, max_length=10)


class AgentSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observations: list[AgentObservation] = Field(min_length=1, max_length=10)
    verification_items: list[SuggestionText] = Field(min_length=1, max_length=10)


class CandidateAssessment(AgentObservation):
    candidate_ref: str
    disposition: Literal["check_together", "consider_separately", "insufficient_information"]


class AutomaticSuggestion(AgentSuggestion):
    schema_version: Literal["automatic-suggestion-v1"]
    candidate_assessments: list[CandidateAssessment] = Field(max_length=100)
    uncertainties: list[AgentObservation] = Field(min_length=1, max_length=10)


class EvidenceReferenceError(ValueError):
    pass


class SourceStateConflictError(EvidenceReferenceError):
    """A recognized empty-result statement conflicts with an unavailable source."""


class CandidateCoverageError(ValueError):
    pass


class EvidenceReadFailure(RuntimeError):
    """A failed read with query metadata only; never send this trace to the model."""
    def __init__(self, evidence: AgentEvidence, code: str):
        super().__init__(code)
        self.evidence, self.code = evidence, code


def trace_only_evidence(evidence: AgentEvidence) -> AgentEvidence:
    return AgentEvidence(
        report_ref=evidence.report_ref, source_rule_version="automatic-evidence-failure-v1",
        checked_at=evidence.checked_at,
        records={ref: record for ref, record in evidence.records.items()
                 if ref.startswith("search:") or ref == "context:investigation"},
    )


def validate_agent_suggestion(raw_json: str, evidence: AgentEvidence) -> AgentSuggestion | AutomaticSuggestion:
    automatic = evidence.source_rule_version == "automatic-evidence-v1"
    schema = AutomaticSuggestion if automatic else AgentSuggestion
    suggestion = schema.model_validate_json(raw_json)
    sections = list(suggestion.observations)
    if automatic:
        sections += suggestion.candidate_assessments + suggestion.uncertainties
    for observation in sections:
        if any(ref not in evidence.records for ref in observation.evidence_refs):
            raise EvidenceReferenceError("建议引用了本次证据范围外的记录")
    if automatic:
        expected = set(evidence.records["context:investigation"]["candidate_refs"])
        actual = [item.candidate_ref for item in suggestion.candidate_assessments]
        if len(actual) != len(set(actual)) or set(actual) != expected:
            raise CandidateCoverageError("候选判断存在遗漏、重复或本次范围外的候选")
        if not any(evidence.report_ref in row.evidence_refs for row in suggestion.observations):
            raise EvidenceReferenceError("关键事实必须引用本条居民报事")
        for item in suggestion.candidate_assessments:
            if item.candidate_ref not in item.evidence_refs or evidence.report_ref not in item.evidence_refs:
                raise EvidenceReferenceError("候选判断必须引用候选本身和本条居民报事")
        for text in [row.text for row in sections] + list(suggestion.verification_items):
            conflicts = find_source_state_conflicts(text, evidence.records)
            if conflicts:
                source = conflicts[0]
                raise SourceStateConflictError(
                    f"{source.source_ref}为{source.status}，不能表述为已查询但没有记录"
                )
    return suggestion
