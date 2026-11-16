"""One saved incident, phrasing boundaries and API regressions; no live model."""
import copy
import json
import unittest
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_evidence import AgentEvidence, SourceStateConflictError, validate_agent_suggestion
from automatic_agent_evidence import read_automatic_agent_evidence
from database import get_db
from models import AgentRun
from routers.agent_runs import get_agent_model, router
from source_state_rules import find_source_state_conflicts
from tests.automatic_evidence_fixture import AutomaticEvidenceFixture, at
from tests.automatic_suggestion_fixture import automatic_draft


BASELINE = Path(__file__).resolve().parents[1] / 'docs/fixtures/information-board-saved-runs-20261116.json'
BAD = '起始报事观察时间未注明，且没有查询到相关候选计划或历史工单记录，无法确认当前信息栏字迹实际状态及此前是否有过处理。'
BAD_FORMS = (
    BAD, '未查到维修工单。', '本次没有查询到历史工单记录。',
    '目前尚未找到任何工单。', '没有检索到候选计划及维修工单。',
    '维修工单未查到。', '维修工单：未查到记录。', '维修工单查询为空。',
    '维修工单检索结果为空。', '本次维修工单0条。', '维修工单：0条。',
    '且没查到工单。', '经查询未查到维修工单。',
)
GOOD_FORMS = (
    '维修工单尚未接入，无法确认此前处理情况。',
    '本次未查到候选计划；维修工单尚未接入。',
    '未查到相关候选计划，维修工单尚未接入，无法判断此前是否处理。',
    '设备台账和维修工单尚未接入，无法确认故障原因。',
    '不能把尚未接入说成没查到工单。',
    '维修工单尚未接入，不能说未查到维修工单。',
    '不能说没有查询到候选计划或历史工单记录。',
    '没有查到计划不等于没有开展工作。',
    '未查到维修工单这一说法没有依据。',
    '并非未查到维修工单，而是未接入。',
    '如果未查到维修工单，请先核对系统接入情况。',
    '若未查到维修工单，也不能认定没有开展维修。',
    '是否未查到维修工单，需要询问工作人员。',
    '未查到维修工单吗？',
    '“未查到维修工单”是错误表述。',
    '居民称未查到维修工单，系统尚未接入此来源。',
    '执行记录载明“未查到维修工单”，仍需核实。',
    '请核对历史工单及现场字迹。',
    '尚未查询执行和现场检查，无法据此判断没有开展工作。',
    '维修工单0条这一结论不能成立。',
    '维修工单尚未接入，未查到的是工作计划。',
    '未查到工作计划或相关报事；不能判断工单有无。',
    '不能这样表述，未查到维修工单。',
    '例如，未查到维修工单。',
    '居民称未查到计划，未查到维修工单。',
    '“未查到计划，未查到维修工单”不应作为结论。',
    '如果来源已接入，未查到维修工单。',
    '记录载明没有查到计划，未查到维修工单。',
    '不应断言，维修工单0条。',
    '「未查到通知；未查到维修工单。」这段原文需要复核。',
)
# Deliberate abstentions, NOT semantic passes. Documented coverage limitations.
OUTSIDE_GRAMMAR = (
    '系统里压根没有历史维修痕迹。',
    '工单：没有记录。',
    '已查询维修工单，结果为空。',
    'We found no work orders.',
    '由于没有查到维修工单，需要现场核查。',
)


class SourceStateRuleTests(unittest.TestCase):
    def setUp(self):
        self.saved = json.loads(BASELINE.read_text(encoding='utf-8'))[0]
        self.records = self.saved['evidence']['records']

    def test_direct_bad_forms_are_detected(self):
        for text in BAD_FORMS:
            with self.subTest(text=text):
                conflicts = find_source_state_conflicts(text, self.records)
                self.assertEqual({x.source_ref for x in conflicts}, {'search:work_orders'})

    def test_correct_negated_conditional_quoted_and_mixed_forms_are_not_rejected(self):
        for text in GOOD_FORMS:
            with self.subTest(text=text):
                self.assertEqual(find_source_state_conflicts(text, self.records), [])

    def test_unavailable_states_are_checked_but_found_and_empty_are_not_guessed(self):
        for status in ('unsupported', 'not_queried', 'failed', 'found', 'empty'):
            with self.subTest(status=status):
                records = copy.deepcopy(self.records)
                records['search:work_orders']['status'] = status
                result = find_source_state_conflicts('未查到维修工单。', records)
                self.assertEqual(bool(result), status in ('unsupported', 'not_queried', 'failed'))

    def test_mixed_source_noun_list_checks_each_status_independently(self):
        records = copy.deepcopy(self.records)
        records['search:notices']['status'] = 'empty'
        result = find_source_state_conflicts('未查到通知或历史工单记录。', records)
        self.assertEqual([row.source_ref for row in result], ['search:work_orders'])
        records['search:work_orders']['status'] = 'empty'
        self.assertEqual(find_source_state_conflicts(BAD, records), [])

    def test_supported_source_types_not_just_work_orders(self):
        for text, source in [('未查到设备台账。', 'assets'), ('未查到管网资料。', 'assets'),
                             ('通知0条。', 'notices'), ('未查到执行记录或现场检查记录。', 'plan_records')]:
            with self.subTest(text=text):
                result = find_source_state_conflicts(text, self.records)
                self.assertEqual({row.source_ref for row in result}, {'search:' + source})

    def test_unknown_and_per_plan_scopes_are_left_to_review(self):
        for status in (None, 'unknown', {'status': 'unsupported'}):
            records = {'search:work_orders': {'status': status}}
            self.assertEqual(find_source_state_conflicts('未查到维修工单。', records), [])
        scoped = {'search:work_orders:plan-1': {'status': 'not_queried'}}
        self.assertEqual(find_source_state_conflicts('未查到维修工单。', scoped), [])

    def test_other_wording_is_explicitly_outside_coverage(self):
        for text in OUTSIDE_GRAMMAR:
            with self.subTest(not_a_semantic_pass=text):
                self.assertEqual(find_source_state_conflicts(text, self.records), [])

    def test_check_applies_to_all_output_sections_without_mutating_them(self):
        for section in ('observations', 'candidate_assessments', 'uncertainties', 'verification_items'):
            with self.subTest(section=section):
                evidence = copy.deepcopy(self.saved['evidence'])
                draft = copy.deepcopy(self.saved['suggestion'])
                draft['uncertainties'][0]['text'] = '维修工单尚未接入，无法确认历史情况。'
                if section == 'candidate_assessments':
                    candidate = 'plan:' + str(uuid4())
                    evidence['records'][candidate] = {'title': '合成候选'}
                    evidence['records']['context:investigation']['candidate_refs'] = [candidate]
                    draft[section] = [{'text': BAD, 'evidence_refs': [evidence['report_ref'], candidate],
                                       'candidate_ref': candidate, 'disposition': 'insufficient_information'}]
                elif section == 'verification_items': draft[section] = [BAD]
                else: draft[section][0]['text'] = BAD
                before = copy.deepcopy((evidence, draft))
                with self.assertRaises(SourceStateConflictError):
                    validate_agent_suggestion(json.dumps(draft), AgentEvidence.model_validate(evidence))
                self.assertEqual(before, (evidence, draft))

    def test_corrected_illustration_passes_without_repairing_original(self):
        draft = copy.deepcopy(self.saved['suggestion'])
        draft['uncertainties'][0] = {
            'text': '观察时间未注明；未查到候选计划；维修工单尚未接入，无法确认此前处理情况。',
            'evidence_refs': [self.saved['evidence']['report_ref'], 'search:plans', 'search:work_orders']}
        parsed = validate_agent_suggestion(json.dumps(draft), AgentEvidence.model_validate(self.saved['evidence']))
        self.assertEqual(parsed.model_dump(mode='json'), draft)
        self.assertEqual(self.saved['suggestion']['uncertainties'][0]['text'], BAD)


class SourceStateAPITests(AutomaticEvidenceFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.model = Mock(model_name='offline-source-state-test')
        def suggest(evidence):
            draft = automatic_draft(evidence)
            draft['uncertainties'] = [{'text': '本次未查到历史工单记录。', 'evidence_refs': ['search:plans']}]
            return json.dumps(draft)
        self.model.suggest.side_effect = suggest
        def get_session():
            with self.sessions() as db: yield db
        app = FastAPI(); app.include_router(router)
        app.dependency_overrides[get_db] = get_session
        app.dependency_overrides[get_agent_model] = lambda: self.model
        self.client = TestClient(app); self.addCleanup(self.client.close)
        self.url = f'/api/service-requests/{self.report_id}/agent-runs'

    def business_rows(self):
        return {k: v for k, v in self.snapshot().items() if k != 'agent_runs'}

    def test_new_conflict_saves_validation_failure_and_same_id_does_not_retry(self):
        before = self.business_rows(); request_id = str(uuid4())
        response = self.client.post(self.url, json={'request_id': request_id})
        self.assertEqual(response.status_code, 201, response.text)
        saved = response.json()
        self.assertEqual(saved['status'], 'failed')
        self.assertEqual(saved['failure_stage'], 'validation')
        self.assertEqual(saved['error_code'], 'SourceStateConflictError')
        self.assertEqual(saved['evidence']['records']['search:work_orders']['status'], 'unsupported')
        self.assertIsNone(saved['suggestion'])  # Existing failure storage contract.
        repeated = self.client.post(self.url, json={'request_id': request_id})
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(repeated.json(), saved)
        self.model.suggest.assert_called_once()
        self.assertEqual(self.client.get(self.url).json()[0], saved)
        self.assertEqual(self.business_rows(), before)

    def test_old_saved_suggestion_reads_back_unchanged(self):
        with self.sessions() as db:
            evidence = read_automatic_agent_evidence(db, self.report_id).model_dump(mode='json')
        draft = automatic_draft(AgentEvidence.model_validate(evidence))
        draft['uncertainties'] = [{'text': '未查到维修工单。', 'evidence_refs': ['search:plans']}]
        run_id = uuid4()
        with self.sessions.begin() as db:
            db.add(AgentRun(id=run_id, report_id=self.report_id, status='pending_review',
                model_name='historical-model', created_at=at('10:00'), finished_at=at('10:01'),
                evidence=evidence, suggestion=draft))
        before = self.snapshot()
        result = self.client.get(self.url)
        self.assertEqual(result.status_code, 200, result.text)
        saved = result.json()[0]
        self.assertEqual(saved['id'], str(run_id))
        self.assertEqual(saved['status'], 'pending_review')
        self.assertEqual(saved['suggestion'], draft)
        self.assertEqual(saved['evidence'], evidence)
        self.assertEqual(before, self.snapshot())
        self.model.suggest.assert_not_called()
