"""Audit Git's public file list without printing matched private values."""
import pathlib
import re
import subprocess


def check():
    files = subprocess.check_output(['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z']).decode().split('\0')
    bad = []
    for name in filter(None, files):
        path = pathlib.Path(name)
        if (path.suffix.lower() in {'.xlsx', '.pdf', '.sqlite', '.sqlite3', '.pem', '.key'}
                or path.parts[0] in {'data', 'Templates', 'spike', 'backups', '.aws', '.codex', '.agents'}
                or path.name.startswith('.env') and path.name != '.env.example'):
            bad.append(name)
            continue
        content = path.read_bytes()
        if re.search(rb'\b\d{8,12}:[A-Za-z0-9_-]{30,}\b', content) or (b'-----BEGIN ' + b'PRIVATE KEY-----') in content:
            bad.append(name)
    if bad:
        raise SystemExit('Review prohibited/private content in: ' + ', '.join(bad))
    print('Public tracked-file audit passed. Manual review is still required.')


if __name__ == '__main__':
    check()
