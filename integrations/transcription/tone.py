"""T-one's 8 kHz Russian telephony model, with greedy CTC decoding on CPU."""
from __future__ import annotations

from pathlib import Path

MODEL_NAME = 't-tech/t-one'
DEFAULT_MODEL_PATH = '/opt/sms/var/models/tone'


def load_model(model_path: str | None, cpu_threads: int):
    import onnx_asr
    import onnxruntime

    model_path = model_path or DEFAULT_MODEL_PATH
    if not all((Path(model_path) / name).is_file() for name in ('model.onnx', 'config.json')):
        raise RuntimeError('T-one не установлен. Запустите tools/install_tone.py на Raspberry.')
    options = onnxruntime.SessionOptions()
    options.intra_op_num_threads = cpu_threads
    options.inter_op_num_threads = 1
    return onnx_asr.load_model(
        MODEL_NAME, model_path, quantization=None,
        sess_options=options, providers=['CPUExecutionProvider'],
    ).with_timestamps()
