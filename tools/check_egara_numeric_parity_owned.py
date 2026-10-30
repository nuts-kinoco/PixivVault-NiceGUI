"""Numerical compatibility of original EgaraNet on CPU vs CUDA, synthetic only."""
from __future__ import annotations

import argparse
from io import BytesIO
import json
from pathlib import Path
import time

import numpy as np
from PIL import Image

from pixivvault_nicegui.egara_inference import EgaraInference, EGARA_BB_SHA256


def synthetic():
    img = Image.new('RGB', (320, 192))
    pixels = np.empty((192, 320, 3), dtype=np.uint8)
    yy, xx = np.indices((192, 320))
    pixels[:, :, 0] = (xx + yy) % 256
    pixels[:, :, 1] = (2 * xx + yy // 2) % 256
    pixels[:, :, 2] = (xx // 3 + 3 * yy) % 256
    img = Image.fromarray(pixels, 'RGB')
    with BytesIO() as stream:
        img.save(stream, format='PNG')
        return stream.getvalue()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise FileExistsError('preserve previous numerical acceptance')
    src = synthetic()
    results = {}
    reports = {}
    for provider in ('cpu', 'cuda'):
        start = time.perf_counter()
        runtime = EgaraInference(args.model, provider=provider)
        try:
            vectors = runtime.infer(src)
            reports[provider] = {
                'active': runtime.provider_active,
                'load_and_first_infer_ms': (time.perf_counter() - start) * 1000,
                'model_sha256': runtime.model_sha256,
            }
            results[provider] = {k: np.frombuffer(v, dtype='<f4').copy()
                                 for k, v in vectors.items()}
        finally:
            runtime.close()
    matched = {}
    for kind in ('style', 'composition'):
        cpu, gpu = results['cpu'][kind], results['cuda'][kind]
        delta = np.abs(cpu.astype(np.float64) - gpu.astype(np.float64))
        cos = float(np.dot(cpu, gpu) / (np.linalg.norm(cpu) * np.linalg.norm(gpu)))
        matched[kind] = {
            'dim': int(cpu.size),
            'max_abs_diff': float(delta.max()),
            'mean_abs_diff': float(delta.mean()),
            'cosine_cpu_gpu': cos,
            'passes': bool(cpu.shape == gpu.shape and cos >= 0.999
                           and delta.max() <= 0.02),
        }
    out = {
        'synthetic_only': True, 'model_sha256': EGARA_BB_SHA256,
        'providers': reports, 'parity': matched,
        'passed': all(value['passes'] for value in matched.values())
    }
    target.write_text(json.dumps(out, indent=2), encoding='utf-8')
    print(json.dumps(out))
    return 0 if out['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
