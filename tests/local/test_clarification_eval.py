"""Evaluation must not turn valid JSON, missing judgements or stale evidence into success."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from support import SOURCE

spec = importlib.util.spec_from_file_location('clarification_eval_test', SOURCE / 'tools/evaluate-clarification.py')
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.pack = self.root / 'skill/evals'
        for name in ('SKILL.md', 'references/protocol.md', 'references/business-rules.md', 'references/scenarios.md'):
            path = self.pack.parent / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text('Method input\n')
        case = self.pack / 'cases/H01'; inp = case / 'input'; inp.mkdir(parents=True)
        (inp / 'request.md').write_text('只允许死信状态显示按钮。\n')
        (inp / 'context.md').write_text('DEAD=死信，SUCCESS=成功。\n')
        evaluation.write(inp / 'policy.json', {'schema':'factory-local/business-source-policy/1', 'sources':[]})
        evaluation.write(case / 'case.json', {'id':'H01', 'kind':'historical-request-replay'})
        evaluation.write(case / 'oracle.json', {'schema':'factory-clarification/oracle/1', 'case_id':'H01',
            'requirements':[{'id':'R1','criterion':'Only DEAD displays button', 'source_kind':'request'}], 'questions':[]})
        evaluation.write(self.pack / 'manifest.json', {'schema':'factory-clarification/case-pack/1',
            'version':'fixture', 'oracle_status':'curated-proxy-pending-human-review', 'cases':['H01']})
        self.output = self.root / 'run'
        run = evaluation.prepare(self.pack, self.output)
        self.work = Path(run['cases'][0]['workspace'])
        self.candidate = {**evaluation.read(self.work / 'bindings.json'), 'rules':[{
            'id':'CL1','given':'列表有投递状态','when':'渲染按钮','then':'仅 DEAD 显示',
            'forbidden':'其他状态显示','source':{'kind':'request','quote':'只允许死信状态显示按钮。'}}],
            'requirement_coverage':[{'id':'RC1','candidate_id':'RQ-1','resolution':'rule',
                                     'rule_id':'CL1','question_id':None}],
            'open_questions':[], 'assumptions':[], 'source_conflicts':[]}

    def candidate_file(self):
        path = self.work / 'task/clarification-contract.json'; evaluation.write(path, self.candidate); return path

    def judgement(self):
        run = evaluation.read(self.output / 'run.json')
        return {'schema':'factory-local/clarification-judgement/1', 'case_id':'H01', 'reviewer':'independent fixture reviewer',
            'candidate_sha256':evaluation.digest(self.candidate_file()), 'oracle_sha256':run['cases'][0]['oracle_sha256'],
            'requirements':[{'id':'R1','verdict':'covered','rule_ids':['CL1'],'reason':'Only DEAD is explicit'}],
            'decisions':[{'id':'CL1','verdict':'supported','reason':'Original request specifies restriction'}],
            'questions':[], 'assumptions':[]}

    def test_oracle_is_not_in_generator_workspace_and_missing_work_never_passes(self):
        self.assertFalse(any('oracle' in p.name for p in self.work.rglob('*')))
        self.assertEqual(evaluation.score(self.output)['status'], 'incomplete')
        self.candidate_file()
        result = evaluation.score(self.output)
        self.assertEqual(result['status'], 'incomplete')
        self.assertEqual(result['cases'][0]['status'], 'pending-independent-judgement')

    def test_complete_independent_mapping_produces_only_explicit_proxy_result(self):
        evaluation.write(self.output / 'H01/judgement.json', self.judgement())
        result = evaluation.score(self.output)
        self.assertEqual(result['status'], 'proxy-passed')
        self.assertEqual(result['human_oracle_review'], 'NOT_RUN')
        self.assertEqual(result['comparison_with_superpowers'], 'NOT_RUN')

    def test_missing_judgement_rows_stale_bindings_or_unknown_rule_cannot_pass(self):
        original = self.judgement()
        for damage in ('coverage', 'decisions', 'binding', 'unknown-rule'):
            row = copy.deepcopy(original)
            if damage == 'coverage': row['requirements'] = []
            if damage == 'decisions': row['decisions'] = []
            if damage == 'binding': row['candidate_sha256'] = 'outdated'
            if damage == 'unknown-rule': row['requirements'][0]['rule_ids'] = ['invented-id']
            evaluation.write(self.output / 'H01/judgement.json', row)
            self.assertEqual(evaluation.score(self.output)['status'], 'failed', damage)

    def test_unsupported_semantics_unnecessary_question_and_hidden_assumption_are_counted(self):
        self.candidate['open_questions'] = [{'id':'Q1','question':'按钮是否要限制死信状态？','blocking':True}]
        self.candidate['assumptions'] = [{'id':'A1','statement':'可把其他状态也展示','affects_behavior':False}]
        row = self.judgement()
        row['decisions'][0]['verdict'] = 'invented'
        row['questions'] = [{'id':'Q1','verdict':'redundant','gold_ids':[],'reason':'Request already states it'}]
        row['assumptions'] = [{'id':'A1','verdict':'invented','reason':'Changes requested business result'}]
        evaluation.write(self.output / 'H01/judgement.json', row)
        result = evaluation.score(self.output)['cases'][0]
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['unnecessary_questions'], 1)
        self.assertEqual(result['invented_decisions'], 2)

    def test_protected_input_forged_answer_and_changed_oracle_are_rejected(self):
        row = self.judgement(); evaluation.write(self.output / 'H01/judgement.json', row)
        extra = self.work / 'task/clarification-answers.json'
        evaluation.write(extra, {'executor':'model-self-confirmed'})
        self.assertEqual(evaluation.score(self.output)['status'], 'failed'); extra.unlink()
        request = self.work / 'request.md'; original = request.read_bytes()
        request.write_text('Changed original input')
        self.assertEqual(evaluation.score(self.output)['status'], 'failed')
        request.write_bytes(original)
        oracle = self.pack / 'cases/H01/oracle.json'
        oracle.write_text(oracle.read_text() + ' ')
        self.assertEqual(evaluation.score(self.output)['status'], 'failed')

    def test_engineering_assumption_cannot_claim_a_fabricated_confirmation(self):
        self.candidate['assumptions'] = [{'id':'A1','statement':'使用现有命名',
            'affects_behavior':False,'confirmed_by':'FAKE-USER-ANSWER'}]
        row = self.judgement()
        row['assumptions'] = [{'id':'A1','verdict':'engineering','reason':'工程命名可自主决定'}]
        evaluation.write(self.output / 'H01/judgement.json', row)
        result = evaluation.score(self.output)
        self.assertEqual(result['status'], 'failed')
        self.assertIn('不能伪造确认来源', result['cases'][0]['error'])

    def test_unresolved_business_question_needs_no_fabricated_behavior_rule(self):
        case = self.pack / 'cases/H01/oracle.json'
        oracle = evaluation.read(case); oracle['requirements'] = []
        oracle['questions'] = [{'id':'QG','criterion':'What is the approved limit?', 'required':True}]
        evaluation.write(case, oracle)
        import shutil
        shutil.rmtree(self.output)
        run = evaluation.prepare(self.pack, self.output)
        self.candidate = {**evaluation.read(self.work / 'bindings.json'), 'rules':[],
            'requirement_coverage':[{'id':'RC1','candidate_id':'RQ-1','resolution':'question',
                                     'rule_id':None,'question_id':'Q1'}],
            'open_questions':[{'id':'Q1','question':'请确认正式边界口径','blocking':True}],
            'assumptions':[],'source_conflicts':[]}
        row = {'schema':'factory-local/clarification-judgement/1', 'case_id':'H01',
            'reviewer':'independent fixture reviewer','candidate_sha256':evaluation.digest(self.candidate_file()),
            'oracle_sha256':run['cases'][0]['oracle_sha256'], 'requirements':[], 'decisions':[],
            'questions':[{'id':'Q1','verdict':'needed','gold_ids':['QG'],'reason':'缺业务决定，正确停下询问'}],
            'assumptions':[]}
        evaluation.write(self.output / 'H01/judgement.json', row)
        result = evaluation.score(self.output)
        self.assertEqual(result['status'], 'proxy-passed')
        self.assertEqual(result['cases'][0]['protocol_status'], 'pending')
        oracle['questions'] = []; evaluation.write(case, oracle)
        with self.assertRaisesRegex(ValueError, 'Oracle must declare'):
            evaluation.prepare(self.pack, self.root / 'empty-oracle')

    def test_empty_run_and_symlink_workspace_cannot_report_success(self):
        run = evaluation.read(self.output / 'run.json'); saved = copy.deepcopy(run)
        run['cases'] = []
        evaluation.write(self.output / 'run.json', run)
        with self.assertRaises(ValueError): evaluation.score(self.output)
        evaluation.write(self.output / 'run.json', saved)
        outside = self.root / 'outside'
        parent = self.work.parent
        parent.rename(outside); parent.symlink_to(outside, target_is_directory=True)
        self.assertEqual(evaluation.score(self.output)['status'], 'failed')


if __name__ == '__main__':
    unittest.main()
