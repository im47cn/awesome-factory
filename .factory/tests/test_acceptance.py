"""契约冻结/独立执行/漂移负控制：真实临时 Git 仓，不联网。"""
import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import acceptance as a


def commit(repo):
    subprocess.run(['git', '-C', str(repo), 'add', 'check.py', 'value'], check=True)
    subprocess.run(['git', '-C', str(repo), '-c', 'user.name=test', '-c', 'user.email=test@example.invalid',
                    'commit', '-qm', 'test: fixture'], check=True)


@pytest.fixture()
def setup(tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    subprocess.run(['git', '-C', str(repo), 'init', '-q'], check=True)
    (repo / 'check.py').write_text('from pathlib import Path\nassert Path("value").read_text() == "fixed"\n')
    (repo / 'value').write_text('fixed')
    commit(repo)
    c = {'schema': 'acceptance-contract/1', 'requirement_id': 'REQ-1', 'goal': '正确读取',
         'non_goals': [], 'acceptance': [{'id': 'AC-1', 'criterion': '读取固定值',
          'argv': [sys.executable, 'check.py'], 'test_files': ['check.py'], 'timeout_seconds': 3}]}
    c['acceptance'][0]['test_hashes'] = {'check.py': a.file_hash(repo / 'check.py')}
    c['approval'] = {'approved_by': 'operator', 'approved_revision': a.digest(c)}
    source, frozen, receipt = (tmp_path / x for x in ('source.json', 'frozen.json', 'receipt.json'))
    a.write(source, c); a.write(frozen, c)
    return repo, source, frozen, receipt, c


def test_real_execution_and_binding(setup):
    repo, source, frozen, receipt, c = setup
    data = a.collect(source, frozen, repo, receipt)
    assert data['results'][0]['exit_code'] == 0
    assert a.verify(source, frozen, repo, receipt)['contract']['goal'] == c['goal']


@pytest.mark.parametrize('mutation', ['approval', 'criterion', 'argv', 'id', 'empty'])
def test_contract_negative_controls(setup, mutation):
    repo, source, frozen, receipt, c = setup
    if mutation == 'approval': c.pop('approval')
    elif mutation == 'empty': c['acceptance'] = []
    elif mutation == 'id': c['acceptance'].append(copy.deepcopy(c['acceptance'][0]))
    else: c['acceptance'][0][mutation] = 'different' if mutation == 'criterion' else ['echo', 'ok']
    a.write(source, c)
    with pytest.raises(a.GateError):
        a.collect(source, frozen, repo, receipt)
    assert a.read(receipt)['schema'] == 'acceptance-evidence-invalid/1'


@pytest.mark.parametrize('mutation', ['log', 'commit', 'result', 'test', 'untracked', 'reapprove'])
def test_stale_or_forged_evidence_rejected(setup, mutation):
    repo, source, frozen, receipt, c = setup
    data = a.collect(source, frozen, repo, receipt)
    if mutation == 'log': (receipt.parent / data['results'][0]['log']).write_text('forged')
    elif mutation == 'commit':
        (repo / 'value').write_text('different'); commit(repo)
    elif mutation == 'result':
        data['results'] = []; a.write(receipt, data)
    elif mutation == 'test': (repo / 'check.py').write_text('pass\n')
    elif mutation == 'untracked': (repo / 'other').write_text('dirty')
    else:
        c['approval']['approved_by'] = 'other'; a.write(source, c)
    with pytest.raises(a.GateError): a.verify(source, frozen, repo, receipt)


def test_baseline_red_and_fixed_green(setup):
    repo, source, frozen, receipt, c = setup
    (repo / 'value').write_text('bad'); commit(repo)
    red = a.collect(source, frozen, repo, receipt, 'baseline')
    assert red['results'][0]['exit_code'] == 1
    (repo / 'value').write_text('fixed'); commit(repo)
    green = a.collect(source, frozen, repo, receipt)
    assert green['results'][0]['exit_code'] == 0
    assert red['results'][0]['test_files'] == green['results'][0]['test_files']


@pytest.mark.parametrize('code', [2, 3, 127])
def test_infrastructure_errors_not_reproduction(setup, code):
    repo, source, frozen, receipt, c = setup
    c['acceptance'][0]['argv'] = [sys.executable, '-c', f'raise SystemExit({code})']
    c['approval']['approved_revision'] = a.digest(a.specification(c))
    a.write(source, c); a.write(frozen, c)
    with pytest.raises(a.GateError): a.collect(source, frozen, repo, receipt, 'baseline')


def test_timeout_not_red(setup):
    repo, source, frozen, receipt, c = setup
    c['acceptance'][0]['argv'] = [sys.executable, '-c', 'import time; time.sleep(30)']
    c['acceptance'][0]['timeout_seconds'] = 1
    c['approval']['approved_revision'] = a.digest(a.specification(c))
    a.write(source, c); a.write(frozen, c)
    with pytest.raises(a.GateError, match='超时'): a.collect(source, frozen, repo, receipt, 'baseline')
    assert a.read(receipt)['schema'] == 'acceptance-evidence-invalid/1'


def test_command_mutation_rejected(setup):
    repo, source, frozen, receipt, c = setup
    c['acceptance'][0]['argv'] = [sys.executable, '-c', 'from pathlib import Path; Path("value").write_text("bad")']
    c['approval']['approved_revision'] = a.digest(a.specification(c))
    a.write(source, c); a.write(frozen, c)
    with pytest.raises(a.GateError, match='工作树'): a.collect(source, frozen, repo, receipt)


def test_path_escape(setup):
    repo, source, frozen, receipt, c = setup
    c['acceptance'][0]['test_files'] = ['../check.py']
    with pytest.raises(a.GateError): a.validate(c, approved=False)


def test_source_opt_in_and_required_missing(setup):
    repo, source, frozen, receipt, c = setup
    cfg = repo / '.factory/factory-local.json'
    a.write(cfg, {'acceptance_required': False})
    assert a.source_path(repo, 'anything') == ''
    a.write(cfg, {'acceptance_required': True})
    with pytest.raises(a.GateError): a.source_path(repo, '1')
    a.write(repo / '.factory/acceptance-contracts/issue-1.json', c)
    assert a.source_path(repo, '1').endswith('issue-1.json')


def test_shell_adapter_freezes_runs_and_exposes_all_criteria(setup, tmp_path):
    repo, source, frozen, receipt, c = setup
    factory = Path(a.__file__).parent
    # CLI 用临时配置及实际 runner，不创建远程对象。
    import shutil
    (repo / '.factory').mkdir(exist_ok=True)
    for name in ('acceptance.py', 'acceptance-lib.sh'):
        shutil.copy2(factory / name, repo / '.factory' / name)
    a.write(repo / '.factory/factory-local.json', {'acceptance_required': True})
    a.write(repo / '.factory/acceptance-contracts/issue-1.json', c)
    subprocess.run(['git', '-C', str(repo), 'add', '.factory'], check=True)
    subprocess.run(['git', '-C', str(repo), '-c', 'user.name=test', '-c', 'user.email=t@example.invalid',
                    'commit', '-qm', 'test: config'], check=True)
    driver = 'set -euo pipefail; REPO="$1"; DIR="$2"; DRY=0; source "$REPO/.factory/acceptance-lib.sh"; acceptance_init 1; acceptance_run "$REPO"; acceptance_context "$REPO"'
    r = subprocess.run(['bash', '-c', driver, 'test', str(repo), str(tmp_path / 'artifacts')],
                       capture_output=True, text=True, env=os.environ.copy())
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)['contract']['acceptance'][0]['id'] == 'AC-1'


def test_committed_weakened_test_fails_against_approval(setup):
    repo, source, frozen, receipt, c = setup
    (repo / 'check.py').write_text('pass\n'); commit(repo)
    with pytest.raises(a.GateError, match='批准字节'):
        a.collect(source, frozen, repo, receipt)
    assert a.read(receipt)['schema'] == 'acceptance-evidence-invalid/1'


def test_each_raw_output_reaches_holdout_context(setup):
    repo, source, frozen, receipt, c = setup
    c['acceptance'][0]['argv'] = [sys.executable, '-c', 'print("AC-1 independent observation")']
    c['approval']['approved_revision'] = a.digest(a.specification(c))
    a.write(source, c); a.write(frozen, c)
    a.collect(source, frozen, repo, receipt)
    assert a.verify(source, frozen, repo, receipt)['observations'][0]['output'] == 'AC-1 independent observation\n'


def test_failed_rerun_invalidates_old_receipt(setup):
    repo, source, frozen, receipt, c = setup
    a.collect(source, frozen, repo, receipt)
    (repo / 'value').write_text('dirty')
    with pytest.raises(a.GateError): a.collect(source, frozen, repo, receipt)
    assert a.read(receipt)['schema'] == 'acceptance-evidence-invalid/1'
    assert list(receipt.parent.glob('receipt-previous-*.json'))
    (repo / 'value').write_text('fixed')
    with pytest.raises(a.GateError): a.verify(source, frozen, repo, receipt)


def test_pr_gate_removes_stale_validation_on_missing_contract(tmp_path):
    import shutil
    repo = tmp_path / 'repo'; (repo / '.factory').mkdir(parents=True)
    factory = Path(a.__file__).parent
    for name in ('acceptance.py', 'acceptance-lib.sh'):
        shutil.copy2(factory / name, repo / '.factory' / name)
    a.write(repo / '.factory/factory-local.json', {'acceptance_required': True})
    text = (factory / 'validate-pr.sh').read_text()
    failure = text[text.index('fail() {'):text.index('# 契约启用时')]
    output = tmp_path / 'host-calls'
    driver = 'set -euo pipefail; REPO="$1"; DIR="$2"; DRY=0; PR=1; HOST="stub_host"; stub_host() { printf "%s\\n" "$*" >> "$3"; }; source "$REPO/.factory/acceptance-lib.sh"; '
    # 捕获输出通过环境变量，不让函数参数覆盖路径。
    driver = driver.replace('>> "$3"', '>> "$HOST_LOG"') + failure + '\nacceptance_init 42 || fail gate "missing contract" 1'
    env = os.environ.copy(); env['HOST_LOG'] = str(output)
    r = subprocess.run(['bash','-c',driver,'test',str(repo),str(tmp_path/'artifacts')],env=env,capture_output=True,text=True)
    assert r.returncode == 1
    assert '--remove factory:validated --add factory:validation-failed' in output.read_text()


def test_contract_symlink_cannot_leave_protected_directory(setup):
    repo, source, frozen, receipt, c = setup
    a.write(repo / '.factory/factory-local.json', {'acceptance_required': True})
    a.write(repo / 'unprotected.json', c)
    path = repo / '.factory/acceptance-contracts/issue-1.json'; path.parent.mkdir()
    path.symlink_to(repo / 'unprotected.json')
    with pytest.raises(a.GateError, match='symlink'): a.source_path(repo, '1')


def test_approve_freeze_and_cli_errors(setup):
    repo, source, frozen, receipt, c = setup
    script = str(Path(a.__file__))
    c.pop('approval'); c['acceptance'][0].pop('test_hashes'); a.write(source, c)
    r = subprocess.run([sys.executable, script, 'approve', str(source), '--by', 'human', '--repo', str(repo)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert a.read(source)['acceptance'][0]['test_hashes']['check.py'] == a.file_hash(repo / 'check.py')
    r = subprocess.run([sys.executable, script, 'freeze', str(source), str(frozen)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    r = subprocess.run([sys.executable, script, 'run', '--source', str(source), '--frozen', str(frozen), '--repo', str(repo), '--receipt', str(receipt)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    r = subprocess.run([sys.executable, script, 'verify', '--source', str(source), '--frozen', str(frozen), '--repo', str(repo), '--receipt', str(receipt)], capture_output=True, text=True)
    assert r.returncode == 0 and 'observations' in json.loads(r.stdout)
    r = subprocess.run([sys.executable, script, 'approve', str(source), '--by', ' ', '--repo', str(repo)], capture_output=True, text=True)
    assert r.returncode == 1


def test_cli_dispatch_in_process(setup, monkeypatch, capsys):
    repo, source, frozen, receipt, c = setup
    script = str(Path(a.__file__))
    def cli(*args):
        monkeypatch.setattr(sys, 'argv', [script, *map(str,args)])
        return a.main()
    c.pop('approval'); a.write(source,c)
    assert cli('approve',source,'--by','human','--repo',repo)==0
    assert cli('freeze',source,frozen)==0
    assert cli('run','--source',source,'--frozen',frozen,'--repo',repo,'--receipt',receipt)==0
    assert cli('verify','--source',source,'--frozen',frozen,'--repo',repo,'--receipt',receipt)==0
    a.write(repo/'.factory/factory-local.json',{'acceptance_required':False})
    assert cli('source','--repo',repo,'--issue','1')==0
    assert cli('approve',source,'--by',' ','--repo',repo)==1
    source.write_text('[]')
    assert cli('freeze',source,frozen)==1


@pytest.mark.parametrize('key,value', [('schema','wrong'),('goal',''),('non_goals',None),('acceptance',[None]),
                                      ('criterion',''),('argv',[]),('test_files',[]),('timeout_seconds',True),('test_hashes',{})])
def test_contract_shape_fail_closed(setup,key,value):
    repo,source,frozen,receipt,c=setup
    if key in ('criterion','argv','test_files','timeout_seconds','test_hashes'): c['acceptance'][0][key]=value
    else:c[key]=value
    with pytest.raises(a.GateError):a.validate(c)


@pytest.mark.parametrize('config,issue', [({'acceptance_required':'yes'},'1'),
    ({'acceptance_required':True},'../1'),
    ({'acceptance_required':True,'acceptance_contract_dir':'/tmp'},'1'),
    ({'acceptance_required':True,'acceptance_contract_dir':'contracts'},'1')])
def test_invalid_enablement_rejected(setup,config,issue):
    repo,source,frozen,receipt,c=setup
    a.write(repo/'.factory/factory-local.json',config)
    with pytest.raises(a.GateError):a.source_path(repo,issue)


def test_remote_pr_head_change_rejected_after_initial_check(tmp_path):
    helper = Path(a.__file__).parent / 'acceptance-lib.sh'
    driver = 'set -euo pipefail; source "$1"; ACCEPTANCE_SOURCE=enabled; REMOTE=aaa; git() { if [ "$1" = ls-remote ]; then printf "%s refs/heads/task\\n" "$REMOTE"; else printf "aaa\\n"; fi; }; acceptance_pr_head task; REMOTE=bbb; acceptance_pr_head task'
    r = subprocess.run(['bash','-c',driver,'test',str(helper)],capture_output=True,text=True)
    assert r.returncode == 1
