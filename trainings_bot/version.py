"""Release information shipped with the bot and shown in Help."""
from pathlib import Path

CHANGELOG = Path(__file__).with_name('CHANGELOG.md').read_text(encoding='utf-8')
LATEST_RELEASE = CHANGELOG.split('\n## ', 1)[1].split('\n## ', 1)[0].strip()
VERSION, _, RELEASE_NOTES = LATEST_RELEASE.partition('\n')


def whats_new():
    return f"What's new in version {VERSION}\n\n{RELEASE_NOTES.strip()}"
