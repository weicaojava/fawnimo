"""Deterministic whole-chapter compilation; expensive byte hashes stay outside writers."""
import asyncio
import hashlib
from decimal import Decimal
from fastapi import HTTPException
from sqlalchemy import select
from app.core.contracts.execution_binding import binding_fingerprint
from app.core.contracts.shot_plan import PlanDocument
from app.models.production import ProductionAsset,AssetVersion,VersionMedia,ProductionMedia
from app.models.script_preparation import ScriptPublication
from app.models.studio import Shot
from app.services.script_preparation.service import canonical,content_hash,chapter_owned,preflight_snapshot,unique,facts
from app.services.studio.production_media import media_path

MIME={'image/png':'image','image/jpeg':'image','image/webp':'image','video/mp4':'video','audio/wav':'audio'}
_UNSET=object()


def issue(code,message,shot_id=None):
    """Return a safe localized preparation failure associated with an optional shot."""
    return {'code':code,'message':message,'shot_id':shot_id}


def basic_shot_issues(shot,source,assets):
    """Share per-shot readiness and exact required entity counts with the compiler and UI."""
    result=[];identifier=source['shot_id']
    if shot is None or shot.status!='ready':result.append(issue('shot_not_ready','镜头尚未完成提取确认',identifier))
    duration=source['duration_ms']
    if duration<1000 or duration%1000 or duration>120000:result.append(issue('duration_not_supported','镜头时长须为1至120整数秒，不自动取整',identifier))
    for character in source['character_ids']:
        if sum(a is not None and not a.archived and a.kind=='character' and a.entity_id==character for a in assets)!=1:result.append(issue('character_version_required','镜头角色 '+character+' 必须恰好选择一个匹配且未归档版本',identifier))
    if sum(a is not None and not a.archived and a.kind=='scene' and a.entity_id==source['scene_id'] for a in assets)!=1:result.append(issue('scene_version_required','镜头场景必须恰好选择一个匹配且未归档版本',identifier))
    return result


async def preparation_issues(db,project_id,chapter_id,document):
    """Aggregate cheap per-shot missing fields before expensive hash validation."""
    data=await validate_document(db,project_id,chapter_id,document)
    if not data['script_snapshot_id']:return [issue('script_required','请先选择本章已发布制作剧本')]
    script=await db.get(ScriptPublication,data['script_snapshot_id'])
    choices={s['shot_id']:s for s in data['shots']};problems=[]
    for source in script.payload['document']['bindings']:
        choice=choices.pop(source['shot_id'],None)
        if choice is None:problems.append(issue('shot_missing','整章计划缺少该剧本绑定镜头',source['shot_id']));choice={'version_ids':[]}
        assets=[]
        for identity in choice['version_ids']:
            version=await db.get(AssetVersion,identity)
            assets.append(await db.get(ProductionAsset,version.asset_id))
        problems.extend(basic_shot_issues(await db.get(Shot,source['shot_id']),source,assets))
    problems.extend(issue('shot_unexpected','镜头未在所选剧本绑定中',identity) for identity in choices)
    return problems


async def validate_document(db,project_id,chapter_id,document):
    """Validate actual scope and identity without requiring complete preparation."""
    await chapter_owned(db,project_id,chapter_id)
    data=PlanDocument.model_validate(document).model_dump()
    if len(canonical(data).encode())>256*1024:raise HTTPException(422,'镜头计划文档超过256KiB')
    unique([s['shot_id'] for s in data['shots']],'镜头')
    if data['script_snapshot_id']:
        script=await db.get(ScriptPublication,data['script_snapshot_id'])
        if script is None or script.project_id!=project_id or script.payload['chapter_id']!=chapter_id:raise HTTPException(422,'剧本快照不存在或不属于本章')
    for selection in data['shots']:
        shot=await db.get(Shot,selection['shot_id'])
        if shot is None or shot.chapter_id!=chapter_id:raise HTTPException(422,'实际镜头不属于本章')
        for key in ['version_ids','reference_media_ids']:unique(selection[key],key)
        for identity in selection['version_ids']:
            row=await db.get(AssetVersion,identity)
            if row is None or row.project_id!=project_id:raise HTTPException(422,'资产版本不存在或不属于本工程')
        for identity in selection['reference_media_ids']:
            row=await db.get(ProductionMedia,identity)
            if row is None or row.project_id!=project_id:raise HTTPException(422,'参考媒体不存在或不属于本工程')
        selection['version_ids'].sort();selection['reference_media_ids'].sort()
    return data


def stat_identity(path):
    """Capture finite local file identity used only with prior validated immutable bytes."""
    stat=path.stat()
    return [stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns]


async def media_evidence(db,project_id,media_id,verified=None):
    """Verify bytes outside write locks, or recheck their exact metadata/stat inside one."""
    row=await db.get(ProductionMedia,media_id)
    if row is None or row.project_id!=project_id or row.mime not in MIME:raise HTTPException(409,'参考媒体不存在、归属或类型不符')
    path=media_path(row.relative_path)
    try:
        before=stat_identity(path)
        if before[2]!=row.size:raise ValueError()
        metadata={'id':row.id,'sha256':row.sha256,'size':row.size,'mime':row.mime,'relative_path':row.relative_path}
        if verified is None:
            checksum=await asyncio.to_thread(lambda:hashlib.sha256(path.read_bytes()).hexdigest())
            if checksum!=row.sha256 or stat_identity(path)!=before:raise ValueError()
        elif verified.get('metadata')!=metadata or verified.get('stat')!=before:raise ValueError()
    except (OSError,ValueError):raise HTTPException(409,'参考媒体已缺失、改变或校验失败') from None
    return {'id':row.id,'kind':MIME[row.mime],'mime':row.mime,'sha256':row.sha256,'size':row.size},{'metadata':metadata,'stat':before}


def public_policy(policy):
    """Freeze trusted public capability/quote identity without runtime or credentials."""
    cap=policy.capability
    return {'binding_hash':binding_fingerprint(policy),'model_id':cap.model_id,'durations':sorted(cap.durations),'reference_kinds':sorted(cap.reference_kinds),'max_references':cap.max_references,'quote_minor':policy.quote_minor,'currency':policy.currency,'iteration_limit':policy.iteration_limit}


async def build_payload(db,project_id,chapter_id,document,runtime=None,verified=None,binding_override=_UNSET):
    """Resolve original requirements, actual scope and selected immutable input snapshots."""
    data=await validate_document(db,project_id,chapter_id,document)
    if not data['model_id'].strip():raise HTTPException(409,'编译前请填写模型意向标识，未配置账号仍可准备')
    if not data['script_snapshot_id']:raise HTTPException(409,'请先选择本章已发布制作剧本')
    script=await db.get(ScriptPublication,data['script_snapshot_id'])
    if script.content_hash!=content_hash(script.payload):raise HTTPException(409,'剧本快照校验失败')
    if not (await preflight_snapshot(db,project_id,script.id))['usable']:raise HTTPException(409,'剧本准备快照已失配，请先发布新修订')
    original=script.payload['document'];bindings={b['shot_id']:b for b in original['bindings']}
    if not bindings or set(bindings)!={s['shot_id'] for s in data['shots']}:raise HTTPException(409,'整章计划须精确覆盖剧本全部绑定镜头')
    actual_facts=await facts(db,project_id,chapter_id,original)
    shot_rows={s['shot_id']:await db.get(Shot,s['shot_id']) for s in data['shots']}
    data['shots'].sort(key=lambda s:(shot_rows[s['shot_id']].index,s['shot_id']))
    policy=runtime.policies.get(data['model_id']) if runtime is not None else None
    binding=(public_policy(policy) if policy is not None else None) if binding_override is _UNSET else binding_override
    entries=[];proof={};all_versions={};all_media={}
    for selection in data['shots']:
        shot_id=selection['shot_id'];shot=shot_rows[shot_id];source=bindings[shot_id]
        pairs=[]
        for identity in selection['version_ids']:
            version=await db.get(AssetVersion,identity);pairs.append((version,await db.get(ProductionAsset,version.asset_id)))
        basic=basic_shot_issues(shot,source,[a for _,a in pairs])
        if basic:raise HTTPException(409,basic[0]['message'])
        dialogues=[d for d in original['dialogues'] if d['id'] in source['dialogue_ids']]
        if any(d['speaker_id'] not in source['character_ids'] for d in dialogues):raise HTTPException(409,'绑定对白的原说话角色未在镜头内')
        versions=[];seen_assets=set();characters=[];scenes=[];media_ids=set(selection['reference_media_ids'])
        for version,asset in pairs:
            if asset is None or asset.project_id!=project_id or asset.archived or asset.id in seen_assets:raise HTTPException(409,'资产已归档、缺失或同资产选择多个版本')
            seen_assets.add(asset.id)
            if asset.kind=='character':
                if asset.entity_id not in source['character_ids']:raise HTTPException(409,'角色版本与镜头人物不匹配')
                characters.append(asset.entity_id)
            elif asset.kind=='scene':
                if asset.entity_id!=source['scene_id']:raise HTTPException(409,'场景版本与镜头场景不匹配')
                scenes.append(asset.entity_id)
            elif asset.kind in {'appearance','voice'}:
                if asset.subject_id not in source['character_ids']:raise HTTPException(409,'服饰或声线版本不属于镜头人物')
            elif asset.kind!='prop':raise HTTPException(409,'不支持的资产用途')
            # AssetVersion was historically canonicalized without ASCII escaping.
            import json
            digest=hashlib.sha256(json.dumps(version.payload,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            relations=set((await db.execute(select(VersionMedia.media_id).where(VersionMedia.version_id==version.id,VersionMedia.project_id==project_id))).scalars())
            declared=version.payload.get('media_ids',[])
            if digest!=version.content_hash or len(set(declared))!=len(declared) or relations!=set(declared):raise HTTPException(409,'资产版本或参考集合已被篡改')
            if any(version.payload.get(key)!=getattr(asset,key) for key in ['kind','entity_id','subject_id']):raise HTTPException(409,'资产版本身份与实际资产不匹配')
            media_ids.update(declared)
            frozen={'version_id':version.id,'asset_id':asset.id,'revision':version.revision,'content_hash':version.content_hash,'payload':version.payload}
            versions.append(frozen);all_versions[version.id]=frozen
        if sorted(characters)!=sorted(source['character_ids']) or scenes!=[source['scene_id']]:raise HTTPException(409,'每个镜头人物与场景必须恰好选定一个匹配资产版本')
        media=[]
        for media_id in sorted(media_ids):
            frozen,evidence=await media_evidence(db,project_id,media_id,None if verified is None else verified.get(media_id,{}))
            media.append(frozen);proof[media_id]=evidence;all_media[media_id]=frozen
        media.sort(key=lambda m:(m['kind'],m['id']))
        references=[{'media_id':m['id'],'kind':m['kind']} for m in media]
        seconds=source['duration_ms']//1000
        if binding and (seconds not in binding['durations'] or len(references)>binding['max_references'] or any(r['kind'] not in binding['reference_kinds'] for r in references)):raise HTTPException(409,'受信模型能力不支持镜头时长或参考模式')
        beats=[b for b in original['beats'] if b['id'] in source['beat_ids']]
        prompt=canonical({'script_title':original['title'],'shot_id':shot_id,'duration_ms':source['duration_ms'],'beats':beats,'dialogues':dialogues,'assets':[v['payload'] for v in versions],'references':references,'user_addition':selection['prompt_addition']})
        if len(prompt)>32000:raise HTTPException(409,'编译提示词超过32000字，不允许截断')
        request={'model_id':data['model_id'],'duration':seconds,'references':references,'prompt':prompt}
        entry={'shot_id':shot_id,'order':len(entries),'duration_ms':source['duration_ms'],'mapping':source,'versions':versions,'media':media,'source_facts_hash':script.payload['facts_hash'],'request':request}
        entry_hash=content_hash(entry);entry['entry_hash']=entry_hash;request['prompt_revision']=entry_hash;entries.append(entry)
    payload={'schema_version':1,'compiler_version':'m4b-v1','project_id':project_id,'chapter_id':chapter_id,'script_snapshot_id':script.id,'script_snapshot_hash':script.content_hash,'script_facts_hash':script.payload['facts_hash'],'facts':actual_facts,'document':data,'binding':binding,'versions':[all_versions[k] for k in sorted(all_versions)],'media':sorted(all_media.values(),key=lambda m:(m['kind'],m['id'])),'entries':entries}
    if len(canonical(payload).encode())>1024*1024:raise HTTPException(409,'编译计划超过1MiB')
    return payload,proof
