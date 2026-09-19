"""Wire the private hosting common.php to the versioned session implementation.

Does not print configuration or include it in Git. Leaves the original unchanged
and writes a separate private deployment copy. Run outside the public web root.
"""
import argparse
import os
import re
from pathlib import Path

REPLACEMENT = """function start_app_session(): void
{
    require_once __DIR__ . '/app_session.php';
    start_persistent_app_session();
}"""


def patch(source):
    pattern = r'function start_app_session\(\): void\n\{\n.*?\n\}'
    matches = list(re.finditer(pattern, source, flags=re.DOTALL))
    if len(matches) != 1:
        raise ValueError('Expected exactly one start_app_session function')
    return source[:matches[0].start()] + REPLACEMENT + source[matches[0].end():]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    source = args.source.read_text()
    updated = patch(source)
    descriptor = os.open(args.destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w') as handle:
        handle.write(updated)
    print('Created deployment copy; original configuration unchanged')


if __name__ == '__main__':
    main()
