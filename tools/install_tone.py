"""Download the pinned T-one ONNX model; no KenLM or runtime dependencies added."""
import argparse
import hashlib
import json
import shutil
import tempfile
import urllib.request
from pathlib import Path

REVISION = '106f3b0b32a9e107eb613312e4ebc61ff3d53926'
HASHES = {
    'model.onnx': '707e4a282d5036304a0b8603ad83945840c3075f53c49911ef88c19bcc5a7d52',
    'config.json': '8a6272e21a05301afac2686f6066554151f9cd85bd0d817297339e8c3dc778a6',
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/opt/sms/var/models/tone'))
    parser.add_argument('--source', type=Path, help='Previously downloaded files; checksums are still required')
    args = parser.parse_args()
    if args.root.exists():
        parser.error('Destination already exists; use a new --root for a separate installation')
    args.root.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.tone-', dir=args.root.parent) as temporary:
        stage = Path(temporary)
        directory = stage / 'model'
        directory.mkdir()
        for name, expected in HASHES.items():
            path = directory / name
            if args.source:
                shutil.copyfile(args.source / name, path)
            else:
                urllib.request.urlretrieve(
                    f'https://huggingface.co/t-tech/T-one/resolve/{REVISION}/{name}', path)
            with path.open('rb') as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != expected:
                    raise ValueError(f'SHA-256 mismatch: {name}')
        (directory / 'installation.json').write_text(json.dumps({
            'repository': 't-tech/T-one', 'revision': REVISION,
            'sha256': HASHES, 'decoder': 'greedy (no language model)',
        }, indent=2) + '\n')
        directory.rename(args.root)
    print(f'Installed T-one: {args.root}')


if __name__ == '__main__':
    main()
