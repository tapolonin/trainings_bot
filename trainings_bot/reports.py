"""Connect saved logs to the existing PDF renderer without changing its output."""
from calendar import monthrange
from datetime import date, datetime, time
from decimal import Decimal
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

from .domain import euros, local_today, month_value, training_amount
from .report.render import pdf_name, render


def report_data(store, uid, month):
    """Read SQLite on the calling thread; the renderer only receives plain values."""
    month = month_value(month)
    profile = store.profile(uid)
    if not profile:
        raise ValueError('Complete your profile in setup first.')
    today = local_today(profile['timezone'])
    if month > today.strftime('%Y-%m'):
        raise ValueError('Choose the current month or an earlier month.')
    rows = store.entries(uid, month)
    rate = store.rate(uid, month)
    year, number = map(int, month.split('-'))
    elapsed = today.day if month == today.strftime('%Y-%m') else monthrange(year, number)[1]
    statuses = store.day_statuses(uid, month)
    missing = sum(f'{month}-{day:02d}' not in statuses for day in range(1, elapsed + 1))
    minutes = sum((int(r['end'][:2])-int(r['start'][:2]))*60
                  + int(r['end'][3:])-int(r['start'][3:]) for r in rows)
    amount = sum((training_amount(r['start'], r['end'], rate) for r in rows), Decimal(0))
    return dict(month=month, count=len(rows), missing=missing, minutes=minutes,
                amount=amount, timezone=profile['timezone'],
                render_args=dict(year=year, month=number,
                    entries=[dict(date=date.fromisoformat(r['day']), gym=r['gym'],
                                  start=time.fromisoformat(r['start']), end=time.fromisoformat(r['end'])) for r in rows],
                    **{key:profile[key] for key in ('first_name','last_name','sparte','iban')},
                    rate=rate / 100))


def summary(data):
    hours = format(Decimal(data['minutes']) / 60, '.2f').replace('.', ',')
    return (f"{data['month']} · {data['count']} trainings\n"
            f"{hours} hours · {euros(data['amount'])}")


def make_document(data):
    """Blocking work, run in a worker thread. Temporary files never outlive the job."""
    if not data['count']:
        raise ValueError('There are no trainings to export for this month.')
    args = data['render_args']
    filename = pdf_name(args['year'], args['month'], args['first_name'], args['last_name'])
    if any(c in filename for c in '/\\\x00'):
        raise ValueError('Remove slashes from your first and last name in Settings before exporting.')
    with TemporaryDirectory(prefix='training-report-') as folder:
        path, _ = render(folder, **args)
        content = path.read_bytes()
    stamp = datetime.now(ZoneInfo(data['timezone'])).strftime('%d.%m.%Y %H:%M %Z')
    caption = summary(data) + f'\nGenerated {stamp}'
    if data['missing']:
        caption += f"\nDays without information: {data['missing']}"
    return content, filename, caption
