"""CPU-only OpenCV worker matching the original MP4 path (no format fallback)."""
from __future__ import annotations
import hashlib
import hmac
import json
from io import BytesIO
import math
import os
from pathlib import Path
import re
import stat
import sys
import zipfile


TOKEN_ENV = 'PIXIVVAULT_UGOIRA_CODEC_TOKEN'


def _reject_links(path):
    for item in (path, *path.parents):
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('codec path contains a link')


def _read_regular(path, limit):
    _reject_links(path)
    with path.open('rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 0 < info.st_size <= limit:
            raise ValueError('invalid codec input')
        data = stream.read(limit + 1)
    if len(data) != info.st_size or len(data) > limit:
        raise ValueError('codec input changed')
    return data


def _request(folder):
    root = Path(folder)
    if not root.is_absolute() or root == root.parent or '..' in root.parts:
        raise ValueError('explicit codec stage required')
    _reject_links(root)
    if not root.is_dir():
        raise ValueError('codec stage is not a directory')
    if {p.name for p in root.iterdir()} != {'source.zip', 'plan.json', 'codec.log'}:
        raise ValueError('codec stage is not a fresh request')
    _reject_links(root / 'codec.log')
    info = (root / 'codec.log').lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError('invalid codec log')
    token = os.environ.pop(TOKEN_ENV, '')
    if re.fullmatch(r'[a-f0-9]{32}', token) is None:
        raise ValueError('codec request token missing')
    plan = json.loads(_read_regular(root / 'plan.json', 512 * 1024))
    fields = {'width', 'height', 'fps', 'max_frame_bytes', 'max_archive_bytes',
              'frames', 'protocol', 'request_token', 'source_sha256'}
    if (type(plan) is not dict or set(plan) != fields
            or type(plan['protocol']) is not int or plan['protocol'] != 1
            or type(plan['request_token']) is not str
            or not hmac.compare_digest(plan['request_token'], token)):
        raise ValueError('codec request token mismatch')
    if any(type(plan[key]) is not int or plan[key] <= 0
           for key in ('width', 'height', 'max_frame_bytes', 'max_archive_bytes')):
        raise ValueError('invalid codec dimensions or budget')
    # Standalone -I worker cannot import the application package. These caps
    # mirror ugoira_archive/media_parity_policy and cannot be raised by a plan.
    if (plan['max_frame_bytes'] > 8 * 1024 * 1024
            or plan['max_archive_bytes'] > 64 * 1024 * 1024
            or plan['width'] * plan['height'] > 16_000_000
            or plan['width'] % 2 or plan['height'] % 2
            or type(plan['fps']) not in (int, float)
            or not math.isfinite(plan['fps']) or not 1 <= plan['fps'] <= 60):
        raise ValueError('invalid codec limits or frame rate')
    frames = plan['frames']
    if (type(frames) is not list or not 1 <= len(frames) <= 3000
            or any(type(frame) is not dict or set(frame) != {'file', 'delay'}
                   or type(frame['file']) is not str or not frame['file']
                   or type(frame['delay']) is not int or not 0 < frame['delay'] <= 60_000
                   for frame in frames)
            or len({frame['file'] for frame in frames}) != len(frames)
            or len(frames) * plan['width'] * plan['height'] > 512_000_000
            or sum(frame['delay'] for frame in frames) > 3 * 60 * 60 * 1000):
        raise ValueError('invalid codec frame plan')
    expected_fps = max(1.0, min(60.0, 1000.0 / max(
        sum(frame['delay'] for frame in frames) / len(frames), 10)))
    if abs(plan['fps'] - expected_fps) > 1e-9:
        raise ValueError('codec timing differs from source')
    if (type(plan['source_sha256']) is not str
            or re.fullmatch(r'[a-f0-9]{64}', plan['source_sha256']) is None):
        raise ValueError('invalid codec source identity')
    raw = _read_regular(root / 'source.zip', plan['max_archive_bytes'])
    if not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), plan['source_sha256']):
        raise ValueError('codec source changed')
    return root, plan, raw


def main(folder):
    root, plan, raw = _request(folder)
    # Exclusive creation also prevents another child from adopting this job.
    with (root / 'verification.json').open('x', encoding='utf-8') as verification:
        target = root / 'animation.mp4'
        with target.open('xb'):
            pass
        _reject_links(target)
        import cv2
        import numpy as np
        from PIL import Image
        cv2.setNumThreads(1)
        cv2.ocl.setUseOpenCL(False)
        width, height, fps = plan['width'], plan['height'], plan['fps']
        writer = cv2.VideoWriter(str(target), cv2.VideoWriter_fourcc(*'mp4v'), fps, (width, height))
        if not writer.isOpened():
            writer.release()
            raise ValueError('MP4 encoder unavailable')
        try:
            with zipfile.ZipFile(BytesIO(raw)) as archive:
                for frame in plan['frames']:
                    info = archive.getinfo(frame['file'])
                    if not 0 < info.file_size <= plan['max_frame_bytes']:
                        raise ValueError('source frame budget')
                    with Image.open(BytesIO(archive.read(info))) as image:
                        if image.size != (width, height):
                            raise ValueError('frame dimensions differ')
                        image.load()
                        writer.write(cv2.cvtColor(np.array(image.convert('RGBA')), cv2.COLOR_RGBA2BGR))
        finally:
            writer.release()
        capture = cv2.VideoCapture(str(target))
        if not capture.isOpened():
            capture.release()
            raise ValueError('MP4 decode unavailable')
        count, samples = 0, []
        actual_fps = capture.get(cv2.CAP_PROP_FPS)
        try:
            with zipfile.ZipFile(root / 'decoded_frames.zip', 'x', compression=zipfile.ZIP_STORED) as decoded:
                while True:
                    ok, frame = capture.read()
                    if not ok:
                        break
                    if frame.shape[:2] != (height, width):
                        raise ValueError('MP4 dimensions changed')
                    samples.append([float(value) for value in frame.mean(axis=(0, 1))[::-1]])
                    png = BytesIO()
                    Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)).save(png, format='PNG')
                    if len(png.getvalue()) > plan['max_frame_bytes']:
                        raise ValueError('decoded frame budget')
                    decoded.writestr(f'{count:06d}.png', png.getvalue())
                    if decoded.fp.tell() > plan['max_archive_bytes']:
                        raise ValueError('decoded archive budget')
                    count += 1
                    if count > len(plan['frames']):
                        raise ValueError('extra encoded frame')
        finally:
            capture.release()
        if count != len(plan['frames']) or abs(actual_fps - fps) > max(0.001, fps * 0.001):
            raise ValueError('MP4 timing mismatch')
        result = dict(codec='mp4v', width=width, height=height, frame_count=count, fps=actual_fps,
            source_duration_ms=sum(row['delay'] for row in plan['frames']),
            encoded_duration_ms=1000 * count / actual_fps, mean_rgb=samples,
            timing='legacy-average-delay-cfr-clamped-1-60', alpha='discarded-as-original')
        verification.write(json.dumps(result, sort_keys=True))

if __name__=='__main__':
    try: main(sys.argv[1])
    except Exception:
        print('MP4 encode/verification failed',file=sys.stderr)
        sys.exit(1)
