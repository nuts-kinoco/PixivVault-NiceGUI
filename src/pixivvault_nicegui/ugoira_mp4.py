"""Owned CPU codec process; original OpenCV mp4v/CFR settings, explicit limitations."""
from pathlib import Path
import hashlib
import importlib.util
import json
import os
import secrets
import subprocess
import sys
import time
from .managed_downloads import _write, _read_bounded, _safe_path
from .ugoira_gif import MAX_GIF_BYTES, MAX_GIF_SECONDS
from .job_service import JobCancelled
from .ugoira_archive import MAX_ARCHIVE_BYTES, MAX_SINGLE_FRAME_BYTES, inspect_ugoira_archive
from .media_parity_policy import parse_ugoira_manifest


class UgoiraMp4Error(ValueError):
    def __init__(self): super().__init__('mp4_environment_or_conversion_failed')


CODEC_TOKEN_ENV = 'PIXIVVAULT_UGOIRA_CODEC_TOKEN'


def default_codec_python(explicit=None):
    """Keep explicit interpreters; only a bundled app may select itself."""
    if explicit is not None:
        return explicit
    if not getattr(sys, 'frozen', False):
        return None
    try:
        executable = Path(sys.executable)
        if (executable.is_absolute() and executable.is_file()
                and all(importlib.util.find_spec(name) is not None
                        for name in ('cv2', 'numpy', 'PIL'))):
            return executable
    except (ImportError, OSError, ValueError):
        pass
    return None


def codec_command(codec_python, stage):
    executable = Path(codec_python)
    if (getattr(sys, 'frozen', False)
            and executable.resolve() == Path(sys.executable).resolve()):
        return [str(executable), '--internal-ugoira-codec', str(stage)]
    return [str(executable), '-I', '-B',
            str(Path(__file__).with_name('ugoira_mp4_worker.py')), str(stage)]


def mp4_plan(inspection):
    first=inspection.frames[0]
    if (first.width%2 or first.height%2 or any((f.width,f.height)!=(first.width,first.height) for f in inspection.frames)):
        raise UgoiraMp4Error()
    fps=max(1.0,min(60.0,1000.0/max(inspection.duration_ms/len(inspection.frames),10)))
    return dict(width=first.width,height=first.height,fps=fps,
                max_frame_bytes=MAX_SINGLE_FRAME_BYTES,max_archive_bytes=MAX_ARCHIVE_BYTES,
                frames=[dict(file=f.filename,delay=f.delay_ms) for f in inspection.frames])


def render_mp4(raw,inspection,stage,codec_python,checkpoint):
    """Retain incomplete evidence; never delete or overwrite codec output."""
    if codec_python is None or not Path(codec_python).is_absolute() or not Path(codec_python).is_file():
        raise UgoiraMp4Error()
    stage = Path(stage)
    try:
        if not stage.is_absolute() or not stage.is_dir():
            raise ValueError('explicit codec stage required')
        _safe_path(stage)
        if any(stage.iterdir()):
            raise ValueError('codec stage must be fresh')
    except Exception:
        raise UgoiraMp4Error() from None
    plan=mp4_plan(inspection)
    token = secrets.token_hex(16)
    # Reuse the existing input file and keep the owner's six-file reservation.
    plan.update(protocol=1, request_token=token,
                source_sha256=hashlib.sha256(raw).hexdigest())
    _write(stage/'source.zip',raw)
    _write(stage/'plan.json',json.dumps(plan).encode())
    env=os.environ.copy()
    env.update(PYTHONDONTWRITEBYTECODE='1',PYTHONUTF8='1',TEMP=str(stage),TMP=str(stage))
    env[CODEC_TOKEN_ENV] = token
    env.pop('PYTHONPATH',None)
    process=None
    deadline=time.monotonic()+MAX_GIF_SECONDS
    try:
        with (stage/'codec.log').open('xb') as log:
            process=subprocess.Popen(codec_command(codec_python, stage),
                stdin=subprocess.DEVNULL,stdout=log,stderr=log,env=env,cwd=stage,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            while process.poll() is None:
                checkpoint()
                if time.monotonic()>deadline: raise UgoiraMp4Error()
                if (stage/'animation.mp4').exists() and (stage/'animation.mp4').stat().st_size>MAX_GIF_BYTES:
                    raise UgoiraMp4Error()
                time.sleep(0.02)
            checkpoint()
            if process.returncode: raise UgoiraMp4Error()
        data=_read_bounded(stage/'animation.mp4',MAX_GIF_BYTES)
        report=json.loads(_read_bounded(stage/'verification.json',512*1024))
        if not data or report['frame_count']!=len(inspection.frames) or report['codec']!='mp4v':
            raise UgoiraMp4Error()
        decoded=_read_bounded(stage/'decoded_frames.zip',MAX_ARCHIVE_BYTES)
        verify_decoded(decoded,inspection)
        return data,report,decoded
    except JobCancelled: raise
    except Exception: raise UgoiraMp4Error() from None
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

def verify_decoded(data,inspection):
    manifest=parse_ugoira_manifest(dict(originalSrc='https://i.pximg.net/img-zip-ugoira/offline/1_ugoira_decoded.zip',
        frames=[dict(file=f'{i:06d}.png',delay=f.delay_ms) for i,f in enumerate(inspection.frames)]),work_id='1')
    decoded=inspect_ugoira_archive(data,manifest)
    if any((a.width,a.height)!=(b.width,b.height) for a,b in zip(decoded.frames,inspection.frames)):
        raise UgoiraMp4Error()
    return decoded
