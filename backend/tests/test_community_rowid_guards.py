"""Actual implicit-rowid collision and strict additive legacy-upgrade regressions use authored dedicated databases only."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import uuid
import pytest
from app.core.contracts.community_evaluations import CommunityEvaluationInput
from app.services.community.store import CommunityRequestStore,CommunityStoreConflict
from app.services.community.evaluations import CommunityEvaluationStore,canonical,digest
from tests.test_community_evaluations import CONFIG,NOW,event,add,proof,evaluation


def seeded(root):
    """Build exactly the public intake/review schema with one original immutable assessment, never user data."""
    intake=CommunityRequestStore(root,CONFIG);reviews=CommunityEvaluationStore(intake,initialize=True,clock_us=lambda:NOW)
    value=event();version=add(intake,value);original=reviews.record(evaluation(version),proof(value))
    return intake,reviews,original


def drop_supplemental_guards(intake,keep=0):
    """Represent the exact earlier published source schema: same tables/data/old guards, only new supplemental names absent."""
    with closing(intake.connect()) as db:
        names=sorted(row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='trigger'") if row[0].endswith('_no_rowid_replace'))
        assert len(names)==7
        for name in names[keep:]:db.execute('DROP TRIGGER '+name)
        db.commit()


def rows(intake):
    """Compare all existing logical rows through the migration, without exposing their contents or credentials in output."""
    with closing(intake.connect()) as db:
        tables=sorted(row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'") if not row[0].startswith('sqlite_'))
        return {table:db.execute('SELECT rowid,* FROM '+table+' ORDER BY rowid').fetchall() for table in tables}


@pytest.mark.parametrize('table',['community_deliveries','community_evaluations'])
def test_explicit_rowid_replace_with_different_logical_keys_is_rejected_even_without_recursive_triggers(tmp_path,table):
    """Reproduce the previously successful bypass with fully valid new logical keys and an explicit colliding physical rowid."""
    intake,_reviews,_original=seeded(tmp_path/'requests');before=rows(intake)
    with closing(sqlite3.connect(intake.database)) as db:
        db.execute('PRAGMA foreign_keys=ON');db.execute('PRAGMA recursive_triggers=OFF')
        data=list(db.execute('SELECT rowid,* FROM '+table).fetchone());data[1]=str(uuid.uuid4())
        if table=='community_evaluations':
            data[4]='rowid-'+uuid.uuid4().hex
            payload=json.loads(data[6]);payload['input']['request_key']=data[4]
            data[5]=digest({'domain':'community-evaluation-input-v1',**payload['input']});data[6]=canonical(payload);data[7]=digest(payload)
        columns=['rowid',*(row[1] for row in db.execute('PRAGMA table_info('+table+')'))]
        with pytest.raises(sqlite3.IntegrityError,match='rowid replacement'):
            db.execute('INSERT OR REPLACE INTO '+table+'('+','.join(columns)+') VALUES('+','.join('?' for _ in data)+')',data)
        db.rollback()
    assert rows(intake)==before


@pytest.mark.parametrize('keep',[0,3,7])
def test_explicit_legacy_guard_upgrade_is_idempotent_and_preserves_all_rows_and_old_definitions(tmp_path,keep):
    """Only absent supplemental guards are installed; complete/partial upgrades preserve every existing row and immutable source receipt."""
    intake,_reviews,original=seeded(tmp_path/'requests');before=rows(intake);drop_supplemental_guards(intake,keep)
    if keep<7:
        with pytest.raises(CommunityStoreConflict):CommunityRequestStore(intake.root,CONFIG,initialize=False)
    legacy=CommunityRequestStore(intake.root,CONFIG,initialize=False,allow_legacy_rowid_guards=True)
    assert legacy.upgrade_rowid_guards()=={'status':'upgraded','added_guards':7-keep,'rows_rewritten':0}
    assert legacy.upgrade_rowid_guards()['added_guards']==0
    assert rows(intake)==before
    reopened=CommunityRequestStore(intake.root,CONFIG,initialize=False)
    assert CommunityEvaluationStore(reopened).replay(CommunityEvaluationInput.model_validate(original['receipt']['input']))['id']==original['id']


@pytest.mark.parametrize('fault',['old_guard_missing','new_guard_altered','source_table_altered'])
def test_migration_refuses_unknown_or_weakened_schema_without_adding_any_guard(tmp_path,fault):
    """The legacy exception cannot accept changed existing guards or columns, even if they use a trusted trigger/table name."""
    intake,_reviews,_original=seeded(tmp_path/'requests');drop_supplemental_guards(intake)
    with closing(intake.connect()) as db:
        if fault=='old_guard_missing':db.execute('DROP TRIGGER community_versions_no_delete')
        elif fault=='new_guard_altered':db.execute("CREATE TRIGGER community_deliveries_no_rowid_replace BEFORE INSERT ON community_deliveries BEGIN SELECT 1; END")
        else:db.execute('ALTER TABLE community_requests ADD COLUMN unexpected TEXT')
        db.commit();before=db.execute("SELECT name,sql FROM sqlite_master ORDER BY name").fetchall()
    with pytest.raises(CommunityStoreConflict):
        legacy=CommunityRequestStore(intake.root,CONFIG,initialize=False,allow_legacy_rowid_guards=True)
        legacy.upgrade_rowid_guards()
    with closing(intake.connect()) as db:assert db.execute("SELECT name,sql FROM sqlite_master ORDER BY name").fetchall()==before


def test_real_upgrade_command_uses_only_explicit_existing_root_and_preserves_review_history(tmp_path):
    """Run the shipped guard-only migration twice in actual processes; raw input content/configuration is never printed."""
    intake,_reviews,_original=seeded(tmp_path/'requests');before=rows(intake);drop_supplemental_guards(intake)
    script=Path(__file__).resolve().parents[1]/'migrate_community_guards.py'
    command=[sys.executable,str(script),'--root',str(intake.root),'--repository-id','17','--repository-name','authored/fawnimo']
    for expected in (7,0):
        result=subprocess.run(command,cwd=tmp_path,capture_output=True,text=True,encoding='utf8',timeout=10)
        assert result.returncode==0,result.stderr
        assert json.loads(result.stdout)=={'status':'upgraded','added_guards':expected,'rows_rewritten':0}
    assert rows(intake)==before
