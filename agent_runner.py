from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import ValidationError

from agent_evidence import (
    AgentEvidence, AgentSuggestion, AutomaticSuggestion, CandidateCoverageError,
    EvidenceReadFailure, EvidenceReferenceError, trace_only_evidence, validate_agent_suggestion,
)


MAX_EVIDENCE_BYTES = 96_000
MAX_OUTPUT_BYTES = 32_000
# tool_request 和 tool_dispatch 用于读回旧版运行记录。
FailureStage = Literal["tool_request", "tool_dispatch", "evidence_read", "suggestion_request", "validation"]


class EvidenceModel(Protocol):
    model_name: str

    def suggest(self, evidence: AgentEvidence) -> str: ...


@dataclass
class AgentOutcome:
    evidence: AgentEvidence | None = None
    suggestion: AgentSuggestion | AutomaticSuggestion | None = None
    failure_stage: FailureStage | None = None
    error_code: str | None = None

    @property
    def status(self) -> Literal["pending_review", "failed"]:
        return "pending_review" if self.suggestion is not None else "failed"


def run_agent(model: EvidenceModel, read_evidence: Callable[[], AgentEvidence]) -> AgentOutcome:
    try:
        evidence = read_evidence()
        if len(evidence.model_dump_json().encode("utf-8")) > MAX_EVIDENCE_BYTES:
            trace = trace_only_evidence(evidence) if evidence.source_rule_version == "automatic-evidence-v1" else None
            return AgentOutcome(evidence=trace, failure_stage="evidence_read", error_code="EvidenceTooLarge")
    except EvidenceReadFailure as error:
        return AgentOutcome(evidence=error.evidence, failure_stage="evidence_read", error_code=error.code)
    except Exception as error:
        return AgentOutcome(failure_stage="evidence_read", error_code=type(error).__name__)

    try:
        draft = model.suggest(evidence)
    except Exception as error:
        return AgentOutcome(evidence=evidence, failure_stage="suggestion_request", error_code=type(error).__name__)

    if not isinstance(draft, str):
        return AgentOutcome(evidence=evidence, failure_stage="validation", error_code="UnexpectedOutputType")
    try:
        if len(draft.encode("utf-8")) > MAX_OUTPUT_BYTES:
            return AgentOutcome(evidence=evidence, failure_stage="validation", error_code="OutputTooLarge")
        suggestion = validate_agent_suggestion(draft, evidence)
    except (ValidationError, EvidenceReferenceError, CandidateCoverageError, UnicodeError) as error:
        return AgentOutcome(evidence=evidence, failure_stage="validation", error_code=type(error).__name__)
    return AgentOutcome(evidence=evidence, suggestion=suggestion)
