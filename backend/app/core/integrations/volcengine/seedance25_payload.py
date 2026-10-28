"""Pure strict text-only S25A wire builder shared by preview and durable transport."""
from __future__ import annotations

from app.core.contracts.seedance25 import MODEL_ID, OUTPUT_PROFILE, SUPPORTED_DURATIONS


def build_seedance25_body(snapshot: dict) -> dict:
    """Preserve the frozen prompt/duration while refusing every unsupported input.

    This function performs no network, configuration lookup or task mutation.
    Errors contain fixed messages so unknown values cannot leak into previews.
    """
    if not isinstance(snapshot, dict):
        raise TypeError('S25A request must be an object')
    if set(snapshot) - {'model_id', 'duration', 'references', 'prompt', 'prompt_revision'}:
        raise ValueError('S25A request contains unsupported fields')
    if snapshot.get('model_id') != MODEL_ID:
        raise ValueError('S25A requires the exact supported model')
    duration = snapshot.get('duration')
    if type(duration) is not int or duration not in SUPPORTED_DURATIONS:
        raise ValueError('S25A duration must be an integer from 4 to 30 seconds')
    prompt = snapshot.get('prompt')
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 32000:
        raise ValueError('S25A prompt must contain 1 to 32000 characters')
    references = snapshot.get('references', [])
    if not isinstance(references, list) or references:
        raise ValueError('S25A currently supports text only; references must be empty')
    revision = snapshot.get('prompt_revision')
    if revision is not None and (not isinstance(revision, str) or not revision
                                 or len(revision) > 128):
        raise ValueError('S25A prompt revision is invalid')
    try:
        prompt.encode('utf-8')
        if revision is not None:
            revision.encode('utf-8')
    except UnicodeError:
        raise ValueError('S25A request contains invalid Unicode') from None
    return {
        'model': MODEL_ID,
        'content': [{'type': 'text', 'text': prompt}],
        **OUTPUT_PROFILE,
        'duration': duration,
    }
