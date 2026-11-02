"""Drive one journal winner, never resume unknown create/start, and release capacity only after exact daemon cleanup."""
import asyncio
import base64
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time

from app.core.contracts.community_code_checks import CodeCheckAdmission, CodeCheckObservation
from app.core.contracts.community_changes import CommunityChangeInput
from app.services.community.candidate_git import CandidateGitRepository
from app.services.community.change_bundles import build_change_bundle, verify_public_snapshot
from app.services.community.change_preparation import current_prepared_work
from app.services.community.code_check_docker import DockerBoundaryError, isolated_docker_cli
from app.services.community.code_check_schema import CHECK_PROFILE
from app.services.community.evaluations import canonical, digest, source_row, validate_proof
from app.services.community.events import unique_fields, invalid_number, finite_float
from app.services.community.public_code import verify_public_code, PublicCodeObservation, CODE_PROTOCOL
from app.services.community.public_source import verify_public_issue
from app.services.community.store import CommunityStoreConflict

RUNNER = Path(__file__).resolve().parents[3] / 'community_isolated_runner.py'


def frame_for(checks, attempt, candidate, repository):
    """Read complete immutable committed source manifests and bind the installed trusted supervisor, with no checkout or host mount."""
    artifact = checks.changes.get(attempt['bundle_id'])
    return {'profile': CHECK_PROFILE, 'attempt_id': attempt['id'], 'deadline_us': attempt['deadline_us'],
        'runner_sha256': hashlib.sha256(RUNNER.read_bytes()).hexdigest(), 'artifact': artifact,
        'base_manifest': repository.code_manifest(artifact['bundle']['scope']['base_tree']),
        'candidate_manifest': repository.code_manifest(candidate.tree)}


def validate_report(raw, cli_exit, record, frame):
    """Accept only a stopped trusted supervisor matching exact original source/kernel/UID facts; discard candidate diagnostic text after hashing."""
    try:
        if not isinstance(raw, bytes) or not 1 <= len(raw) <= 196608: raise ValueError()
        report = json.loads(raw.decode('utf8'), object_pairs_hook=unique_fields, parse_constant=invalid_number, parse_float=finite_float)
        state = record['State']; status = report['status']
        if (state.get('Running') is not False or state.get('Restarting') is not False or state.get('Status') != 'exited'
                or state.get('OOMKilled') is not False or status not in {'passed','failed','timed_out'}): raise ValueError()
        if (report.get('profile') != CHECK_PROFILE or report.get('attempt_id') != frame['attempt_id']
                or report.get('bundle_id') != frame['artifact']['bundle_id'] or report.get('scope_hash') != frame['artifact']['bundle']['scope_hash']
                or report.get('runner_sha256') != frame['runner_sha256'] or report.get('base_manifest_hash') != digest(frame['base_manifest'])
                or report.get('candidate_manifest_hash') != digest(frame['candidate_manifest'])): raise ValueError()
        if (any(report.get(name) is not True for name in ('code_executed','kernel_limits_verified','namespace_isolation_verified',
                'uid_separation_verified','no_new_privileges','seccomp_filter','image_readonly'))
                or any(report.get(name) is not False for name in ('independent_review_completed','ci_qualified','deployment_authorized'))
                or any(type(report.get(name)) is not int or report[name] != 0 for name in ('external_model_calls','paid_authorization'))): raise ValueError()
        expected = {'supervisor_uid':0,'candidate_uid':10001,'candidate_gid':10001,'memory_bytes':536870912,
            'swap_bytes':0,'pids':64,'cpu_quota_ratio':1,'interfaces':['lo'],'candidate_cap_eff':'0000000000000000'}
        if any(report.get(name) != value or isinstance(value,int) and type(report.get(name)) is not int for name,value in expected.items()): raise ValueError()
        if int(report.get('supervisor_cap_eff','-1'),16) != 0xe0: raise ValueError()
        output = base64.b64decode(report['diagnostic_output_base64'], validate=True)
        count = report['output_bytes']; exit_code = report['process_exit_code']
        if (type(count) is not int or not 0 <= count <= 65537 or len(output) != min(count,65536)
                or hashlib.sha256(output).hexdigest() != report['output_sha256'] or type(exit_code) is not int or not -128 <= exit_code <= 255): raise ValueError()
        if state.get('ExitCode') != (0 if status == 'passed' else 124 if status == 'timed_out' else 1): raise ValueError()
        if status == 'passed' and (cli_exit != 0 or exit_code != 0 or count > 65536 or report.get('reason') != 'candidate_tests_finished'): raise ValueError()
        return CodeCheckObservation(status, exit_code, frame['runner_sha256'], report['output_sha256'], count, True, True, True)
    except (ValueError,TypeError,KeyError,AttributeError,OverflowError):
        raise DockerBoundaryError('Trusted stopped supervisor result was not verified; candidate output is not an approval') from None


def reconcile_check(checks, boundary, identifier):
    """Stop/remove only a positively owned original CID; an absent resource after unacknowledged create remains unresolved without rerunning it."""
    view = checks.get(identifier)
    if not view['active_reservation']: return view
    attempt = checks.private_ownership(identifier); owner = attempt['owner_token']
    try:
        with boundary.budget(checks.policy.cleanup_seconds):
            record = boundary.find_owned(attempt, expected_id=view['container_id'])
            if record is not None and view['container_id'] is None:
                view = checks.record_created(identifier, owner, record['Id'])
            cleanup = boundary.remove_owned(attempt, expected_id=view['container_id'])
            view = checks.get(identifier)
            observed = next((event['receipt']['details'] for event in view['history'] if event['phase'] == 'observed'), None)
            outcome = 'cancelled' if view['cancel_requested'] else observed['outcome'] if observed else 'interrupted'
            return checks.finish(identifier, owner, outcome, cleanup)
    except (CommunityStoreConflict, OSError, ValueError, sqlite3.Error):
        current = checks.get(identifier)
        if not current['active_reservation']: return current
        # This append does not authorize a retry or assume that a timed-out
        # create/start call was rejected by the Docker daemon.
        return checks.cleanup_required(identifier, owner, 'cleanup_unverified')


async def attach_until_stopped(checks, boundary, attempt, frame):
    """Observe durable cancellation while bounded owned CLI I/O runs; daemon cleanup is a separate identity-fenced step."""
    remaining = (attempt['deadline_us'] - checks.changes.clock_us()) / 1000000
    if not 0 < remaining <= checks.policy.controller_seconds: raise DockerBoundaryError('Original controller deadline is spent')
    raw = (canonical(frame) + '\n').encode('utf8')
    task = asyncio.create_task(boundary.attach(attempt['container_id'], raw, timeout=remaining))
    until = time.monotonic() + remaining
    try:
        while not task.done():
            if checks.get(attempt['id'])['cancel_requested']: raise DockerBoundaryError('Original check cancellation requested')
            if time.monotonic() >= until: raise DockerBoundaryError('Original check controller deadline reached')
            await asyncio.wait({task}, timeout=.1)
        return task.result()
    finally:
        if not task.done(): task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def drive_allocated_check(checks, boundary, allocation, frame):
    """Only a newly allocated winner may create/start once; any interruption goes to cleanup/reconciliation, never an external-operation replay."""
    if allocation.get('replayed') is not False: return checks.get(allocation['id'])
    identifier = allocation['id']; attempt = checks.private_ownership(identifier); owner = attempt['owner_token']
    cancelled = False
    try:
        remaining = (attempt['deadline_us'] - checks.changes.clock_us()) / 1000000
        with boundary.budget(max(0, remaining)):
            requested = checks.request_create(identifier, owner)
            if requested.get('phase_replayed') is not False: raise DockerBoundaryError('Original create phase is already recorded; external operation will not repeat')
            container_id = boundary.create(attempt)
            checks.record_created(identifier, owner, container_id)
            record = boundary.find_owned(attempt, expected_id=container_id)
            if record is None: raise DockerBoundaryError('Acknowledged owned container disappeared before start')
            boundary.validate_created(attempt, record)
            requested = checks.request_start(identifier, owner)
            if requested.get('phase_replayed') is not False: raise DockerBoundaryError('Original start phase is already recorded; external operation will not repeat')
        attached = {**attempt, 'container_id': container_id}
        raw, cli_exit = await attach_until_stopped(checks, boundary, attached, frame)
        with boundary.budget(max(0,(attempt['deadline_us'] - checks.changes.clock_us()) / 1000000)):
            stopped = boundary.find_owned(attempt, expected_id=container_id)
            if stopped is None: raise DockerBoundaryError('Owned stopped supervisor was not observed')
            boundary.validate_created(attempt, stopped)
            checks.observe(identifier, owner, validate_report(raw, cli_exit, stopped, frame))
    except asyncio.CancelledError:
        cancelled = True; checks.request_cancel(identifier)
    except (CommunityStoreConflict,OSError,ValueError,sqlite3.Error,asyncio.TimeoutError):
        if checks.get(identifier)['active_reservation']: checks.cleanup_required(identifier, owner, 'controller_interrupted')
    finally:
        result = reconcile_check(checks, boundary, identifier)
    if cancelled: raise asyncio.CancelledError()
    return result


async def run_current_check(preparation, checks, body, image_id, *, repository=None, boundary=None):
    """Reobserve exact current public issue/main/prepared scope, allocate under source selection fencing, then drive one separately enabled check."""
    original = checks.replay(body, image_id)
    if original is not None: return original
    if preparation is None: raise CommunityStoreConflict('New isolated checks require their exact current prepared source')
    changes = checks.changes; intake = preparation.intake; artifact = changes.get(body.bundle_id); payload = artifact['bundle']; scope = payload['scope']
    if (changes.config.repository_id != intake.config.repository_id or changes.config.repository_name.casefold() != intake.config.repository_name.casefold()
            or changes.root.is_relative_to(intake.root) or intake.root.is_relative_to(changes.root)):
        raise CommunityStoreConflict('Dedicated original source/change roots do not match')
    candidate_repository = repository or CandidateGitRepository()
    candidate = candidate_repository.recover(artifact)
    if candidate is None: raise CommunityStoreConflict('Create the exact inert local candidate before enabling its check')
    with closing(intake.connect()) as db:
        prepared = current_prepared_work(preparation, db, scope['work_id'])
        source = source_row(db, prepared['work']['entry']['version_id'])
    issue = await verify_public_issue(intake.config, source['issue_number'])
    code = await verify_public_code(intake.config, prepared['work']['entry']['feature_ids'], [item['path'] for item in payload['files']])
    registry = json.loads(code.snapshot.files['capabilities/features.json'].decode('utf8'))
    if digest(registry) != digest(preparation.queue.catalog()): raise CommunityStoreConflict('Installed/public feature register differs')

    async def allocate_and_drive(engine):
        """Keep network/daemon/Git metadata outside the short source writer; hold selection stable only across validation and journal allocation."""
        engine.image(image_id)
        base_manifest = candidate_repository.code_manifest(scope['base_tree']); candidate_manifest = candidate_repository.code_manifest(candidate.tree)
        with closing(intake.connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                now = time.time_ns() // 1000; prepared_now = current_prepared_work(preparation, db, scope['work_id'])
                validate_proof(intake.config, source_row(db, prepared_now['work']['entry']['version_id']), issue, now)
                if (not isinstance(code,PublicCodeObservation) or code.protocol != CODE_PROTOCOL or type(code.checked_at_us) is not int
                        or not 0 <= now - code.checked_at_us <= 60000000 or code.snapshot.repository_id != scope['repository_id']
                        or code.snapshot.repository_name.casefold() != scope['repository_name'] or code.snapshot.commit != scope['base_commit']
                        or code.snapshot.tree != scope['base_tree'] or verify_public_snapshot(code.snapshot) != payload['base_code_manifest']
                        or build_change_bundle(CommunityChangeInput.model_validate(payload['input']),prepared_now,code.snapshot,registry,
                            observations=payload['source_observations']) != artifact):
                    raise CommunityStoreConflict('Original prepared/public candidate scope is stale or changed')
                admission = CodeCheckAdmission(body.bundle_id,payload['scope_hash'],candidate.commit,candidate.tree,candidate.parent,image_id,
                    intake.config.repository_id,intake.config.repository_name,scope['source_fingerprint'],code.checked_at_us)
                allocation = checks.allocate(body,candidate,image_id,admission)
                db.commit()
            except BaseException:
                db.rollback(); raise
        if allocation.get('replayed'): return allocation
        # No Git/source/daemon operation occurs between reserving and building
        # this immutable in-memory frame; source files are never host-mounted.
        frame = {'profile':CHECK_PROFILE,'attempt_id':allocation['id'],'deadline_us':allocation['deadline_us'],
            'runner_sha256':hashlib.sha256(RUNNER.read_bytes()).hexdigest(),'artifact':artifact,
            'base_manifest':base_manifest,'candidate_manifest':candidate_manifest}
        return await drive_allocated_check(checks,engine,allocation,frame)

    if boundary is not None: return await allocate_and_drive(boundary)
    with isolated_docker_cli() as engine: return await allocate_and_drive(engine)


def reconcile_locally(checks, identifier, *, cancel=False):
    """Persist stop intent before contacting Docker; a disconnected daemon leaves a recoverable reservation and never clears it by age."""
    view = checks.request_cancel(identifier) if cancel else checks.get(identifier)
    if not view['active_reservation']: return view
    try:
        with isolated_docker_cli(verify_engine=False) as engine: return reconcile_check(checks, engine, identifier)
    except (CommunityStoreConflict,OSError,ValueError,sqlite3.Error):
        current = checks.get(identifier)
        if not current['active_reservation']: return current
        private = checks.private_ownership(identifier)
        return checks.cleanup_required(identifier, private['owner_token'], 'daemon_unavailable')
