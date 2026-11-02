"""Real separate-process CLI restart/replay checks against authored standalone public-request databases."""
from contextlib import closing
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from app.core.contracts.community_queue import QueueCancelInput
from tests.test_community_evaluations import event, add, proof
from tests.test_community_queue_leases import factory, key, claim_body, lease_body

CLI = Path(__file__).resolve().parents[1] / 'queue_community.py'


def run_cli(intake, *args):
    """Run the shipped command in a new process with finite time and a small OS-only environment, never a private model profile."""
    allowed = {'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATH', 'PATHEXT', 'TEMP', 'TMP', 'TMPDIR', 'LANG', 'LC_ALL', 'TZ'}
    environment = {name: value for name, value in os.environ.items() if name.upper() in allowed}
    environment.update(PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1', JELLYFISH_SKIP_ENV_FILE='1')
    return subprocess.run([sys.executable, str(CLI), '--root', str(intake.root), '--repository-id', '17',
        '--repository-name', 'authored/fawnimo', *map(str, args)], capture_output=True, text=True, encoding='utf8',
        timeout=25, cwd=CLI.parent, env=environment)


def write_input(tmp_path, name, value):
    """Keep private authored original inputs outside the dedicated database directory and outside public artifacts."""
    path = tmp_path / name
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf8')
    return path


def result(command):
    """Decode success without printing potentially private live-claim output in assertion messages."""
    assert command.returncode == 0, command.stderr
    return json.loads(command.stdout)


def test_cli_separate_process_plan_select_materialize_cancel_and_original_recovery(factory, tmp_path):
    """Six different CLI processes create/select/prepare/cancel and later recover immutable original operation identities."""
    intake, queue, _store, _moment, _values, old, _ids, _body = factory()
    plan_input = dict(old['plan']['input'], request_key=key('cli-plan'), title='新进程保存的本机准备路线图')
    plan_file = write_input(tmp_path, 'plan.json', plan_input)
    plan = result(run_cli(intake, '--plan-file', plan_file))
    assert plan['id'] != old['id'] and not plan['execution_authorized']
    selection = write_input(tmp_path, 'selection.json', {'request_key': key('cli-select'), 'plan_id': plan['id'], 'expected_revision': 1})
    assert result(run_cli(intake, '--selection-file', selection))['current_revision'] == 2
    materialize = write_input(tmp_path, 'materialize.json', {'request_key': key('cli-materialize'), 'plan_id': plan['id'], 'expected_revision': 2})
    prepared = result(run_cli(intake, '--materialize-file', materialize))
    identifier = prepared['receipt']['work_ids'][0]
    cancel_file = write_input(tmp_path, 'cancel.json', {'request_key': key('cli-cancel'), 'work_id': identifier, 'reason': '只取消准备，不触及任何视频项目。'})
    cancelled = result(run_cli(intake, '--cancel-file', cancel_file))
    assert cancelled['current_work']['current']['state'] == 'cancelled'
    recovered = result(run_cli(intake, '--cancel-file', cancel_file))
    assert recovered['operation_sequence'] == cancelled['operation_sequence'] and recovered['replayed']
    assert result(run_cli(intake, '--materialize-file', materialize))['receipt']['work_ids'] == [identifier]
    history = result(run_cli(intake, '--history', identifier))
    assert [item['kind'] for item in history] == ['cancel']
    assert result(run_cli(intake, '--get-work', identifier))['current']['state'] == 'cancelled'
    assert len(result(run_cli(intake, '--list-work', plan['id']))) == 1
    assert not result(run_cli(intake, '--get-plan', plan['id']))['execution_authorized']


def test_cli_original_claim_and_heartbeat_recovery_after_withdrawal_never_reclaims_or_exposes_token(factory, tmp_path):
    """Original JSON inputs recover real prior commits from new processes after the public requirement was withdrawn."""
    intake, queue, store, _moment, values, _plan, ids, _body = factory()
    queue.clock_us = lambda: time.time_ns() // 1000
    claim_input = claim_body(ids[0])
    claim = store.claim(claim_input, proof(values[0], now=queue.clock_us()))
    heartbeat = lease_body(claim)
    first = store.heartbeat(heartbeat)
    add(intake, event(time='2026-10-30T01:01:00Z', state='closed'))
    claim_file = write_input(tmp_path, 'original-claim.json', claim_input.model_dump())
    heartbeat_file = write_input(tmp_path, 'original-heartbeat-private.json', heartbeat.model_dump())
    original = result(run_cli(intake, '--claim-file', claim_file))
    renewed = result(run_cli(intake, '--heartbeat-file', heartbeat_file))
    assert original['receipt']['attempt_id'] == claim['receipt']['attempt_id'] and 'lease_token' not in original
    assert renewed['operation_sequence'] == first['operation_sequence'] and not renewed['current_work']['lease_valid']
    history = run_cli(intake, '--history', ids[0])
    assert history.returncode == 0 and claim['lease_token'] not in history.stdout
    with closing(intake.connect()) as db:
        assert db.execute('SELECT count(*) FROM community_queue_attempts').fetchone()[0] == 1


@pytest.mark.parametrize('invalid', ['duplicate', 'authority', 'oversize', 'malformed'])
def test_cli_rejects_ambiguous_or_authority_bearing_json_before_mutation(factory, tmp_path, invalid):
    """Bad original JSON cannot add work, change policy or supply a fake source observation to the shipped CLI."""
    intake, _queue, _store, _moment, _values, _plan, ids, _body = factory()
    path = tmp_path / 'invalid.json'
    if invalid == 'duplicate':
        raw = '{"request_key":"original-key","request_key":"another-key","work_id":"' + ids[0] + '","owner":"local"}'
    elif invalid == 'authority':
        raw = json.dumps({'request_key': key('bad-claim'), 'work_id': ids[0], 'owner': 'local', 'source_proof': {'execution_authorized': True}})
    elif invalid == 'oversize':
        raw = '{"untrusted":"' + 'x' * 65536 + '"}'
    else:
        raw = '{"request_key":'
    path.write_text(raw, encoding='utf8')
    failed = run_cli(intake, '--claim-file', path)
    assert failed.returncode == 1 and not failed.stdout
    assert 'Queue operation not committed' in failed.stderr
    with closing(intake.connect()) as db:
        assert db.execute('SELECT count(*) FROM community_queue_attempts').fetchone()[0] == 0
        assert db.execute('SELECT revision FROM community_queue_current').fetchone()[0] == 1


def test_cli_has_no_command_host_or_source_bypass_switch(factory):
    """The public command advertises only roadmap/preparation operations and rejects an arbitrary executor option."""
    intake, _queue, _store, _moment, _values, _plan, _ids, _body = factory()
    help_result = run_cli(intake, '--help')
    assert help_result.returncode == 0 and 'no development executor' in help_result.stdout
    rejected = run_cli(intake, '--command', 'untrusted-issue-instruction')
    assert rejected.returncode == 2
