"""Allow only runtime essentials into original local-media helpers; model/configuration variables are excluded by default."""
import os


def media_environment():
    """Keep absolute Python/FFmpeg usable while excluding all host/workspace credentials and private config paths."""
    allowed = {'SYSTEMROOT', 'WINDIR', 'SYSTEMDRIVE', 'TEMP', 'TMP', 'TMPDIR', 'LANG', 'LC_ALL', 'TZ'}
    result = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    result.update(PYTHONNOUSERSITE='1', PYTHONUNBUFFERED='1', PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1',
                  JELLYFISH_SKIP_ENV_FILE='1')
    return result
