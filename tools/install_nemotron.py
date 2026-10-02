"""Install the pinned NVIDIA CPU runtime and Q8 ASR model, without pip changes.

Run as the bot's service user. Sources and SHA-256 values are pinned to the
official NeMo-Speech.cpp 0.1.0 release and its model index. No audio is uploaded.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path

VERSION = '0.1.0'
RUNTIME_HASHES = {
    'aarch64': '0e4112255d566de7bdd142f239e984995c4447103ba8feb41f2bb5c559d561d3',
    'x86_64': '0f74131d631ad2c694cf0ec53490866bb6461147959589a69fb6fc231944065b',
}
MODEL_FILE = 'nemotron-3.5-asr-streaming-0.6b.q8_0.gguf'
MODEL_REVISION = '1c8deaecc64b91f034d73e08dd8b64625eb3395d'
MODEL_HASH = 'a5c435f294eea8f88ce68dd27b8c3bfea7f777cb2fbba04fcd30eaa555f429ae'
MODEL_URL = f'https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b/resolve/{MODEL_REVISION}/{MODEL_FILE}'


def verify(path: Path, expected: str):
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if digest != expected:
        raise ValueError(f'SHA-256 mismatch: {path.name}')


def extract_runtime(archive: Path, destination: Path):
    # Python 3.11 on Raspberry OS predates tarfile's data filter. Validate all
    # paths and links before extracting the checksum-verified release archive.
    root = destination.resolve()
    with tarfile.open(archive) as source:
        for member in source.getmembers():
            target = (root / member.name).resolve()
            if not target.is_relative_to(root) or member.isdev() or member.isfifo():
                raise ValueError('Unsafe runtime archive member')
            if member.issym() or member.islnk():
                link = (target.parent if member.issym() else root) / member.linkname
                if not link.resolve().is_relative_to(root):
                    raise ValueError('Unsafe runtime archive link')
        source.extractall(root)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/opt/sms/var/models/nemotron'))
    parser.add_argument('--archive', type=Path, help='Previously downloaded runtime archive')
    parser.add_argument('--model-file', type=Path, help='Previously downloaded model')
    args = parser.parse_args()
    arch = platform.machine()
    if platform.system() != 'Linux' or arch not in RUNTIME_HASHES:
        parser.error('This installer supports Linux aarch64 and x86_64')
    args.root.mkdir(parents=True, exist_ok=True)
    runtime = args.root / 'runtime'
    if runtime.exists():
        parser.error(f'{runtime} already exists; use a new --root for a separate installation')
    name = f'nemo-speech-{VERSION}-linux-{arch}-cpu'
    with tempfile.TemporaryDirectory(prefix='.install-', dir=args.root) as temporary:
        stage = Path(temporary)
        archive = args.archive or stage / (name + '.tar.gz')
        if not args.archive:
            urllib.request.urlretrieve(
                f'https://github.com/NVIDIA/NeMo-Speech.cpp/releases/download/v{VERSION}/{archive.name}', archive)
        verify(archive, RUNTIME_HASHES[arch])
        model = args.model_file or stage / MODEL_FILE
        if not args.model_file:
            urllib.request.urlretrieve(MODEL_URL, model)
        verify(model, MODEL_HASH)
        extract_runtime(archive, stage)
        destination = args.root / MODEL_FILE
        if destination.exists():
            verify(destination, MODEL_HASH)
        else:
            shutil.copyfile(model, stage / 'model.verified')
            (stage / 'model.verified').replace(destination)
        (stage / name).rename(runtime)
        (args.root / 'installation.json').write_text(json.dumps({
            'runtime': VERSION, 'architecture': arch, 'runtime_sha256': RUNTIME_HASHES[arch],
            'model_revision': MODEL_REVISION, 'model_sha256': MODEL_HASH,
            'source': MODEL_URL,
        }, indent=2) + '\n')
    print(f'Installed NVIDIA Nemotron 3.5 ASR: {args.root}')


if __name__ == '__main__':
    main()
