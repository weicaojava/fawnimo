"""Self-deadlined exact-frame FFmpeg child, also bounded after parent death."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading


def main():
    """Read private stdin parameters and kill/join FFmpeg on a local watchdog."""
    options=json.loads(sys.stdin.buffer.read());deadline=float(options['deadline'])
    if not 0<deadline<=45 or type(options['index']) is not int or not 0<=options['index']<7200:sys.exit(2)
    source=Path(options['source']).resolve();output=Path(options['output']).resolve();binary=Path(options['ffmpeg']).resolve()
    if not source.is_file() or not binary.is_file() or source==output:sys.exit(2)
    active=[None]
    def expire():
        """Terminate and join the decoder before the watchdog process exits."""
        process=active[0]
        if process is not None:
            try:process.kill();process.wait(timeout=2)
            except Exception:pass
        os._exit(124)
    timer=threading.Timer(deadline,expire);timer.daemon=True;timer.start()
    try:
        active[0]=subprocess.Popen([str(binary),'-nostdin','-v','error','-threads','1','-protocol_whitelist','file,pipe','-enable_drefs','0','-i',str(source),'-vf',f"select=eq(n\\,{options['index']}),scale=480:480:force_original_aspect_ratio=decrease",'-frames:v','1','-q:v','3','-fs','512000','-y',str(output)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        active[0].wait()
        if active[0].returncode:sys.exit(2)
    finally:
        process=active[0]
        if process is not None and process.poll() is None:process.kill();process.wait()
        timer.cancel()


if __name__=='__main__':main()
