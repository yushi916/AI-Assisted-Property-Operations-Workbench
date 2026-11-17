"""Deterministic output for plumbing tests; this is not a model quality evaluator."""


def automatic_draft(evidence, *, observations=None, verification_items=None):
    seed = evidence.report_ref
    candidates = evidence.records["context:investigation"]["candidate_refs"]
    point = evidence.records[seed].get("location_id")
    location = evidence.records.get(f"location:{point}", {}).get("display_path", "居民所述地点（待确认）")
    return {
        "schema_version": "automatic-suggestion-v1",
        "observations": observations or [{"text": "已收到居民原始报事，实际情况仍需核实。", "evidence_refs": [seed]}],
        "candidate_assessments": [{
            "candidate_ref": ref, "disposition": "insufficient_information",
            "text": "程序找到了此项线索，是否对应居民所述问题仍需核对原文及现场。",
            "evidence_refs": [seed, ref],
        } for ref in candidates],
        "uncertainties": [{
            "text": "尚未接入设备及管网资料，无法确认共同设备或故障原因。",
            "evidence_refs": ["search:assets"],
        }],
        "verification_items": verification_items or [f"请值班人员前往{location}核对报事原文所述问题，并补充现场观察时间和检查记录。"],
    }
