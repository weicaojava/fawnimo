"""Stable ID-based semantic dependency comparison over immutable DNA snapshots."""
from fastapi import HTTPException
from app.services.script_preparation.service import canonical


def paths_changed(old, new, path):
    """Return deterministic leaf paths without leaking unrelated before/after document text."""
    if type(old) is type(new) and old == new:
        return []
    if isinstance(old, dict) and isinstance(new, dict):
        result = []
        for key in sorted(set(old) | set(new)):
            if key not in old or key not in new:
                result.append(path + '/' + key)
            else:
                result.extend(paths_changed(old[key], new[key], path + '/' + key))
        return result
    if isinstance(old, list) and isinstance(new, list):
        combined = old + new
        if combined and all(isinstance(item, dict) and isinstance(item.get('id'), str) for item in combined):
            left, right = {item['id']: item for item in old}, {item['id']: item for item in new}
            result = []
            for identifier in sorted(set(left) | set(right)):
                result.extend([path + '/' + identifier] if identifier not in left or identifier not in right else paths_changed(left[identifier], right[identifier], path + '/' + identifier))
            if [item['id'] for item in old] != [item['id'] for item in new]:
                result.append(path + '/$order')
            return result
        result = []
        for index in range(max(len(old), len(new))):
            if index >= len(old) or index >= len(new):
                result.append(path + '/' + str(index))
            else:
                result.extend(paths_changed(old[index], new[index], path + '/' + str(index)))
        return result
    return [path]


def references(document):
    """Index both old and new references, so removing a dependency does not hide its former users."""
    result = {asset['id']: set() for asset in document['assets']}
    for shot in document['shots']:
        identifiers = shot['character_ids'] + shot['prop_ids'] + ([shot['scene_id']] if shot['scene_id'] else [])
        for identifier in identifiers:
            result.setdefault(identifier, set()).add(shot['id'])
    return result


def compare_snapshots(base, other):
    """Describe scoped semantic changes; never patch a draft, submit a task or change current adoption."""
    old, new = base.payload, other.payload
    if (old['source']['sha256'], old['source']['frame_clock_hash']) != (new['source']['sha256'], new['source']['frame_clock_hash']):
        raise HTTPException(422, '只能比较同一原片和实际帧时钟的DNA版本')
    old_doc, new_doc = old['document'], new['document']
    old_shots = {shot['id']: shot for shot in old_doc['shots']}
    new_shots = {shot['id']: shot for shot in new_doc['shots']}
    ordered = list(dict.fromkeys([shot['id'] for shot in old_doc['shots']] + [shot['id'] for shot in new_doc['shots']]))
    old_refs, new_refs = references(old_doc), references(new_doc)
    union = {key: old_refs.get(key, set()) | new_refs.get(key, set()) for key in set(old_refs) | set(new_refs)}
    changed, reasons, byte_count = [], [], 0
    def record(paths, targets, reason):
        """Attach a precise reason for each affected field/shot without inventing quality scores."""
        nonlocal byte_count
        changed.extend(paths)
        for path in paths:
            for identifier in ordered:
                if identifier not in targets:
                    continue
                item = {'shot_id': identifier, 'path': path, 'reason': reason}
                byte_count += len(canonical(item).encode('utf-8'))
                if byte_count > 2 * 1024 * 1024:
                    raise HTTPException(409, '完整版本差异超过2MiB，请比较相邻修订；报告未被截断')
                reasons.append(item)
    for key in ('title', 'reviewer', 'human_reviewed', 'schema_version'):
        record(paths_changed(old_doc[key], new_doc[key], '/document/' + key), [], 'metadata')
    for key in ('global_style', 'negative_constraints', 'audio_editing'):
        record(paths_changed(old_doc[key], new_doc[key], '/document/' + key), ordered, '全局风格或约束变化')
    old_assets = {asset['id']: asset for asset in old_doc['assets']}
    new_assets = {asset['id']: asset for asset in new_doc['assets']}
    for identifier in sorted(set(old_assets) | set(new_assets)):
        path = '/document/assets/' + identifier
        diff = [path] if identifier not in old_assets or identifier not in new_assets else paths_changed(old_assets[identifier], new_assets[identifier], path)
        record(diff, union.get(identifier, set()), '分析资产或固定生产版本变化（旧新引用并集）')
    for identifier in ordered:
        path = '/document/shots/' + identifier
        diff = [path] if identifier not in old_shots or identifier not in new_shots else paths_changed(old_shots[identifier], new_shots[identifier], path)
        record(diff, [identifier], '镜头内容、证据、对白、事件或时间范围变化')
    source_diff = paths_changed(old['source'], new['source'], '/source')
    record(source_diff, ordered, '分析确认边界或来源修订变化')
    old_versions = {item['version_id']: item for item in old['asset_versions']}
    new_versions = {item['version_id']: item for item in new['asset_versions']}
    version_diff = paths_changed(old_versions, new_versions, '/asset_versions')
    # Version metadata normally follows a document binding change. For a
    # historical byte-evidence change, invalidate only users of that version.
    if version_diff:
        differing = {key for key in set(old_versions) | set(new_versions) if old_versions.get(key) != new_versions.get(key)}
        users = set()
        for asset in old_doc['assets'] + new_doc['assets']:
            if asset['production_version_id'] in differing:
                users.update(union.get(asset['id'], set()))
        record(version_diff, users, '固定生产版本字节证据变化')
    affected = {item['shot_id'] for item in reasons}
    result = {'base_snapshot_id': base.id, 'other_snapshot_id': other.id, 'changed_paths': sorted(set(changed)), 'affected_shot_ids': [identifier for identifier in ordered if identifier in affected], 'dependency_index': {key: [identifier for identifier in ordered if identifier in union[key]] for key in sorted(union)}, 'reasons': reasons, 'execution_enabled': False}
    if len(canonical(result).encode('utf-8')) > 2 * 1024 * 1024:
        raise HTTPException(409, '完整版本差异超过2MiB，请比较相邻修订；报告未被截断')
    return result
