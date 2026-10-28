"""Validate human DNA references, evidence ranges and fixed production version bytes."""
import hashlib
import json
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from app.core.contracts.video_dna import DnaDocument,FIELD_KEYS
from app.models.production import AssetVersion,ProductionAsset,VersionMedia,ProductionMedia
from app.services.video_dna.source import issue,media_evidence
from app.services.script_preparation.service import canonical

SHOT_CLAIMS=('composition','camera','lighting','action','continuity','audio','image_prompt','video_prompt')


def unknown():
    """A template's absence of evidence stays an explicit human-review task."""
    return {'zh':None,'en':None,'status':'unknown','reason':'待人工观察与确认','confidence':None,'evidence':[]}


def template(source):
    """Generate exact confirmed original-frame intervals; N is not an invented terminal time."""
    cuts=source['confirmed_cut_indices'];end=len(source['frames'])
    return DnaDocument(title='',global_style=unknown(),negative_constraints=unknown(),audio_editing=unknown(),shots=[{'id':f'shot_{i+1:03d}','start_frame':start,'end_frame':cuts[i+1] if i+1<len(cuts) else end,**{key:unknown() for key in SHOT_CLAIMS}} for i,start in enumerate(cuts)]).model_dump()


def claims(data):
    """Yield stable claim paths with local shot ranges and audible-source restrictions."""
    for key in ('global_style','negative_constraints','audio_editing'):yield key,data[key],None,None,key=='audio_editing'
    for asset in data['assets']:
        for key,value in asset['fields'].items():yield f"assets/{asset['id']}/fields/{key}",value,None,None,asset['kind']=='character' and key=='voice'
        yield f"assets/{asset['id']}/lock_prompt",asset['lock_prompt'],None,None,False
    for shot in data['shots']:
        for key in SHOT_CLAIMS:yield f"shots/{shot['id']}/{key}",shot[key],shot['id'],None if key=='continuity' else (shot['start_frame'],shot['end_frame']),key=='audio'
        for group in ('dialogues','events'):
            for item in shot[group]:yield f"shots/{shot['id']}/{group}/{item['id']}/claim",item['claim'],shot['id'],(shot['start_frame'],shot['end_frame']),False


def unique(values,label):
    """Repeated local identities are errors, not implicit overwrites."""
    if len(values)!=len(set(values)):raise HTTPException(422,label+'不能重复')


def validate_document(document,source):
    """Allow incomplete claims but reject bad source ranges, references and unsupported original audio."""
    try:data=DnaDocument.model_validate(document).model_dump()
    except ValidationError:raise HTTPException(422,'DNA字段、整数、版本或固定资产字段格式无效') from None
    if len(canonical(data).encode())>512*1024:raise HTTPException(422,'DNA文档超过512KiB')
    unique([a['id'] for a in data['assets']],'资产ID');unique([s['id'] for s in data['shots']],'镜头ID')
    assets={a['id']:a for a in data['assets']};n=len(source['frames'])
    for shot in data['shots']:
        if shot['end_frame']>n:raise HTTPException(422,'镜头范围超出原始帧序列')
        unique(shot['character_ids'],'镜头角色');unique(shot['prop_ids'],'镜头道具')
        for kind,identities in [('character',shot['character_ids']),('scene',[shot['scene_id']] if shot['scene_id'] else []),('prop',shot['prop_ids'])]:
            if any(identity not in assets or assets[identity]['kind']!=kind for identity in identities):raise HTTPException(422,'镜头资产引用不存在或类型不符')
        for group in ('dialogues','events'):unique([i['id'] for i in shot[group]],'镜头对白或事件ID')
        for dialogue in shot['dialogues']:
            speaker=dialogue['speaker_id']
            if speaker is not None and speaker not in shot['character_ids']:raise HTTPException(422,'说话角色必须是本镜头引用的角色')
            if dialogue['claim']['status']!='unknown' and (not source['audio_present'] or speaker is None):raise HTTPException(422,'非未知对白需要源音轨和本镜头实际说话角色')
        for event in shot['events']:
            unique(event['character_ids'],'事件角色')
            if any(identity not in shot['character_ids'] for identity in event['character_ids']):raise HTTPException(422,'事件角色必须已被本镜头引用')
    for path,claim,shot_id,interval,audible in claims(data):
        for evidence in claim['evidence']:
            if evidence['end_frame']>n or (interval and not interval[0]<=evidence['start_frame']<evidence['end_frame']<=interval[1]):raise HTTPException(422,'证据区间不在允许的原始帧范围')
        if audible and not source['audio_present'] and claim['status']=='observed':raise HTTPException(422,'无源音轨不能把声音标为原片观察事实')
    return data


def readiness(data,source):
    """Freeze complete structure while keeping unknowns explicit rather than inventing answers."""
    problems=[]
    if not data['title'].strip():problems.append(issue('title_missing','请填写DNA标题','title'))
    if not data['reviewer'].strip() or not data['human_reviewed']:problems.append(issue('human_review','请填写复核人并明确人工复核','reviewer'))
    cuts=source['confirmed_cut_indices'];expected=[(start,cuts[i+1] if i+1<len(cuts) else len(source['frames'])) for i,start in enumerate(cuts)]
    if [(s['start_frame'],s['end_frame']) for s in data['shots']]!=expected:problems.append(issue('shot_coverage','镜头集合及顺序必须恰为原片已确认切点划分','shots'))
    for asset in data['assets']:
        if not asset['name_zh'].strip() or not asset['name_en'].strip():problems.append(issue('asset_names','请填写资产中英文名称',f"assets/{asset['id']}"))
    for path,claim,shot_id,_,_ in claims(data):
        if claim['status']=='unknown':
            valid=bool(claim['reason'].strip()) and claim['zh'] in (None,'') and claim['en'] in (None,'') and claim['confidence'] is None
        else:
            valid=bool((claim['zh'] or '').strip()) and bool((claim['en'] or '').strip()) and claim['confidence'] is not None and bool(claim['evidence']) and (claim['status']!='inferred' or bool(claim['reason'].strip()))
        if not valid:problems.append(issue('claim_incomplete','请补全双语证据声明，或用有原因的显式未知',path,shot_id))
    return problems


async def asset_versions(db,project_id,data,verified=None):
    """Freeze only metadata/byte evidence while separately validating original M2 canonical payloads."""
    frozen={};proof={};media_cache={};total=0
    for item in data['assets']:
        identity=item['production_version_id']
        if not identity:continue
        if not item['binding_note'].strip():raise HTTPException(422,'生产版本人工对应必须有说明')
        version=await db.get(AssetVersion,identity);asset=await db.get(ProductionAsset,version.asset_id) if version else None
        if version is None or asset is None or version.project_id!=project_id or asset.project_id!=project_id or asset.kind!=item['kind']:raise HTTPException(422,'生产版本不存在、不属于本工程或资产类型不符')
        digest=hashlib.sha256(json.dumps(version.payload,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        if digest!=version.content_hash or version.payload.get('kind')!=asset.kind:raise HTTPException(409,'生产资产原版本哈希或类型校验失败')
        ids=version.payload.get('media_ids')
        if not isinstance(ids,list) or any(not isinstance(i,str) for i in ids) or len(ids)!=len(set(ids)):raise HTTPException(409,'生产版本媒体集合无效')
        relationships=list((await db.execute(select(VersionMedia.media_id).where(VersionMedia.version_id==identity,VersionMedia.project_id==project_id))).scalars())
        if set(ids)!=set(relationships):raise HTTPException(409,'生产版本媒体关联缺失或与冻结集合不符')
        media=[]
        for media_id in sorted(ids):
            if media_id not in media_cache:
                row=await db.get(ProductionMedia,media_id)
                if row is None or row.project_id!=project_id:raise HTTPException(409,'生产参考媒体跨工程或缺失')
                total+=row.size
                if len(media_cache)>=128 or total>512*1024*1024:raise HTTPException(409,'生产参考超过128个不同媒体或512MiB')
                evidence,private=await media_evidence(db,project_id,media_id,None if verified is None else verified.get(media_id,{}))
                media_cache[media_id]=evidence;proof[media_id]=private
            media.append(media_cache[media_id])
        frozen[identity]={'version_id':identity,'asset_id':asset.id,'kind':asset.kind,'content_hash':version.content_hash,'media':media}
    return [frozen[k] for k in sorted(frozen)],proof
