"""Install pinned Parakeet Q8 weights and transcribe.cpp CPU runtime.

Does not install Python packages or change the chosen transcription backend.
"""
from __future__ import annotations

import argparse
import json
import platform
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.install_nemotron import extract_runtime, verify

VERSION = '0.2.3'
RUNTIME_HASHES = {
    'aarch64': '823081680364643d7afd6e294a879879311ed241a9713beec77185a1000ff773',
    'x86_64': 'a35b3c6eb220b825a5bc47ce265b7613be50dbdd7ec487f65c1c86c0e6ff9287',
}
MODEL_FILE = 'parakeet-tdt-0.6b-v3-Q8_0.gguf'
MODEL_REVISION = 'd8d8c32cb2017c57e5307767343e97870f8a7f53'
MODEL_HASH = '5859f77944efcd8eafa23a6350731960b2b55b2203df51f319665c807d802cc7'
MODEL_URL = f'https://huggingface.co/handy-computer/parakeet-tdt-0.6b-v3-gguf/resolve/{MODEL_REVISION}/{MODEL_FILE}'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/opt/sms/var/models/parakeet'))
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--model-file', type=Path)
    args = parser.parse_args()
    arch = platform.machine()
    if platform.system() != 'Linux' or arch not in RUNTIME_HASHES:
        parser.error('Supported platforms: Linux aarch64 and x86_64')
    args.root.mkdir(parents=True, exist_ok=True)
    runtime = args.root / 'runtime'
    if runtime.exists():
        parser.error('Runtime already exists; use a new --root for a separate installation')
    name = f'transcribe-native-{VERSION}-linux-{arch}-cpu-vulkan.tar.gz'
    with tempfile.TemporaryDirectory(prefix='.install-', dir=args.root) as temporary:
        stage = Path(temporary)
        archive = args.archive or stage / name
        if not args.archive:
            urllib.request.urlretrieve(f'https://github.com/handy-computer/transcribe.cpp/releases/download/v{VERSION}/{name}', archive)
        verify(archive, RUNTIME_HASHES[arch])
        model = args.model_file or stage / MODEL_FILE
        if not args.model_file:
            urllib.request.urlretrieve(MODEL_URL, model)
        verify(model, MODEL_HASH)
        extract_runtime(archive, stage)
        source = stage / f'transcribe-native-linux-{arch}-cpu-vulkan'
        if not (source / 'libtranscribe.so').is_file():
            raise ValueError('Missing native library')
        destination = args.root / MODEL_FILE
        if destination.exists():
            verify(destination, MODEL_HASH)
        else:
            shutil.copyfile(model, stage / 'model.verified')
            (stage / 'model.verified').replace(destination)
        source.rename(runtime)
        (args.root / 'installation.json').write_text(json.dumps(dict(
            runtime=VERSION, architecture=arch, runtime_sha256=RUNTIME_HASHES[arch],
            model_revision=MODEL_REVISION, model_sha256=MODEL_HASH, source=MODEL_URL), indent=2)+'\n')
    print(f'Installed Parakeet: {args.root}')


if __name__ == '__main__':
    main()
