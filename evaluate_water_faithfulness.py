"""Freeze a saved run, then optionally replay its evidence. No database or HTTP writes.

`capture` only GETs saved runs; `replay` makes 1--3 explicit Gemini calls.
`review` exports a local citation comparison; it never calls the model or database.
Semantic review remains manual; structural validation is recorded separately.
"""

import argparse
import hashlib
import json
import os
from html import escape
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import ProxyHandler, build_opener
from uuid import UUID, uuid4

from agent_evidence import AgentEvidence, validate_agent_suggestion
from agent_runner import MAX_EVIDENCE_BYTES, MAX_OUTPUT_BYTES
from automatic_agent_evidence import add_beijing_time_labels
from gemini_agent import (
    AUTOMATIC_INSTRUCTIONS, AUTOMATIC_OUTPUT_VERSION, AUTOMATIC_PROMPT_VERSION,
    GeminiEvidenceModel, automatic_response_schema,
)


ROOT = Path(__file__).resolve().parent
CHECKS = [
    "事实范围：W1只涉及厨房和卫生间龙头；W3只试一个公共龙头，不能断言仅该点异常。",
    "冷热水：W1/W2明确为冷水；不得扩大为冷热水或全屋用水均异常。",
    "观察时间：W1为2026-10-13 09:20北京时间；不得称为接收时间或改用11月录入日期。",
    "症状与原因：W2值得一起核查；W3可分开或一起核查但须解释差异、保留不确定性；不确认共同原因。",
    "未知信息：保留W1原文不知道邻居情况、W3住户情况不清楚；其他报事是线索，不是替原居民作确认。",
    "出处与行动：引用对应记录，具体核查地点与症状；无设备资料不能假定共用管网或宣称恢复供水。",
]


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def request_error_details(error):
    message = getattr(error, "message", None)
    message = message if isinstance(message, str) else "SDK未提供结构化错误说明。"
    secret = os.environ.get("GEMINI_API_KEY", "").strip()
    if secret:
        message = message.replace(secret, "[REDACTED]")
    return {"request_error": type(error).__name__, "http_code": getattr(error, "code", None),
            "api_status": getattr(error, "status", None), "api_message": message[:4000]}


def save_result(value, directory, prefix):
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = directory / f"{prefix}-{stamp}-{uuid4().hex[:8]}.json"
    with path.open("x", encoding="utf-8") as output:
        json.dump(value, output, ensure_ascii=False, indent=2)
    print(path.resolve())
    return path


def capture(args):
    url = f"http://127.0.0.1:8000/api/service-requests/{args.report_id}/agent-runs"
    # Loopback only; do not send local records through configured proxies.
    with build_opener(ProxyHandler({})).open(url, timeout=15) as response:
        runs = json.load(response)
    selected = next((row for row in runs if row.get("id") == str(args.run_id)), None)
    if selected is None:
        raise ValueError("最近10次记录中未找到指定编号；没有替换成另一条结果。")
    if selected.get("report_id") != str(args.report_id):
        raise ValueError("报事编号不一致。")
    evidence = AgentEvidence.model_validate(selected["evidence"])
    if evidence.report_ref != f"report:{args.report_id}":
        raise ValueError("证据不属于指定报事。")
    old_prompt = (ROOT / "docs/fixtures/automatic-prompt-before-faithfulness-20261114.txt").read_text(encoding="utf-8").strip()
    return save_result({
        "kind": "water-faithfulness-baseline-v1", "selected_run": selected,
        "recent_saved_runs": runs, "evidence_sha256": digest(selected["evidence"]),
        "reference_before_prompt": old_prompt,
        "historical_request_limit": "旧运行未保存真实网络请求和提示词版本。提示词为交付源码参考副本，不冒称历史网络截获。",
        "semantic_review": {"status": "needs_revision", "checks": CHECKS,
                            "known_issues": ["全屋无水扩大观察范围", "单点异常被断言为局部", "UTC正文与观察/反映时间混淆"]},
    }, args.output_dir, "w1-baseline")


def prepare_replay(bundle):
    saved = bundle["selected_run"]
    if digest(saved["evidence"]) != bundle["evidence_sha256"]:
        raise ValueError("基线证据摘要不一致，停止复测。")
    evidence = AgentEvidence.model_validate(saved["evidence"])
    if evidence.source_rule_version != "automatic-evidence-v1":
        raise ValueError("仅复测完整自动取证快照。")
    if bundle.get("replay_policy") == "exact_saved_evidence":
        # R1 already contains display labels. Preserve every saved field for this comparison.
        return evidence
    # Same business evidence; only deterministic display labels are added.
    records = {ref: add_beijing_time_labels(row) if ref.split(":", 1)[0] in
               {"report", "plan", "execution", "inspection", "link"} else dict(row)
               for ref, row in evidence.records.items()}
    return evidence.model_copy(update={"records": records})


def replay(args):
    bundle = json.loads(args.input.read_text(encoding="utf-8"))
    evidence = prepare_replay(bundle)
    if len(evidence.model_dump_json().encode("utf-8")) > MAX_EVIDENCE_BYTES:
        raise ValueError("复测输入超过主流程大小上限，未调用模型。")
    model = GeminiEvidenceModel()
    if not os.environ.get("GEMINI_API_KEY", "").strip():
        raise ValueError("当前终端未配置GEMINI_API_KEY。")
    if model.model_name != bundle["selected_run"]["model_name"]:
        raise ValueError("模型与基线不同；请先设置GEMINI_MODEL，避免把模型切换混入此次比较。")
    manifest = {
        "kind": "water-faithfulness-replay-v1", "baseline_run_id": bundle["selected_run"]["id"],
        "model_name": model.model_name, "prompt_version": AUTOMATIC_PROMPT_VERSION,
        "output_schema_version": AUTOMATIC_OUTPUT_VERSION,
        "response_json_schema": automatic_response_schema(evidence),
        "system_instruction": AUTOMATIC_INSTRUCTIONS, "input": evidence.model_dump(mode="json"),
        "input_sha256": digest(evidence.model_dump(mode="json")),
        "baseline_evidence_sha256": bundle["evidence_sha256"],
        "input_matches_saved_evidence": digest(evidence.model_dump(mode="json")) == bundle["evidence_sha256"],
        "baseline_semantic_review": bundle.get("semantic_review", {"status": "not_reviewed"}),
        "comparison_limit": "固定快照复测；结合input_matches_saved_evidence和保存的prompt/schema比较。旧运行未保存真实请求时不能声称严格单变量实验；结构通过不代表内容通过。",
        "runs": [],
    }
    for number in range(1, args.runs + 1):
        row = {"run": number, "structural_checks": "not_run",
               "semantic_review": {"status": "not_reviewed", "checks": bundle.get("semantic_review", {}).get("checks", CHECKS)}}
        try:
            raw = model.suggest(evidence)
            row["raw_output"] = raw
            try:
                if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_OUTPUT_BYTES:
                    raise ValueError("输出类型或大小不符合主流程限制。")
                suggestion = validate_agent_suggestion(raw, evidence)
                row.update(structural_checks="passed", suggestion=suggestion.model_dump(mode="json"))
            except Exception as error:
                row.update(structural_checks="failed", validation_error=type(error).__name__)
        except Exception as error:
            row.update(request_error_details(error))
        manifest["runs"].append(row)
        save_result(manifest, args.output_dir, "r1-after" if bundle.get("kind") == "record-scope-baseline-v1" else "w1-after")
        print(f"{number}/{args.runs}: {row['structural_checks']}; semantic_review=not_reviewed")
        if "request_error" in row:
            print("请求失败，停止后续调用；已保存失败记录，不自动重试。")
            break


REVIEW_SECTIONS = {
    "observations": "事实摘要", "candidate_assessments": "初步判断",
    "uncertainties": "待核实信息", "verification_items": "建议核查事项",
}
REVIEW_LABELS = {
    "not_reviewed": "未评审", "supported": "支持对应内容", "not_supported": "不支持对应内容",
    "complete": "正文事实引用充分", "incomplete": "正文仍有事实缺少充分引用",
    "needs_clarification": "有歧义，需复核", "not_applicable": "不适用（建议动作，无引用字段）",
    "needs_revision": "需修正", "reviewed_with_limitations": "已评审，保留限制",
}


def review_template(bundle):
    """Align saved rows and refs only. All semantic decisions start unreviewed."""
    if digest(bundle["input"]) != bundle["input_sha256"]:
        raise ValueError("输入摘要不一致，停止导出。")
    entries = []
    for run in bundle["runs"]:
        for section in REVIEW_SECTIONS:
            for index, row in enumerate((run.get("suggestion") or {}).get(section, [])):
                text, refs = (row, []) if section == "verification_items" else (row["text"], row["evidence_refs"])
                entries.append({
                    "run": run["run"], "section": section, "index": index, "text": text,
                    "evidence_refs": list(refs), "cited_source_support": "not_reviewed",
                    "cited_sources": [{"ref": ref, "support": "not_reviewed", "note": ""} for ref in refs],
                    "fact_coverage": "not_reviewed", "scope_review": "not_reviewed",
                    "note": "", "other_records_to_check": [],
                })
    return {
        "kind": "offline-citation-review-v1", "evaluation_only": True,
        "replay_sha256": digest(bundle), "input_sha256": bundle["input_sha256"],
        "reviewer": "尚未填写；此文件不是产品中的正式审核或审计记录。",
        "entries": entries,
        "run_reviews": [{"run": run["run"], "structural_checks": run["structural_checks"],
                         "content_review": "not_reviewed", "note": ""} for run in bundle["runs"]],
        "limitations": ["编号存在不代表引用支持正文；引用支持部分内容不代表所有事实都有充分引用。",
                        "不自动补引用或修改原结果，不写产品数据库，不提供产品人工修正或审计能力。"],
    }


def citation_review_html(bundle, annotations=None):
    """Render all saved claims, cited originals, and every other snapshot record."""
    template = review_template(bundle)
    review = template if annotations is None else annotations
    if review.get("replay_sha256") != template["replay_sha256"] or review.get("input_sha256") != template["input_sha256"]:
        raise ValueError("评审记录不属于这份输出，停止导出。")
    identity = ("run", "section", "index", "text", "evidence_refs")
    if len(review["entries"]) != len(template["entries"]) or any(
        any(row.get(key) != expected[key] for key in identity)
        or [source.get("ref") for source in row["cited_sources"]] != expected["evidence_refs"]
        for row, expected in zip(review["entries"], template["entries"])
    ):
        raise ValueError("评审条目、正文或引用与原输出不一致，停止导出。")
    if [r["run"] for r in review["run_reviews"]] != [r["run"] for r in template["run_reviews"]] or any(
        r["structural_checks"] != saved["structural_checks"]
        for r, saved in zip(review["run_reviews"], template["run_reviews"])
    ):
        raise ValueError("评审不能改写原程序校验结果。")
    records = bundle["input"]["records"]
    anchors = {ref: f"record-{index}" for index, ref in enumerate(records)}
    def h(value):
        return escape(str(value), quote=True)
    def pretty(value):
        return h(json.dumps(value, ensure_ascii=False, indent=2))
    def label(value):
        return h(REVIEW_LABELS.get(value, value))
    def link(ref):
        if ref not in records:
            return f'<strong class="missing">{h(ref)}：本快照不存在，未自动修补</strong>'
        return f'<a href="#{anchors[ref]}">{h(ref)}</a>'
    parts = ['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>离线引用对照评审</title>',
             '<style>body{max-width:1100px;margin:32px auto;padding:0 20px;font:16px/1.65 system-ui;color:#203044}a{color:#245780}article,details{border:1px solid #ccd6df;padding:12px;margin:14px 0}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f6f8;padding:12px}h2{margin-top:36px}.missing{color:#a12622}li{overflow-wrap:anywhere}</style>',
             '<h1>离线引用对照评审</h1><p>仅用于项目评估，非产品人工修正、保存或审计功能。原输出及引用保持不变；本页不调用模型或业务数据库。</p>',
             '<p>分开检查：①已列引用是否支持对应内容；②正文各项事实是否都有充分引用。两者不能互相代替。未评审不等于通过。</p>',
             f'<p>基线运行：{h(bundle.get("baseline_run_id", "未记录"))}；模型：{h(bundle.get("model_name", "未记录"))}；输入摘要：{h(bundle["input_sha256"])}</p>',
             f'<p>审阅说明：{h(review["reviewer"])}</p><p><a href="#all-records">查看同一快照全部记录与原文索引</a></p>']
    for summary, saved in zip(review["run_reviews"], bundle["runs"]):
        number = saved["run"]
        parts += [f'<h2>第{h(number)}次输出</h2>',
                  f'<p>程序校验（原值）：{h(saved["structural_checks"])}；原语义状态：{h(saved.get("semantic_review", {}).get("status", "未记录"))}；本次对照评审：{label(summary["content_review"])}</p>',
                  f'<p>{h(summary["note"])}</p>']
        if not saved.get("suggestion"):
            parts.append(f'<p>本次没有通过结构校验的建议，不补造条目。保存的输出或失败信息：</p><pre>{pretty(saved)}</pre>')
        for row in review["entries"]:
            if row["run"] != number:
                continue
            parts += [f'<article data-review-row="{h(number)}-{h(row["section"])}-{row["index"]}">',
                      f'<h3>{h(REVIEW_SECTIONS[row["section"]])} {row["index"] + 1}</h3><p>{h(row["text"])}</p>',
                      '<p>本条原引用与对应材料：</p><ul>']
            for source in row["cited_sources"]:
                ref = source["ref"]
                parts.append(f'<li>{link(ref)}；来源类型：{h(ref.split(":", 1)[0])}；对应支持：{label(source["support"])} {h(source["note"])}')
                if ref in records:
                    record = records[ref]
                    original = record.get("description", record.get("requirements", record.get("note")))
                    parts.append(f'<pre>{h(original)}</pre>' if original is not None else '<p>此项为查询或上下文记录，完整字段见索引。</p>')
                parts.append('</li>')
            parts += ['</ul>', '<p>本节为建议动作，原格式没有引用字段。</p>' if row["section"] == "verification_items" else '',
                      f'<p>已列引用支持对应内容：{label(row["cited_source_support"])}；正文事实引用覆盖：{label(row["fact_coverage"])}；范围表达：{label(row["scope_review"])}</p>',
                      f'<p>评审说明：{h(row["note"])}</p>']
            if row["other_records_to_check"]:
                parts.append('<p>评审时发现的其他材料（未加入原引用）：' + '、'.join(link(ref) for ref in row["other_records_to_check"]) + '</p>')
            parts += ['<p><a href="#all-records">查看本快照其他材料，检查是否漏引</a></p></article>']
    parts += [f'<h2 id="all-records">同一证据快照的全部记录（{len(records)}项）</h2>',
              '<p>以下是输入材料索引，不代表每项都支持上面的判断，也不会自动加入模型引用。业务原文、查询限制和地点信息均完整保留。</p><ul>']
    parts += [f'<li>{link(ref)}</li>' for ref in records]
    parts.append('</ul>')
    for ref, record in records.items():
        parts.append(f'<details id="{anchors[ref]}"><summary>{h(ref)}</summary><pre>{pretty(record)}</pre></details>')
    parts.append('<h2>限制</h2><ul>' + ''.join(f'<li>{h(note)}</li>' for note in review["limitations"]) + '</ul>')
    parts.append('<script>document.addEventListener("click",event=>{const a=event.target.closest("a");if(a&&a.hash.startsWith("#record-")){const target=document.getElementById(a.hash.slice(1));if(target)target.open=true;}});</script></html>')
    return "\n".join(parts)


def export_review(args):
    bundle = json.loads(args.input.read_text(encoding="utf-8"))
    annotations = json.loads(args.annotations.read_text(encoding="utf-8")) if args.annotations else None
    document = citation_review_html(bundle, annotations)
    # Create new sidecars only. Never overwrite the input, an annotation, or a prior export.
    args.output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    html_path = args.output_dir / f"citation-review-{stamp}.html"
    with html_path.open("x", encoding="utf-8") as output:
        output.write(document)
    print(html_path.resolve())
    if annotations is None:
        path = args.output_dir / f"citation-review-{stamp}.json"
        with path.open("x", encoding="utf-8") as output:
            json.dump(review_template(bundle), output, ensure_ascii=False, indent=2)
        print(path.resolve())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="action", required=True)
    default_dir = ROOT.parent / "property-v2-evaluations"
    capture_parser = subs.add_parser("capture", help="仅GET并保存旧结果，不调用Gemini")
    capture_parser.add_argument("--report-id", type=UUID, required=True)
    capture_parser.add_argument("--run-id", type=UUID, required=True)
    capture_parser.add_argument("--output-dir", type=Path, default=default_dir)
    replay_parser = subs.add_parser("replay", help="用冻结快照做少量真实模型复测，不写业务数据库")
    replay_parser.add_argument("--input", type=Path, required=True)
    replay_parser.add_argument("--runs", type=int, choices=(1, 2, 3), default=2)
    replay_parser.add_argument("--output-dir", type=Path, default=default_dir)
    review_parser = subs.add_parser("review", help="仅本地导出逐条引用和全部快照材料，不调用模型或数据库")
    review_parser.add_argument("--input", type=Path, required=True)
    review_parser.add_argument("--annotations", type=Path, help="与该输出绑定的独立评审记录")
    review_parser.add_argument("--output-dir", type=Path, default=default_dir)
    args = parser.parse_args()
    try:
        {"capture": capture, "replay": replay, "review": export_review}[args.action](args)
    except (ValueError, OSError, KeyError) as error:
        parser.exit(1, f"{type(error).__name__}: {error}\n")


if __name__ == "__main__":
    main()
