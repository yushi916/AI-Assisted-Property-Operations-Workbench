"""One frozen H1/H2 evaluation round; prepare is offline, run is capped at four calls."""
import argparse
import importlib.metadata
import json
import os
import tempfile
from html import escape
from datetime import datetime, UTC
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5
from unittest.mock import patch

from alembic import command
from alembic.config import Config

from sqlalchemy import event
from sqlalchemy.orm import sessionmaker
from agent_evidence import AgentEvidence, validate_agent_suggestion
from agent_runner import MAX_EVIDENCE_BYTES, MAX_OUTPUT_BYTES
from automatic_evidence import read_automatic_evidence
from automatic_agent_evidence import build_automatic_agent_evidence
from database import Base, create_db_engine, engine as business_engine
from evaluate_water_faithfulness import digest, request_error_details, citation_review_html, review_template
from execution_schemas import ExecutionCreate
from inspection_schemas import InspectionCreate
from plan_schemas import PlanCreate
from schemas import ServiceRequestCreate
from models import ServiceRequest, PlanInstance, PlanLocation, ExecutionRecord, ExecutionLocation, InspectionRecord
from seed_demo_location_hierarchy import seed_demo_location_hierarchy
from seed_demo_floor_locations import seed_demo_floor_locations
from gemini_agent import AUTOMATIC_INSTRUCTIONS, AUTOMATIC_PROMPT_VERSION, AUTOMATIC_OUTPUT_VERSION, automatic_response_schema, GeminiEvidenceModel

ROOT = Path(__file__).resolve().parent
ROUND = 'challenge-20261116-v1'
MODEL = 'gemini-3.5-flash-lite'
DIMENSIONS = ['事实范围与冷热水/部位及局部正常', '实际时间与先后关系', '记录存在与事实覆盖', '主体及单次观察范围', '状态与原因', '引用支持', '事实引用覆盖', '建议针对性']
SOURCE_FILES = ['gemini_agent.py', 'agent_evidence.py', 'agent_runner.py', 'automatic_evidence.py', 'automatic_agent_evidence.py', 'evidence_rules.py', 'evaluate_challenge_round.py']


def at(day, clock):
    return datetime.fromisoformat(f'{day}T{clock}:00+08:00')


def uid(case, kind):
    return uuid5(NAMESPACE_URL, f'property-v2/{ROUND}/{case}/{kind}')


def write_new(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def source_hashes():
    import hashlib
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCE_FILES}


def cases():
    return {
        'H1': dict(day='2026-10-14', point='LOC-A-B01-U01-R302', category='water_supply',
            report='早上九点十分左右，我用卫生间洗手盆时，热水那边只有很小的一股水，冷水这边出水正常。家里其他龙头还没试，邻居家什么情况我也不清楚，麻烦来看看。',
            title='302室卫生间洗手盆出水情况核查',
            requirements='核对302室卫生间洗手盆冷水侧和热水侧的出水情况，记录现场所见及实际检查范围。',
            start='09:30', end='10:00', work_start='09:35', finish='09:45', observed='09:50',
            execution='已到302室查看卫生间洗手盆，冷水侧打开后出水正常，随后关好龙头。',
            inspection='这次查看了卫生间洗手盆的冷水侧，打开后出水正常。热水侧本次没有核实。',
            report_relation='before', inspection_relation='after'),
        'H2': dict(day='2026-10-15', point='LOC-A-LOBBY', category='cleaning',
            report='九点十分经过大堂电梯前，地上有一摊像饮料留下的污渍，踩着有些发黏，麻烦来清理一下。什么时候弄上的、是谁弄的，我都不知道。',
            title='A区大堂入口至电梯前地面清洁',
            requirements='清理大堂入口至电梯前地面的垃圾、污渍及水迹，清洁后确认通行情况。',
            start='08:30', end='09:00', work_start='08:35', finish='08:55', observed='09:25',
            execution='入口到电梯前的地面已经清洁，地上可见的垃圾和污渍已清理，随后收回了清洁工具。',
            inspection='本次只查看了入口地垫周边，地面干燥，没有看见明显污渍。电梯前的区域这次没有查看。',
            report_relation='after', inspection_relation='after'),
    }


def make_evidence(case, spec):
    """Build each case in a separate temporary SQLite database; never connect to business DB."""
    day, point = spec['day'], spec['point']
    with tempfile.TemporaryDirectory(prefix='property-challenge-') as folder, patch.object(business_engine, 'connect', side_effect=AssertionError('禁止连接业务库')):
        engine = create_db_engine(Path(folder) / 'case.db')
        try:
            config = Config(str(ROOT / 'alembic.ini'))
            config.set_main_option('script_location', str(ROOT / 'migrations'))
            with engine.connect() as connection:
                config.attributes['connection'] = connection
                command.upgrade(config, 'head')
            seed_demo_location_hierarchy(engine)
            seed_demo_floor_locations(engine)
            sessions = sessionmaker(bind=engine, expire_on_commit=False)
            report = ServiceRequestCreate(request_id=uid(case, 'report-request'), service_category=spec['category'], description=spec['report'], source='phone', risk_status='unknown', location_id=point, observed_at=at(day, '09:10'))
            plan = PlanCreate(request_id=uid(case, 'plan-request'), title=spec['title'], service_category=spec['category'], plan_kind='special' if case == 'H1' else 'routine', requirements=spec['requirements'], owner_name='陈浩' if case == 'H1' else '周宁', location_ids=[point], planned_start_at=at(day, spec['start']), planned_end_at=at(day, spec['end']))
            execution = ExecutionCreate(request_id=uid(case, 'execution-request'), plan_id=uid(case, 'plan'), executor_name='陈浩' if case == 'H1' else '林悦', description=spec['execution'], completion_claim='completed', location_ids=[point], claimed_started_at=at(day, spec['work_start']), claimed_finished_at=at(day, spec['finish']))
            inspection = InspectionCreate(request_id=uid(case, 'inspection-request'), plan_id=uid(case, 'plan'), inspector_name='许岚' if case == 'H1' else '周宁', description=spec['inspection'], finding='no_issue_observed', location_id=point, observed_at=at(day, spec['observed']))
            with sessions.begin() as db:
                db.add(ServiceRequest(id=uid(case, 'report'), received_at=at(day, '11:00'), version=1, **report.model_dump()))
                db.add(PlanInstance(id=uid(case, 'plan'), created_at=at(day, '08:10'), version=1, **plan.model_dump(exclude={'location_ids'}), locations=[PlanLocation(location_id=point)]))
                db.flush()
                db.add(ExecutionRecord(id=uid(case, 'execution'), recorded_at=at(day, '11:02'), version=1, **execution.model_dump(exclude={'location_ids'}), locations=[ExecutionLocation(location_id=point)]))
                db.add(InspectionRecord(id=uid(case, 'inspection'), recorded_at=at(day, '11:03'), version=1, **inspection.model_dump()))
            def snapshot():
                with engine.connect() as connection:
                    return {name: connection.exec_driver_sql(f'SELECT * FROM "{name}" ORDER BY 1').all() for name in Base.metadata.tables}
            before = snapshot()
            statements = []
            def observe(conn, cursor, statement, parameters, context, many):
                statements.append(statement.lstrip().split(None, 1)[0].upper())
            event.listen(engine, 'before_cursor_execute', observe)
            try:
                with sessions.begin() as db, patch('automatic_evidence.utc_now', return_value=at(day, '11:10')):
                    source = read_automatic_evidence(db, uid(case, 'report'))
                    evidence = build_automatic_agent_evidence(source)
            finally:
                event.remove(engine, 'before_cursor_execute', observe)
            assert before == snapshot(), '取证修改了临时业务数据'
            assert not set(statements) & {'INSERT', 'UPDATE', 'DELETE', 'REPLACE'}, statements
            assert not source.related_reports and not source.manual_links and len(source.plans) == 1
            p = source.plans[0]
            assert p.plan.id == uid(case, 'plan') and p.automatic and p.plan_location_matches
            assert len(p.executions) == len(p.inspections) == 1
            e, i = p.executions[0], p.inspections[0]
            assert e.record.id == uid(case, 'execution') and i.record.id == uid(case, 'inspection')
            assert e.location_relation == i.location_relation == 'same_location'
            assert e.report_relation_to_completion == spec['report_relation']
            assert i.observation_relation_to_report == spec['inspection_relation']
            assert len(p.plan_checks.coverage) == 1
            cover = p.plan_checks.coverage[0]
            assert cover.execution_ids == [e.record.id] and cover.inspection_ids == [i.record.id]
            assert not p.plan_checks.findings
            assert p.plan_checks.comparisons[0].relation == 'after'
            expected = {f'{kind}:{uid(case, kind)}' for kind in ('report', 'plan', 'execution', 'inspection')}
            actual = {key for key in evidence.records if key.split(':', 1)[0] in ('report', 'plan', 'execution', 'inspection')}
            assert actual == expected
            for kind, text in [('report', spec['report']), ('execution', spec['execution']), ('inspection', spec['inspection'])]:
                assert evidence.records[f'{kind}:{uid(case, kind)}']['description'] == text
            assert evidence.records[f'plan:{uid(case, "plan")}']['requirements'] == spec['requirements']
            statuses = {q.source: q.status for q in source.queries}
            assert statuses['related_reports'] == 'empty'
            assert all(statuses[k] == 'unsupported' for k in ('assets', 'work_orders', 'notices'))
            assert len(evidence.model_dump_json().encode()) <= MAX_EVIDENCE_BYTES
            return evidence, dict(status='passed', business_records=4, expected_refs=sorted(expected), coverage={'execution': 1, 'inspection': 1}, report_relation_to_completion=spec['report_relation'], observation_relation_to_report=spec['inspection_relation'], same_location=True, source_statuses=statuses, business_data_unchanged=True, business_database_connected=False)
        finally:
            engine.dispose()


def rubric():
    return dict(kind='challenge-rubric-v1', dimensions=DIMENSIONS,
        statuses=['pass', 'fail', 'unclear'], assistant_review='not_reviewed', author_confirmation='not_reviewed',
        common=['程序、结构、内容分开；不逐字匹配或固定候选枚举。', '逐条引用支持与正文事实覆盖分开；不借用其它条目的引用，不自动补引用。', '已有局部正常必须承认；未知不能补造。行动无引用字段不机械判漏引，但预设事实仍审。'],
        cases={
            'H1': {'essential': ['09:10居民报告洗手盆热水侧水流小、冷水侧正常，其他点和邻居仍未知。', '09:50检查只确认本次冷水侧正常、热水侧未核实。', '执行与检查存在各1条；热水侧事实覆盖不足不是缺记录。'], 'forbidden': ['冷水停水、全屋无水、热水已恢复', '把执行正文未提热水写成执行人确定没查，或所有人未查', '诊断设备、共用管网或责任'], 'action': '核实报事热水侧及是否有未记录的对应检查情况，不能只重复检查已正常的冷水侧。'},
            'H2': {'essential': ['08:55执行人填报完成，09:10居民电梯前发黏，09:25检查只看入口。', '入口较晚局部正常不能确认电梯前情况。', '同一大堂ID不抹去原文部位范围。'], 'forbidden': ['电梯前已干净、整个大堂正常', '09:25检查早于居民09:10观察', '确定未清洁、虚报、复发、解决或指定污染原因'], 'action': '优先电梯前位置及08:55之后的变化，不用重复检查入口代替。'},
        })


def prepare(directory):
    if directory.exists():
        raise ValueError('冻结目录已存在，不覆盖或重新生成本轮材料。')
    directory.mkdir(parents=True)
    sealed = []
    for case, spec in cases().items():
        evidence, preflight = make_evidence(case, spec)
        bundle = {'case_id': case, 'input': evidence.model_dump(mode='json'), 'input_sha256': digest(evidence.model_dump(mode='json')), 'preflight': preflight, 'response_json_schema': automatic_response_schema(evidence)}
        write_new(directory / f'{case}.json', bundle)
        sealed.append({'case_id': case, 'file': f'{case}.json', 'sha256': digest(bundle), 'input_sha256': bundle['input_sha256']})
    criteria = rubric(); write_new(directory / 'rubric.json', criteria)
    manifest = dict(kind='challenge-round-v1', round_id=ROUND, model_name=MODEL, cases=sealed, trials_per_case=2, maximum_requests=4, rubric_sha256=digest(criteria),
        system_instruction=AUTOMATIC_INSTRUCTIONS, prompt_sha256=digest(AUTOMATIC_INSTRUCTIONS), prompt_version=AUTOMATIC_PROMPT_VERSION, output_schema_version=AUTOMATIC_OUTPUT_VERSION,
        sdk_version=importlib.metadata.version('google-genai'), source_sha256=source_hashes(),
        generation_config={'max_output_tokens': 4096, 'timeout_ms': 20000, 'retry_attempts': 1, 'automatic_function_calling': False, 'temperature': 'not explicitly set; SDK/server default'},
        pricing={'checked_date': '2026-11-16', 'url': 'https://ai.google.dev/gemini-api/docs/pricing/', 'input_usd_per_million': .30, 'output_usd_per_million': 2.50, 'budget_usd': 1.0})
    estimates = []
    for c in sealed:
        b = json.loads((directory / c['file']).read_text(encoding='utf-8'))
        # Local UTF-8 byte estimate with reserve, not an exact tokenizer or billing cap.
        estimate = len((AUTOMATIC_INSTRUCTIONS + AgentEvidence.model_validate(b['input']).model_dump_json() + json.dumps(b['response_json_schema'], ensure_ascii=False)).encode()) + 4096
        estimates.append({'case_id': c['case_id'], 'estimated_input_token_allowance_per_call': estimate})
    manifest['cost_estimate'] = {'method': 'UTF-8 bytes of prompt/input/schema plus 4096-token reserve per call; not exact token counts', 'per_case': estimates,
        'four_calls_usd': round(2 * sum(x['estimated_input_token_allowance_per_call'] for x in estimates) * .30 / 1_000_000 + 4 * 4096 * 2.50 / 1_000_000, 6)}
    write_new(directory / 'manifest.json', manifest)
    write_new(directory / 'seal.json', {'manifest_sha256': digest(manifest)})
    return manifest


def verify(directory):
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    if digest(manifest) != json.loads((directory / 'seal.json').read_text(encoding='utf-8'))['manifest_sha256']:
        raise ValueError('manifest已变化')
    if manifest['source_sha256'] != source_hashes() or manifest['sdk_version'] != importlib.metadata.version('google-genai'):
        raise ValueError('代码或SDK与冻结配置不一致，未调用模型。')
    if manifest['system_instruction'] != AUTOMATIC_INSTRUCTIONS or manifest['maximum_requests'] != 4 or manifest['trials_per_case'] != 2:
        raise ValueError('本轮配置不一致')
    criteria = json.loads((directory / 'rubric.json').read_text(encoding='utf-8'))
    if digest(criteria) != manifest['rubric_sha256']: raise ValueError('评审标准已变化')
    bundles = []
    for entry in manifest['cases']:
        b = json.loads((directory / entry['file']).read_text(encoding='utf-8'))
        ev = AgentEvidence.model_validate(b['input'])
        if digest(b) != entry['sha256'] or digest(b['input']) != entry['input_sha256'] or automatic_response_schema(ev) != b['response_json_schema']:
            raise ValueError('冻结输入或schema已变化')
        bundles.append(b)
    return manifest, criteria, bundles


def invoke(evidence, output):
    """Observe public SDK output before production adapter rejects e.g. truncation."""
    from google.genai.models import Models
    original = Models.generate_content
    def observed(client, *args, **kwargs):
        response = original(client, *args, **kwargs)
        write_new(output, {'text': response.text, 'finish_reasons': [str(x.finish_reason) for x in (response.candidates or [])],
            'usage_metadata': response.usage_metadata.model_dump(mode='json') if response.usage_metadata else None})
        return response
    with patch.object(Models, 'generate_content', observed):
        return GeminiEvidenceModel().suggest(evidence)


def export_results(directory, manifest, criteria, bundles):
    for b in bundles:
        case = b['case_id']; rows = []
        for trial in (1, 2):
            path = directory / f'{case}-trial-{trial}.json'
            if path.exists(): rows.append(json.loads(path.read_text(encoding='utf-8')))
        if not rows: continue
        bundle = {'kind': 'challenge-replay-v1', 'round_id': ROUND, 'model_name': manifest['model_name'], 'prompt_version': manifest['prompt_version'],
            'input': b['input'], 'input_sha256': b['input_sha256'], 'runs': rows}
        # Derived reports may be exported once only; immutable trial files are authoritative.
        review = review_template(bundle)
        review.update(case_id=case, rubric_sha256=digest(criteria), criteria=criteria['cases'][case],
            author_confirmation='not_reviewed', assistant_review='not_reviewed',
            dimension_reviews=[{'run': row['run'], 'dimensions': [
                {'dimension': name, 'assistant_review': 'not_reviewed', 'author_confirmation': 'not_reviewed', 'note': ''}
                for name in criteria['dimensions']]} for row in rows])
        for path, value in [(directory / f'{case}-results.json', bundle), (directory / f'{case}-review.json', review)]:
            if not path.exists(): write_new(path, value)
        html = directory / f'{case}-review.html'
        if not html.exists():
            heading = '<h1>离线引用对照评审</h1>'
            criteria_html = '<h2>' + case + ' 冻结评审标准</h2><pre>' + escape(json.dumps(criteria['cases'][case], ensure_ascii=False, indent=2)) + '</pre>'
            html.write_text(citation_review_html(bundle).replace(heading, heading + criteria_html, 1), encoding='utf-8')


def run_round(bundle_dir, directory, invoke_fn=invoke):
    manifest, criteria, bundles = verify(bundle_dir)
    if not os.environ.get('GEMINI_API_KEY', '').strip(): raise ValueError('当前终端未配置GEMINI_API_KEY，未发请求。请在已配置密钥的本机终端执行，不要发送密钥。')
    if GeminiEvidenceModel().model_name != manifest['model_name']: raise ValueError('请设置GEMINI_MODEL为冻结模型，未发请求。')
    if manifest['cost_estimate']['four_calls_usd'] > manifest['pricing']['budget_usd']: raise ValueError('本轮估算超预算，未发请求')
    directory.mkdir(parents=True, exist_ok=True)
    journal = directory / 'round-state.json'
    state = {'round_id': ROUND, 'manifest_sha256': digest(manifest), 'status': 'started', 'attempted': [], 'author_confirmation': 'not_reviewed'}
    write_new(journal, state)  # Exclusive creation blocks repeat invocation and concurrent runs.
    print('四次估算费用（美元，非账单上限）：', manifest['cost_estimate']['four_calls_usd'])
    for b in bundles:
        for trial in (1, 2):
            case = b['case_id']; slot = f'{case}-{trial}'
            intent = directory / f'{slot}-started.json'
            write_new(intent, {'slot': slot, 'input_sha256': b['input_sha256'], 'started_at': datetime.now(UTC).isoformat()})
            state['attempted'].append(slot)
            journal.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
            row = {'case_id': case, 'run': trial, 'structural_checks': 'not_run', 'semantic_review': {'status': 'not_reviewed', 'checks': DIMENSIONS}, 'assistant_review': 'not_reviewed', 'author_confirmation': 'not_reviewed'}
            try:
                raw = invoke_fn(AgentEvidence.model_validate(b['input']), directory / f'{slot}-provider.json')
                row['raw_output'] = raw
                try:
                    if not isinstance(raw, str) or len(raw.encode()) > MAX_OUTPUT_BYTES: raise ValueError('输出类型或大小异常')
                    row['suggestion'] = validate_agent_suggestion(raw, AgentEvidence.model_validate(b['input'])).model_dump(mode='json')
                    row['structural_checks'] = 'passed'
                except Exception as error:
                    row.update(structural_checks='failed', validation_error=type(error).__name__)
            except Exception as error:
                row.update(request_error_details(error)); state['status'] = 'stopped_on_request_error'
            write_new(directory / f'{case}-trial-{trial}.json', row)
            print(f'{slot}: {row["structural_checks"]}; 内容/作者确认=not_reviewed')
            journal.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
            if 'request_error' in row:
                export_results(directory, manifest, criteria, bundles)
                return state
    state['status'] = 'finished'; journal.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    export_results(directory, manifest, criteria, bundles)
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['prepare', 'verify', 'run'])
    parser.add_argument('--bundle', type=Path, default=ROOT / 'docs/fixtures/challenge-20261116')
    args = parser.parse_args()
    if args.command == 'prepare':
        value = prepare(args.bundle); print('预检通过并已冻结：', args.bundle); print('四次费用估算USD：', value['cost_estimate']['four_calls_usd'])
    elif args.command == 'verify':
        m, _, bundles = verify(args.bundle); print('冻结校验通过：', [(b['case_id'], b['input_sha256']) for b in bundles]); print('四次费用估算USD：', m['cost_estimate']['four_calls_usd'])
    else:
        out = ROOT.parent / 'property-v2-evaluations' / ROUND
        run_round(args.bundle, out); print('结果目录：', out)


if __name__ == '__main__':
    try: main()
    except (ValueError, FileExistsError) as error:
        raise SystemExit(str(error) + '\n未自动重试；如本轮状态文件已存在，不要删除或换目录继续抽样。')
