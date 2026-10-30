"""Synthetic-only ONNX CUDA proof. No account/DB/real-image I/O.

Use an isolated interpreter with onnxruntime-gpu[cuda,cudnn]. The model is read
only; profiling stays under the requested owned evidence directory.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import statistics
import time

from PIL import Image
import onnxruntime as ort

from pixivvault_nicegui.egara_inference import EgaraInference, preprocess, CUDA_OPTIONS


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--provider', choices=('cpu', 'cuda'), default='cuda')
    arguments = parser.parse_args()
    output = arguments.out
    output.mkdir(parents=True, exist_ok=False)
    image = Image.new('RGB', (320, 192), (109, 65, 220))
    with BytesIO() as data:
        image.save(data, format='PNG')
        encoded = data.getvalue()
    times = []
    session_holder = []

    def create(model_path, options, provider):
        options.enable_profiling = True
        options.profile_file_prefix = str(output / 'ort-synthetic')
        session = ort.InferenceSession(str(model_path), options, providers=(
            [('CUDAExecutionProvider', dict(CUDA_OPTIONS)), 'CPUExecutionProvider']
            if provider == 'cuda' else ['CPUExecutionProvider']))
        session_holder.append(session)
        return session

    result = {'mode': arguments.provider, 'runtime': ort.__version__,
              'device_available': ort.get_available_providers(),
              'input_shape': list(preprocess(encoded).shape)}
    start = time.perf_counter()
    interpreter = EgaraInference(arguments.model, provider=arguments.provider,
                                  session_factory=create, threads=2)
    try:
        vectors = {}
        for attempt in range(2):
            t0 = time.perf_counter()
            vectors = interpreter.infer(encoded)
            times.append((time.perf_counter() - t0) * 1000)
        result.update(active=interpreter.provider_active,
                      fallthrough=interpreter.fallback_reason,
                      elapsed_ms=times, total_ms=(time.perf_counter()-start)*1000,
                      dimensions={k: len(v) // 4 for k, v in vectors.items()},
                      sha256={k: sha256(v).hexdigest() for k, v in vectors.items()})
    finally:
        for session in session_holder:
            trace = session.end_profiling()
            if trace:
                events = json.loads(Path(trace).read_text(encoding='utf-8'))
                counts = {}
                duration_us = {}
                for event in events:
                    provider = event.get('args', {}).get('provider')
                    if provider:
                        counts[provider] = counts.get(provider, 0) + 1
                        duration_us[provider] = duration_us.get(provider, 0) + event.get('dur', 0)
                result['execution_event_counts'] = counts
                result['execution_event_time_us'] = duration_us
                result['cuda_kernels_executed'] = counts.get('CUDAExecutionProvider', 0) > 0
        interpreter.close()
    (output / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    # Minimal output, no generated artwork or original input bytes.
    print(json.dumps(result, ensure_ascii=False))
    return 0 if (arguments.provider != 'cuda' or
                 (result['active'] == 'cuda' and result['cuda_kernels_executed'])) else 1


if __name__ == '__main__':
    raise SystemExit(main())
