"""Registered business constraints, frozen source binding and conflict gates."""
import copy
import json

from test_acceptance import FactoryCase, runner
from runtime.local.business_sources import capture, cited_rule, validate_snapshot
from runtime.local.workflow import validate_clarification


class BusinessSourceTests(FactoryCase):
    def normative(self):
        path = self.write('docs/rules.md', '行级启停操作必须使用公共确认执行器。\n')
        self.config['business_sources'] = {'schema': 'factory-local/business-source-policy/1', 'sources': [{
            'id': 'write-policy', 'path': 'docs/rules.md', 'kind': 'normative', 'status': 'active',
            'authority': 'project-maintained', 'reviewed_sha256': runner.digest(path), 'scope_paths': ['src/']}]}
        self.write('.factory/factory-local.json', self.config)
        return path

    def constraint(self):
        data = json.loads((self.task / 'business-sources.snapshot.json').read_text())['sources'][0]
        return {'id': 'CL-P', 'given': '行级写操作', 'when': '启停', 'then': '使用公共确认执行器',
                'forbidden': '无额外约束', 'intent': 'preserve', 'targets': ['src/allowed.ts'],
                'source': {'kind': 'project-rule', 'source_id': data['id'], 'source_sha256': data['sha256'],
                           'source_version': data['version'], 'quote': data['content'].strip()}}

    def add_constraint(self):
        value = json.loads((self.task / 'clarification-contract.json').read_text())
        value['rules'].append(self.constraint())
        runner.write_json(self.task / 'clarification-contract.json', value)
        return value

    def test_registered_normative_constraint_is_citable_and_mapped_to_spec(self):
        self.normative(); self.initialize(); self.add_constraint()
        checked = validate_clarification(self.task, self.request, runner.task_identity(self.task))
        self.assertEqual(checked['status'], 'passed')
        self.assertEqual(checked['schema'], 'factory-local/clarification-result/1')
        current = runner.spec_input(self.task, self.request)
        self.contract.update({k: v for k, v in current.items() if k != 'schema'})
        self.contract['acceptance'][0]['clarification_ids'].append('CL-P')
        runner.write_json(self.task / 'spec-input.json', current)
        runner.write_json(self.task / 'spec-contract.json', self.contract)
        self.assertEqual(runner.validate_spec(self.task, self.request, self.config)['status'], 'passed')

    def test_historical_inactive_and_guidance_cannot_become_business_rules(self):
        self.normative()
        for field, values in [('kind', ['historical', 'record', 'guidance']), ('status', ['reference', 'superseded'])]:
            for value in values:
                with self.subTest(field=field, value=value):
                    policy = copy.deepcopy(self.config['business_sources'])
                    policy['sources'][0][field] = value
                    data = capture(self.root, policy, {})
                    self.assertEqual(data['sources'], [])
                    with self.assertRaises(ValueError):
                        cited_rule({'source_id': 'write-policy'}, {}, {})

    def test_unregistered_stale_or_change_claim_is_rejected(self):
        self.normative(); self.initialize()
        for damage in ('id', 'hash', 'version', 'intent', 'scope'):
            with self.subTest(damage=damage):
                value = self.add_constraint()
                rule = value['rules'][-1]
                if damage == 'id': rule['source']['source_id'] = 'invented'
                if damage == 'hash': rule['source']['source_sha256'] = '0' * 64
                if damage == 'version': rule['source']['source_version'] = 'latest'
                if damage == 'intent': rule['intent'] = 'change'
                if damage == 'scope': rule['targets'] = ['other/domain.ts']
                runner.write_json(self.task / 'clarification-contract.json', value)
                with self.assertRaises(ValueError):
                    validate_clarification(self.task, self.request, runner.task_identity(self.task))
                self.clarify(self.task, self.request)

    def test_source_content_path_authority_and_snapshot_corruption_fail_closed(self):
        path = self.normative(); self.initialize()
        binding = {'workflow_version': runner.WORKFLOW_VERSION, 'task_id': runner.task_identity(self.task),
                   'request_sha256': runner.digest(self.request)}
        frozen = json.loads((self.task / 'business-sources.snapshot.json').read_text())
        frozen['sources'][0]['content'] = 'changed'
        with self.assertRaises(ValueError): validate_snapshot(frozen, binding)
        path.write_text('new rule not reviewed')
        with self.assertRaisesRegex(ValueError, '版本漂移'): runner.business_sources(self.task, self.request, self.config)
        for field, value in [('path', '../outside.md'), ('authority', 'model-approved')]:
            policy = copy.deepcopy(self.config['business_sources']); policy['sources'][0][field] = value
            with self.assertRaises(ValueError): capture(self.root, policy, binding)

    def test_declared_conflict_must_link_blocking_question_and_prevents_spec(self):
        self.normative(); self.initialize()
        value = self.add_constraint()
        value['source_conflicts'] = [{'id': 'SC-1', 'description': '请求与有效规范冲突',
                                      'source_ids': ['write-policy'], 'question_id': 'Q-1'}]
        value['open_questions'] = [{'id': 'Q-1', 'question': '本次是否申请修改已登记规范？', 'blocking': True}]
        runner.write_json(self.task / 'clarification-contract.json', value)
        self.assertEqual(validate_clarification(self.task, self.request, runner.task_identity(self.task))['status'], 'pending')
        with self.assertRaisesRegex(ValueError, '需求澄清尚未完成'):
            runner.validate_spec(self.task, self.request, self.config)
        value['open_questions'][0]['blocking'] = False
        runner.write_json(self.task / 'clarification-contract.json', value)
        with self.assertRaisesRegex(ValueError, '来源冲突'):
            validate_clarification(self.task, self.request, runner.task_identity(self.task))

    def test_old_protocol_cannot_reuse_v3_bindings(self):
        self.initialize()
        value = json.loads((self.task / 'clarification-contract.json').read_text())
        value['schema'] = 'factory-local/clarification/1'
        runner.write_json(self.task / 'clarification-contract.json', value)
        with self.assertRaisesRegex(ValueError, '不匹配'):
            validate_clarification(self.task, self.request, runner.task_identity(self.task))

    def test_noncanonical_source_paths_and_scopes_are_rejected(self):
        self.normative()
        for field, value in [('path', 'docs//rules.md'), ('path', 'docs/./rules.md'),
                             ('path', 'docs/rules.md/'), ('scope_paths', ['src//']),
                             ('scope_paths', ['src/', 'src/']), ('scope_paths', [{}])]:
            with self.subTest(field=field, value=value):
                policy = copy.deepcopy(self.config['business_sources'])
                policy['sources'][0][field] = value
                with self.assertRaises(ValueError): capture(self.root, policy, {})
