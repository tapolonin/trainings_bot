"""Validation shared by setup, settings, and training entry."""
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_GYMS = ['Herschelschule', 'F.-N. Schule', 'Glücksburger Weg', 'Polizeihalle']


def training_amount(start, end, cents):
    """Unrounded euros, so monthly totals use the same calculation as the form."""
    start_hour, start_minute = map(int, start.split(':'))
    end_hour, end_minute = map(int, end.split(':'))
    minutes = (end_hour - start_hour) * 60 + end_minute - start_minute
    return Decimal(minutes) * Decimal(cents) / Decimal(6000)


def euros(amount):
    rounded = Decimal(amount).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return format(rounded, ',.2f').translate(str.maketrans(',.', '.,')) + ' €'


def short_text(value):
    value = value.strip()
    if not value or len(value) > 100 or any(ord(c) < 32 for c in value):
        raise ValueError('Enter between 1 and 100 characters on one line.')
    return value


def rate_cents(value):
    try:
        amount = Decimal(value.replace(',', '.'))
        if not amount.is_finite() or not 0 < amount <= 10000 or amount * 100 != (amount * 100).to_integral_value():
            raise ValueError
        return int(amount * 100)
    except (InvalidOperation, ValueError):
        raise ValueError('Enter an hourly rate between 0.01 and 10000, with at most two decimals.') from None


def clock_time(value):
    value = value.strip()
    if re.fullmatch(r'[0-9]{1,2}', value):
        value = value.zfill(2) + ':00'
    if not re.fullmatch(r'[0-9]{1,2}:[0-9]{2}', value):
        raise ValueError('Enter an hour such as 16, or a time such as 16:30.')
    try:
        return datetime.strptime(value, '%H:%M').strftime('%H:%M')
    except ValueError:
        raise ValueError('Enter a valid time from 00:00 to 23:59.') from None


def time_range(value):
    parts = re.split(r'\s*[-–]\s*', value.strip())
    if len(parts) != 2:
        raise ValueError('Use a start and end time, for example 16:00-20:00.')
    start, end = map(clock_time, parts)
    if end <= start:
        raise ValueError('End time must be after start time on the same day.')
    return start, end


def local_today(timezone):
    return datetime.now(ZoneInfo(timezone)).date()


def training_date(value, today):
    value = value.strip().lower()
    if value == 'today':
        return today.isoformat()
    try:
        if re.fullmatch(r'\d{1,2}\.\d{1,2}\.?', value):
            day, month = map(int, value.rstrip('.').split('.'))
            result = date(today.year, month, day)
        elif re.fullmatch(r'\d{1,2}\.\d{1,2}\.\d{4}', value):
            result = datetime.strptime(value, '%d.%m.%Y').date()
        else:
            result = date.fromisoformat(value)
        if result > today:
            raise ValueError
        return result.isoformat()
    except ValueError:
        raise ValueError('Use today, DD.MM, DD.MM.YYYY or YYYY-MM-DD. Future days cannot be logged.') from None


def month_value(value):
    if not re.fullmatch(r'\d{4}-\d{2}', value):
        raise ValueError('Use YYYY-MM, for example 2026-09.')
    try:
        date.fromisoformat(value + '-01')
    except ValueError:
        raise ValueError('Enter a valid month as YYYY-MM.') from None
    return value


def iban_value(value):
    """Keep the user's report text without imposing IBAN format/checksum rules."""
    return short_text(value)


def profile_value(field, value):
    if field == 'iban':
        return iban_value(value)
    if field == 'rate':
        return rate_cents(value)
    if field == 'timezone':
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError('Use a timezone such as Europe/Berlin or Europe/Kyiv.') from None
        return value
    if field == 'reminder_time':
        return clock_time(value)
    return short_text(value)


def quick_entry(value, today):
    match = re.fullmatch(r'(\S+)\s+(.+?)\s+([0-9]{1,2}(?::[0-9]{2})?\s*[-–]\s*[0-9]{1,2}(?::[0-9]{2})?)', value.strip())
    if not match:
        raise ValueError('Try: today Herschelschule 16:00-20:00, or choose Log day for guided entry.')
    day, gym, times = match.groups()
    start, end = time_range(times)
    return dict(day=training_date(day, today), gym=short_text(gym), start=start, end=end)


def time_suggestions(day, finish=False):
    weekday = date.fromisoformat(day).weekday()
    if weekday < 5:
        return ['19', '19:15', '20'] if finish else ['16', '16:15']
    if weekday == 5:
        return ['16'] if finish else ['10', '11']
    return []


def time_prompt(finish=False, suggestions=True):
    label = 'finish' if finish else 'start'
    examples = '18 or 18:45' if finish else '17 or 17:30'
    if suggestions:
        return f'Choose a suggested {label} time below, or type your own—for example, {examples}.'
    return f'Type the {label} time—for example, {examples}.'
