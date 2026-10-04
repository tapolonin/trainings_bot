#!/usr/bin/env bash
# Installed by the operator as /usr/local/sbin/deploy-trainings-bot.
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'Run as root.' >&2; exit 1; }
sha=${1:?commit SHA required}
archive=${2:?archive path required}
[[ $sha =~ ^[0-9a-f]{40}$ ]] || exit 2
exec 9>/run/lock/trainings-bot-deploy.lock
flock -n 9 || { echo 'Another deployment is running.' >&2; exit 1; }
base=/opt/trainings-bot
release="$base/releases/$sha"
test -f /etc/trainings-bot/bot.env
test -f /var/lib/trainings-bot/assets/template.xlsx
test -f /var/lib/trainings-bot/assets/instructions.pdf
test ! -e "$release"
install -d -m 755 "$release"
# Reject links and paths outside the release before extracting an uploaded archive.
python3 - "$archive" "$release" <<'PY'
import pathlib, sys, tarfile
with tarfile.open(sys.argv[1]) as archive:
    for member in archive.getmembers():
        path = pathlib.PurePosixPath(member.name)
        if path.is_absolute() or '..' in path.parts or not (member.isfile() or member.isdir()):
            raise SystemExit('Unsafe archive member')
        if path.parts[0] not in ('trainings_bot', 'requirements.txt', 'deploy'):
            raise SystemExit('Unexpected archive member')
    archive.extractall(sys.argv[2], filter='data')
PY
ln -s /var/lib/trainings-bot/assets "$release/assets"
chown -R trainingbot:trainingbot "$release"
runuser -u trainingbot -- python3 -m venv "$release/.venv"
runuser -u trainingbot -- "$release/.venv/bin/python" -m pip install -r "$release/requirements.txt"
runuser -u trainingbot -- "$release/.venv/bin/python" -m compileall -q "$release/trainings_bot"
previous=$(readlink -f "$base/current" || true)
activated=false
recover() {
    code=$?
    if [[ $code != 0 ]]; then
        if [[ $activated == true ]]; then
            systemctl stop trainings-bot || true
            if [[ -n $previous && -d $previous ]]; then
                ln -sfn "$previous" "$base/current.next"
                mv -Tf "$base/current.next" "$base/current"
                systemctl start trainings-bot || true
            fi
        elif [[ -n $previous && -d $previous ]]; then
            systemctl start trainings-bot || true
        fi
        echo 'Deployment failed. Database was not rolled back; inspect service and backup.' >&2
    fi
    exit "$code"
}
trap recover EXIT
systemctl stop trainings-bot
if [[ -f /var/lib/trainings-bot/trainings.sqlite3 ]]; then
    umask 077
    sqlite3 /var/lib/trainings-bot/trainings.sqlite3 ".backup '/var/backups/trainings-bot/pre-$sha.sqlite3'"
fi
ln -sfn "$release" "$base/current.next"
mv -Tf "$base/current.next" "$base/current"
activated=true
systemctl start trainings-bot
# This checks process stability, not Telegram end-to-end readiness.
sleep 15
systemctl is-active --quiet trainings-bot
test "$(systemctl show trainings-bot -p NRestarts --value)" = 0
trap - EXIT
echo "Activated $sha"
