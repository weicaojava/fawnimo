"""Bounded isolated video analysis; no decoding runs in the application process."""
import json
import sys
from pathlib import Path
from app.core.owned_process import run_owned_process


async def probe(path, output_dir, *, trusted_ffmpeg, deadline_seconds=90) -> dict:
    """Analyze original frame timestamps in a killable trusted child process."""
    if not 0 < deadline_seconds <= 90:raise ValueError('Probe deadline must be within 90 seconds')
    source=Path(path).resolve();output=Path(output_dir).resolve();ffmpeg=Path(trusted_ffmpeg).resolve()
    if not source.is_file() or source.stat().st_size>64*1024*1024:raise ValueError('Input missing or exceeds 64MB')
    if not ffmpeg.is_file():raise ValueError('Trusted FFmpeg executable missing')
    if output==source or output in source.parents:raise ValueError('Output must be a separate directory')
    output.mkdir(parents=True,exist_ok=True)
    if any(output.iterdir()):raise ValueError('Probe output directory must be empty')
    child=Path(__file__).with_name('video_probe_child.py')
    parameters={'path':str(source),'output_dir':str(output),'ffmpeg':str(ffmpeg),'deadline':deadline_seconds}
    stdout,returncode=await run_owned_process([sys.executable,str(child)],json.dumps(parameters).encode(),timeout=deadline_seconds+1)
    result=json.loads(stdout)
    if not isinstance(result,dict):raise ValueError('Video probe returned an invalid result')
    if returncode or 'error' in result:raise ValueError(result.get('error','Video probe failed'))
    return result
