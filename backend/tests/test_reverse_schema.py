"""Additive schema guards keep source and machine reports independent of cut edits."""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from app.config import settings
from app.services.reverse.schema import migrate_reverse
from app.services.reverse.ingestion import create_analysis,run_analysis,edit_cuts
from tests.test_reverse_ingestion import setup,fixture_probe


@pytest.mark.asyncio
async def test_reverse_migration_freezes_evidence_but_allows_confirmed_edits(tmp_path,monkeypatch):
    """Repeat migration, reject raw rewrites/deletes, and preserve legitimate CAS edits."""
    monkeypatch.setattr(settings,'production_media_root',str(tmp_path))
    engine,factory=await setup(tmp_path)
    try:
        await migrate_reverse(engine);await migrate_reverse(engine)
        identity=await create_analysis(factory,'a','a-video','schema')
        assert await run_analysis(factory,identity,'fixture',probe_fn=fixture_probe,clock=lambda:1000)
        for statement in ["UPDATE reverse_analyses SET source_hash='changed' WHERE id=:id","UPDATE reverse_analyses SET manifest='{}' WHERE id=:id","DELETE FROM reverse_analyses WHERE id=:id"]:
            with pytest.raises(IntegrityError):
                async with engine.begin() as connection:await connection.execute(text(statement),{'id':identity})
        result=await edit_cuts(factory,'a',identity,[0,1],1)
        assert result['revision']==2
        async with engine.begin() as connection:await connection.execute(text('ALTER TABLE reverse_analyses ADD COLUMN incompatible TEXT'))
        with pytest.raises(RuntimeError,match='Unsupported reverse-analysis schema'):await migrate_reverse(engine)
    finally:await engine.dispose()
