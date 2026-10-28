"""Pure seven-layer bilingual preparation from fixed DNA; no runtime or provider is imported."""
import copy
from fastapi import HTTPException
from app.core.contracts.video_dna import DnaSnapshotPayload, safe_json
from app.services.script_preparation.service import canonical, content_hash

COMPILER = 'dna-preparation-v1'
MAX_BYTES = 8 * 1024 * 1024
LAYERS = ('GLOBAL_STYLE_PROMPT', 'CHARACTER_LOCK_PROMPT', 'SCENE_LOCK_PROMPT', 'SHOT_IMAGE_PROMPT', 'SHOT_VIDEO_PROMPT', 'NEGATIVE_CONSTRAINTS', 'AUDIO_AND_EDITING_PROMPTS')


def frame_locator(source, index):
    """Map an exact frame boundary; frame N is a sentinel and never an estimated timestamp."""
    frames = source['frames']
    if index == len(frames):
        return {'frame_index': index, 'relative_us': None, 'terminal_sentinel': True, 'reason': '原分析没有记录可靠的末帧结束时间'}
    if type(index) is not int or not 0 <= index < len(frames):
        raise HTTPException(409, 'DNA原始帧引用无效')
    item = frames[index]
    return {'frame_index': item['index'], 'pts': item['pts'], 'time_base_num': item['time_base_num'], 'time_base_den': item['time_base_den'], 'relative_us': item['relative_us'], 'terminal_sentinel': False}


def prepared_claim(claim, path, label, source):
    """Preserve declared bilingual text/status and expand source references without guessing content."""
    return {'path': path, 'label': label, 'status': claim['status'], 'zh': claim['zh'], 'en': claim['en'], 'reason': claim['reason'], 'confidence': claim['confidence'], 'evidence': [{'analysis_id': source['analysis_id'], 'media_id': source['media_id'], 'source_hash': source['sha256'], 'start': frame_locator(source, ref['start_frame']), 'end': frame_locator(source, ref['end_frame'])} for ref in claim['evidence']]}


def make_layer(key, statements):
    """Render known text while retaining unknown statements and their reasons in the same layer."""
    texts = {}
    for language in ('zh', 'en'):
        texts[language] = '\n'.join(f'[{item["label"]}] {item[language]}' for item in statements if item['status'] != 'unknown' and item[language]) or None
    return {'key': key, **texts, 'claims': statements}


def asset_statements(asset, source):
    """Keep all fixed bible attributes and the lock recipe linked to the local analysis identity."""
    prefix = f'/document/assets/{asset["id"]}'
    label = f'{asset["id"]} · {asset["name_zh"]} / {asset["name_en"]}'
    result = [prepared_claim(asset['fields'][key], f'{prefix}/fields/{key}', f'{label} · {key}', source) for key in sorted(asset['fields'])]
    result.append(prepared_claim(asset['lock_prompt'], prefix + '/lock_prompt', label + ' · lock', source))
    return result


def compile_payload(payload, dna_hash):
    """Compile immutable semantic evidence to bounded seven-layer preparation, never an execution request.

    Unknowns, unmapped assets and the unknown terminal clock are explicit issues.
    This function reads no mutable project state, model policy, endpoint or secret.
    """
    safe_json(payload)
    DnaSnapshotPayload.model_validate(payload)
    if content_hash(payload) != dna_hash:
        raise HTTPException(409, 'DNA快照校验失败')
    source, document = payload['source'], payload['document']
    assets = {asset['id']: asset for asset in document['assets']}
    entries, byte_count = [], 0
    for shot in document['shots']:
        prefix = f'/document/shots/{shot["id"]}'
        def statement(key):
            """Associate a shot-local claim with its stable semantic ID path."""
            return prepared_claim(shot[key], f'{prefix}/{key}', key, source)
        def global_statement(key):
            """Associate a shared claim without converting it into an original-author parameter."""
            return prepared_claim(document[key], f'/document/{key}', key, source)
        character = [item for identifier in shot['character_ids'] for item in asset_statements(assets[identifier], source)]
        environment_ids = ([shot['scene_id']] if shot['scene_id'] else []) + shot['prop_ids']
        environment = [item for identifier in environment_ids for item in asset_statements(assets[identifier], source)]
        actions = [statement(key) for key in ('action', 'continuity', 'video_prompt')]
        actions.extend(prepared_claim(event['claim'], f'{prefix}/events/{event["id"]}/claim', f'event {event["id"]} · {",".join(event["character_ids"])}', source) for event in shot['events'])
        audio = [global_statement('audio_editing'), statement('audio')]
        audio.extend(prepared_claim(line['claim'], f'{prefix}/dialogues/{line["id"]}/claim', f'dialogue {line["id"]} · speaker {line["speaker_id"] or "unknown"}', source) for line in shot['dialogues'])
        layers = [make_layer(LAYERS[0], [global_statement('global_style')]), make_layer(LAYERS[1], character), make_layer(LAYERS[2], environment), make_layer(LAYERS[3], [statement(key) for key in ('composition', 'camera', 'lighting', 'image_prompt')]), make_layer(LAYERS[4], actions), make_layer(LAYERS[5], [global_statement('negative_constraints')]), make_layer(LAYERS[6], audio)]
        unknowns = [{'path': item['path'], 'reason': item['reason']} for layer in layers for item in layer['claims'] if item['status'] == 'unknown']
        issues = [{'code': 'unknown_claim', 'message': '保留未知内容：' + item['reason'], 'path': item['path'], 'shot_id': shot['id']} for item in unknowns]
        if not shot['character_ids']:
            issues.append({'code': 'character_unassigned', 'message': '本镜尚未指定分析角色，不自动补人物', 'path': prefix + '/character_ids', 'shot_id': shot['id']})
        if shot['scene_id'] is None:
            issues.append({'code': 'scene_unassigned', 'message': '本镜尚未指定分析场景', 'path': prefix + '/scene_id', 'shot_id': shot['id']})
        for identifier in shot['character_ids'] + environment_ids:
            if assets[identifier]['production_version_id'] is None:
                issues.append({'code': 'asset_unmapped', 'message': '分析资产尚未关联生产版本：' + identifier, 'path': f'/document/assets/{identifier}/production_version_id', 'shot_id': shot['id']})
        end = frame_locator(source, shot['end_frame'])
        if end['relative_us'] is None:
            issues.append({'code': 'terminal_duration_unknown', 'message': '末镜精确结束时间未知，真实执行前须重新核验，不能按帧率估计', 'path': prefix + '/end_frame', 'shot_id': shot['id']})
        entry = {'shot_id': shot['id'], 'start_frame': shot['start_frame'], 'end_frame': shot['end_frame'], 'start_relative_us': frame_locator(source, shot['start_frame'])['relative_us'], 'end_relative_us': end['relative_us'], 'character_ids': shot['character_ids'], 'scene_id': shot['scene_id'], 'prop_ids': shot['prop_ids'], 'layers': layers, 'unknowns': unknowns, 'issues': issues}
        byte_count += len(canonical(entry).encode('utf-8'))
        if byte_count > MAX_BYTES:
            raise HTTPException(409, '完整七层准备清单超过8MiB，请分段整理，内容未被截断')
        entries.append(entry)
    result = {'schema_version': 1, 'compiler_version': COMPILER, 'dna_content_hash': dna_hash, 'source': {key: copy.deepcopy(source[key]) for key in ('analysis_id', 'analysis_revision', 'media_id', 'sha256', 'frame_clock_hash', 'confirmed_cut_indices', 'audio_present', 'terminal_time_known')}, 'asset_versions': copy.deepcopy(payload['asset_versions']), 'entries': entries, 'execution_enabled': False, 'model_qualified': False, 'original_author_prompt': False, 'prompt_provenance': 'human_reconstruction_hypothesis', 'human_declarations_only': True}
    safe_json(result)
    if len(canonical(result).encode('utf-8')) > MAX_BYTES:
        raise HTTPException(409, '完整七层准备清单超过8MiB，内容未被截断')
    return result


def preparation_view(snapshot):
    """Keep snapshot identity outside the semantic hash so inert round trips preserve preparation."""
    payload = compile_payload(snapshot.payload, snapshot.content_hash)
    return {'snapshot_id': snapshot.id, 'dna_content_hash': snapshot.content_hash, 'compiler_version': COMPILER, 'content_hash': content_hash(payload), 'payload': payload}
