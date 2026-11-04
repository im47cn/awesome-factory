"""Machine-owned contract envelope; semantic decisions remain in Rules Skills."""
import copy
import hashlib
import json
import re
from .policy import PUBLIC_CONFIGURATION

ID_PATTERN = r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}'
ENVIRONMENTS = ('local', 'mock', 'test-environment')
EVIDENCE_TYPES = ('browser', 'integrity', 'other')
SCHEMAS = {'requirement': 'factory-local/requirement-contract/1',
           'clarification': 'factory-local/clarification/3', 'spec': 'factory-local/spec-contract/2'}
CONTAINERS = {'requirement': ('observations', 'behavior_candidates', 'open_questions', 'skill_usage', 'acceptance_candidates'),
              'clarification': ('rules', 'requirement_coverage', 'open_questions', 'assumptions', 'source_conflicts'),
              'spec': ('acceptance', 'verification', 'external_requirements')}


class ContractFormatError(ValueError):
    """Only malformed JSON/containers/IDs are eligible for bounded format repair."""


def complete(stage, value, binding, *, reference_hashes=None):
    if not isinstance(value, dict):
        raise ContractFormatError('契约根节点必须是对象')
    result = copy.deepcopy(value)
    filled = []
    for key, expected in {'schema': SCHEMAS[stage], **{k: v for k, v in binding.items() if k != 'schema'}}.items():
        if key in result and result[key] != expected:
            raise ValueError('模型提供了冲突的运行器绑定：' + key)
        if key not in result:
            result[key] = expected
            filled.append(key)
    for key in CONTAINERS[stage]:
        if not isinstance(result.get(key), list):
            raise ContractFormatError(key + ' 必须显式为数组；不能自动猜测业务内容')
    if stage == 'requirement':
        for index, row in enumerate(result['observations']):
            if (isinstance(row, dict) and row.get('kind') == 'source'
                    and isinstance(row.get('source'), dict)
                    and row['source'].get('path') in PUBLIC_CONFIGURATION):
                row['kind'] = 'docs'
                filled.append(f'observations.{index}.kind=docs(public-configuration)')
    if stage == 'spec':
        references = result.get('references', [])
        if not isinstance(references, list):
            raise ContractFormatError('references 必须为数组')
        for index, row in enumerate(references):
            if (not isinstance(row, dict) or not isinstance(row.get('path'), str)
                    or row['path'] not in (reference_hashes or {})):
                continue
            expected = reference_hashes[row['path']]
            if 'sha256' in row and row['sha256'] != expected:
                raise ValueError('模型提供了冲突的单文件引用绑定：' + row['path'])
            if 'sha256' not in row:
                row['sha256'] = expected
                filled.append(f'references.{index}.sha256')
        seen = set()
        for row in result['verification']:
            if not isinstance(row, dict) or not isinstance(row.get('id'), str) or not re.fullmatch(ID_PATTERN, row['id']) or row['id'] in seen:
                raise ContractFormatError('verification.id 必须为唯一的安全标识，不能自动重排引用')
            seen.add(row['id'])
            if 'status' not in row:
                row['status'] = 'NOT_RUN'
                filled.append('verification.' + row['id'] + '.status')
            elif row['status'] != 'NOT_RUN':
                raise ValueError('Spec 不能自报已执行结果：' + row['id'])
    return result, filled


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def reference_fields():
    """Generated Rules reference and prompt share this executable definition."""
    return ('# Factory 契约机械字段（自动生成）\n\n'
            '由 awesome-factory/tools/sync-local-protocol.py 生成；修改运行器定义后重新生成，勿手改。\n\n'
            '- schema、task_id、workflow_version、所有输入摘要由 Factory 补齐；模型可省略，提供冲突值则阻断。\n'
            '- Spec verification.status 缺失时补 NOT_RUN；任何已执行状态均拒绝。\n'
            '- verification.id：`' + ID_PATTERN + '`；引用关系由模型保持，运行器不擅自重编号。\n'
            '- environment：' + ', '.join(ENVIRONMENTS) + '。\n'
            '- manual evidence_type：' + ', '.join(EVIDENCE_TYPES) + '；command 不接受 evidence_type。\n'
            '- 需要浏览器验收时，Spec 至少有一项 required=true、kind=manual、evidence_type=browser。\n'
            '- integrity 的 executor=source-integrity 仅证明 HEAD、修改范围和范围外文件内容/模式/索引；不证明业务语义。\n'
            '- 可选 references 只列原始 request 或本业务仓已纳入基线的普通文件；不列其他仓库的 Skill 路径。模型可只给 path，Factory 补对应单文件 sha256；已提供冲突摘要则阻断。\n'
            '- browser_plan_sha256 是计划路径/内容/模式/索引的综合绑定，不是计划文件 sha256；不能用于 references。\n'
            '- 格式修复最多两次（默认一次），只反馈格式诊断；绑定冲突、授权或业务缺口不能自动修复。\n')
