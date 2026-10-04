"""SQLite persistence. All personal records are scoped to a Telegram user."""
import json
import secrets
import sqlite3
from pathlib import Path

from .domain import clock_time, month_value, short_text

SCHEMA = '''
CREATE TABLE IF NOT EXISTS owner (singleton INTEGER PRIMARY KEY CHECK(singleton=1), user_id INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS profiles (
 user_id INTEGER PRIMARY KEY, first_name TEXT NOT NULL, last_name TEXT NOT NULL,
 sparte TEXT NOT NULL, iban TEXT NOT NULL, timezone TEXT NOT NULL,
 reminder_time TEXT NOT NULL, reminders_enabled INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS rates (
 user_id INTEGER NOT NULL REFERENCES profiles(user_id), effective_month TEXT NOT NULL,
 cents INTEGER NOT NULL CHECK(cents>0), PRIMARY KEY(user_id,effective_month));
CREATE TABLE IF NOT EXISTS gyms (
 id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES profiles(user_id),
 name TEXT NOT NULL, archived INTEGER NOT NULL DEFAULT 0, UNIQUE(user_id,name), UNIQUE(user_id,id));
CREATE TABLE IF NOT EXISTS day_logs (
 user_id INTEGER NOT NULL REFERENCES profiles(user_id), day TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('training','no_training')), PRIMARY KEY(user_id,day));
CREATE TABLE IF NOT EXISTS trainings (
 id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES profiles(user_id),
 day TEXT NOT NULL, gym_id INTEGER NOT NULL, start TEXT NOT NULL, end TEXT NOT NULL CHECK(end>start),
 request_id TEXT NOT NULL UNIQUE,
 FOREIGN KEY(user_id,gym_id) REFERENCES gyms(user_id,id),
 UNIQUE(user_id,day,gym_id,start,end));
CREATE TABLE IF NOT EXISTS conversations (
 user_id INTEGER PRIMARY KEY, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS schedules (
 id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, kind TEXT NOT NULL,
 name TEXT NOT NULL, frequency TEXT NOT NULL, weekdays TEXT NOT NULL,
 month_day INTEGER NOT NULL, time TEXT NOT NULL, message TEXT NOT NULL, enabled INTEGER NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS builtin_schedule ON schedules(user_id,kind) WHERE kind!='custom';
CREATE TABLE IF NOT EXISTS notifications (
 user_id INTEGER NOT NULL, kind TEXT NOT NULL, period TEXT NOT NULL,
 retry_at REAL NOT NULL, message_id INTEGER,
 PRIMARY KEY(user_id,kind,period));
CREATE TABLE IF NOT EXISTS ui_actions (
 token TEXT PRIMARY KEY, user_id INTEGER NOT NULL, screen TEXT NOT NULL, buttons TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ui_messages (
 user_id INTEGER NOT NULL, chat_id INTEGER NOT NULL, message_id INTEGER NOT NULL,
 PRIMARY KEY(user_id,chat_id,message_id));
CREATE TRIGGER IF NOT EXISTS one_training_per_day_insert
BEFORE INSERT ON trainings
WHEN EXISTS (SELECT 1 FROM trainings WHERE user_id=NEW.user_id AND day=NEW.day)
BEGIN SELECT RAISE(ABORT, 'one_training_per_day'); END;
CREATE TRIGGER IF NOT EXISTS one_training_per_day_move
BEFORE UPDATE OF day,user_id ON trainings
WHEN (NEW.day != OLD.day OR NEW.user_id != OLD.user_id)
 AND EXISTS (SELECT 1 FROM trainings WHERE user_id=NEW.user_id AND day=NEW.day AND id!=OLD.id)
BEGIN SELECT RAISE(ABORT, 'one_training_per_day'); END;
'''


class Store:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(path)
        path.chmod(0o600)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript(SCHEMA)

    def close(self):
        self.db.close()

    def track_message(self, uid, chat_id, message_id):
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO ui_messages VALUES (?,?,?)', (uid,chat_id,message_id))

    def message_ids(self, uid, chat_id):
        return [r[0] for r in self.db.execute('SELECT message_id FROM ui_messages WHERE user_id=? AND chat_id=?', (uid,chat_id))]

    def forget_message(self, uid, chat_id, message_id):
        with self.db:
            self.db.execute('DELETE FROM ui_messages WHERE user_id=? AND chat_id=? AND message_id=?', (uid,chat_id,message_id))

    def schedules(self, uid):
        p = self.profile(uid)
        if not p:
            return []
        defaults = [
            ('evening','Evening log reminder','weekly',0,
             'No information is recorded for {date}. Please log training or no training for this day.'),
            ('month_end','Monthly report reminder','monthly',0,
             'It’s the end of {month}. Review your logs and generate your monthly PDF when you’re ready.')]
        with self.db:
            for kind,name,frequency,month_day,message in defaults:
                if self.db.execute('SELECT 1 FROM schedules WHERE user_id=? AND kind=?',(uid,kind)).fetchone():
                    continue
                self.db.execute('INSERT OR IGNORE INTO schedules '
                                '(user_id,kind,name,frequency,weekdays,month_day,time,message,enabled) VALUES (?,?,?,?,?,?,?,?,?)',
                                (uid,kind,name,frequency,'0,1,2,3,4,5,6',month_day,p['reminder_time'],message,p['reminders_enabled']))
        return [dict(r) for r in self.db.execute('SELECT * FROM schedules WHERE user_id=? ORDER BY id',(uid,))]

    def schedule(self, uid, schedule_id):
        row = self.db.execute('SELECT * FROM schedules WHERE user_id=? AND id=?',(uid,schedule_id)).fetchone()
        if row is None:
            raise ValueError('This notification no longer exists. Open Notifications again.')
        return dict(row)

    def save_schedule(self, uid, values):
        name = short_text(values['name'])
        message = values['message'].strip()
        if not message or len(message) > 1000:
            raise ValueError('Enter message text between 1 and 1000 characters.')
        frequency = values['frequency']
        days = sorted(set(int(d) for d in values['weekdays'].split(',') if d))
        if frequency not in ('weekly','monthly') or any(d not in range(7) for d in days):
            raise ValueError('Choose a valid schedule.')
        if frequency == 'weekly' and not days:
            raise ValueError('Select at least one weekday.')
        month_day = int(values['month_day'])
        if month_day not in range(32):
            raise ValueError('Choose a day from 1 to 31, or Last day.')
        fields = (name, frequency, ','.join(map(str,days)), month_day,
                  clock_time(values['time']), message, int(bool(values['enabled'])))
        with self.db:
            if values.get('id'):
                self.schedule(uid,values['id'])
                self.db.execute('UPDATE schedules SET name=?,frequency=?,weekdays=?,month_day=?,time=?,message=?,enabled=? WHERE user_id=? AND id=?',
                                (*fields,uid,values['id']))
                return values['id']
            if len(self.schedules(uid)) >= 32:
                raise ValueError('Delete a custom notification before adding another (30 maximum).')
            return self.db.execute('INSERT INTO schedules (name,frequency,weekdays,month_day,time,message,enabled,user_id,kind) VALUES (?,?,?,?,?,?,?,?,?)',
                                   (*fields,uid,'custom')).lastrowid

    def delete_schedule(self, uid, schedule_id):
        row = self.schedule(uid,schedule_id)
        if row['kind'] != 'custom':
            raise ValueError('Turn off this built-in reminder instead of deleting it.')
        with self.db:
            self.db.execute('DELETE FROM schedules WHERE user_id=? AND id=?',(uid,schedule_id))

    def is_notification_message(self, uid, message_id):
        return self.db.execute('SELECT 1 FROM notifications WHERE user_id=? AND message_id=? LIMIT 1',
                               (uid,message_id)).fetchone() is not None

    def notification(self, uid, kind, period):
        row = self.db.execute('SELECT * FROM notifications WHERE user_id=? AND kind=? AND period=?',
                              (uid,kind,period)).fetchone()
        return dict(row) if row else None

    def defer_notification(self, uid, kind, period, retry_at):
        with self.db:
            self.db.execute('INSERT INTO notifications VALUES (?,?,?,?,NULL) '
                            'ON CONFLICT(user_id,kind,period) DO UPDATE SET retry_at=excluded.retry_at',
                            (uid,kind,period,retry_at))

    def mark_notification(self, uid, kind, period, message_id):
        with self.db:
            self.db.execute('UPDATE notifications SET message_id=? WHERE user_id=? AND kind=? AND period=?',
                            (message_id,uid,kind,period))

    def user_ids(self):
        return [r[0] for r in self.db.execute('SELECT user_id FROM profiles ORDER BY user_id')]

    def owner(self):
        """Legacy metadata only; not used for access control."""
        row = self.db.execute('SELECT user_id FROM owner').fetchone()
        return row[0] if row else None

    def claim(self, user_id, supplied, expected):
        """Legacy compatibility for old databases; registration no longer calls this."""
        if not expected or not secrets.compare_digest(supplied.encode(), expected.encode()):
            return False
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO owner VALUES (1,?)', (user_id,))
        return self.owner() == user_id

    def profile(self, uid):
        row = self.db.execute('SELECT * FROM profiles WHERE user_id=?', (uid,)).fetchone()
        return dict(row) if row else None

    def setup(self, uid, values, gyms):
        with self.db:
            self.db.execute('INSERT INTO profiles VALUES (?,?,?,?,?,?,?,1)',
                            (uid, *(values[k] for k in ('first_name','last_name','sparte','iban','timezone','reminder_time'))))
            # Initial rate also covers entries backfilled before setup.
            self.db.execute('INSERT INTO rates VALUES (?,?,?)', (uid, '0001-01', values['rate']))
            for name in gyms:
                self.db.execute('INSERT OR IGNORE INTO gyms(user_id,name) VALUES (?,?)', (uid,name))
            self.db.execute('DELETE FROM conversations WHERE user_id=?', (uid,))

    def update_profile(self, uid, field, value):
        if field not in {'first_name','last_name','sparte','iban','timezone','reminder_time','reminders_enabled'}:
            raise ValueError('Unknown setting.')
        with self.db:
            self.db.execute(f'UPDATE profiles SET {field}=? WHERE user_id=?', (value,uid))
            # Keep older settings commands compatible with the migrated reminders.
            if field in ('reminder_time','reminders_enabled'):
                column = 'time' if field == 'reminder_time' else 'enabled'
                self.db.execute(f"UPDATE schedules SET {column}=? WHERE user_id=? AND kind!='custom'", (value,uid))

    def set_rate(self, uid, month, cents):
        month_value(month)
        with self.db:
            self.db.execute('INSERT INTO rates VALUES (?,?,?) ON CONFLICT(user_id,effective_month) DO UPDATE SET cents=excluded.cents', (uid,month,cents))

    def rate(self, uid, month):
        month_value(month)
        row = self.db.execute('SELECT cents FROM rates WHERE user_id=? AND effective_month<=? ORDER BY effective_month DESC LIMIT 1', (uid,month)).fetchone()
        if row is None:
            raise ValueError('Set up your hourly rate first.')
        return row[0]

    def gyms(self, uid):
        return [dict(r) for r in self.db.execute('SELECT * FROM gyms WHERE user_id=? AND archived=0 ORDER BY id', (uid,))]

    def add_gym(self, uid, name):
        name = short_text(name)
        with self.db:
            self.db.execute('INSERT INTO gyms(user_id,name) VALUES (?,?) ON CONFLICT(user_id,name) DO UPDATE SET archived=0', (uid,name))

    def change_gym(self, uid, gym_id, name=None):
        with self.db:
            if name is None:
                self.db.execute('UPDATE gyms SET archived=1 WHERE user_id=? AND id=?', (uid,gym_id))
            else:
                try:
                    self.db.execute('UPDATE gyms SET name=? WHERE user_id=? AND id=?', (short_text(name),uid,gym_id))
                except sqlite3.IntegrityError:
                    raise ValueError('A gym with that name already exists.') from None

    def conversation(self, uid):
        row = self.db.execute('SELECT payload FROM conversations WHERE user_id=?', (uid,)).fetchone()
        return json.loads(row[0]) if row else {}

    def save_conversation(self, uid, payload):
        with self.db:
            if payload:
                self.db.execute('INSERT INTO conversations VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET payload=excluded.payload', (uid,json.dumps(payload)))
            else:
                self.db.execute('DELETE FROM conversations WHERE user_id=?', (uid,))

    def save_training(self, uid, entry, request_id, save_gym=False):
        from datetime import date
        date.fromisoformat(entry['day'])
        start, end = clock_time(entry['start']), clock_time(entry['end'])
        if end <= start:
            raise ValueError('End time must be after start time.')
        name = short_text(entry['gym'])
        with self.db:
            existing = self.db.execute('SELECT t.*,g.name AS gym FROM trainings t JOIN gyms g ON t.gym_id=g.id WHERE t.user_id=? AND t.day=?', (uid,entry['day'])).fetchall()
            if existing:
                if any(r['request_id'] == request_id or (r['gym'],r['start'],r['end']) == (name,start,end) for r in existing):
                    self.db.execute('DELETE FROM conversations WHERE user_id=?', (uid,))
                    return False
                raise ValueError('This day already has a training. Select it in Calendar and choose Edit training.')
            gym = self.db.execute('SELECT id FROM gyms WHERE user_id=? AND name=?', (uid,name)).fetchone()
            if gym:
                gym_id = gym[0]
                if save_gym:
                    self.db.execute('UPDATE gyms SET archived=0 WHERE user_id=? AND id=?', (uid,gym_id))
            else:
                gym_id = self.db.execute('INSERT INTO gyms(user_id,name,archived) VALUES (?,?,?)', (uid,name,int(not save_gym))).lastrowid
            try:
                inserted = self.db.execute('INSERT OR IGNORE INTO trainings(user_id,day,gym_id,start,end,request_id) VALUES (?,?,?,?,?,?)', (uid,entry['day'],gym_id,start,end,request_id)).rowcount
            except sqlite3.IntegrityError:
                raise ValueError('This day already has a training. Select it in Calendar and choose Edit training.') from None
            if inserted:
                self.db.execute("INSERT INTO day_logs VALUES (?,?,'training') ON CONFLICT(user_id,day) DO UPDATE SET status='training'", (uid,entry['day']))
            self.db.execute('DELETE FROM conversations WHERE user_id=?', (uid,))
        return bool(inserted)

    def no_training(self, uid, day):
        from datetime import date
        date.fromisoformat(day)
        with self.db:
            if self.db.execute('SELECT 1 FROM trainings WHERE user_id=? AND day=?', (uid,day)).fetchone():
                raise ValueError('This day already has training entries.')
            self.db.execute("INSERT INTO day_logs VALUES (?,?,'no_training') ON CONFLICT(user_id,day) DO UPDATE SET status='no_training'", (uid,day))

    def entries(self, uid, month):
        month_value(month)
        return [dict(r) for r in self.db.execute('SELECT t.*, g.name AS gym FROM trainings t JOIN gyms g ON t.gym_id=g.id AND t.user_id=g.user_id WHERE t.user_id=? AND substr(day,1,7)=? ORDER BY day,start,id', (uid,month))]

    def entry(self, uid, entry_id):
        row = self.db.execute('SELECT t.*, g.name AS gym FROM trainings t JOIN gyms g ON t.gym_id=g.id AND t.user_id=g.user_id WHERE t.user_id=? AND t.id=?', (uid, entry_id)).fetchone()
        if row is None:
            raise ValueError('This training is no longer available. Open View calendar again.')
        return dict(row)

    def day_statuses(self, uid, month):
        month_value(month)
        return dict(self.db.execute('SELECT day,status FROM day_logs WHERE user_id=? AND substr(day,1,7)=?', (uid, month)))

    def _refresh_training_day(self, uid, day):
        """After removing the last session, the day's status becomes unknown."""
        exists = self.db.execute('SELECT 1 FROM trainings WHERE user_id=? AND day=?', (uid, day)).fetchone()
        if exists:
            self.db.execute("INSERT INTO day_logs VALUES (?,?,'training') ON CONFLICT(user_id,day) DO UPDATE SET status='training'", (uid, day))
        else:
            self.db.execute("DELETE FROM day_logs WHERE user_id=? AND day=? AND status='training'", (uid, day))

    def edit_training(self, uid, entry_id, values):
        from datetime import date
        date.fromisoformat(values['day'])
        start, end = clock_time(values['start']), clock_time(values['end'])
        if end <= start:
            raise ValueError('Finish must be after start. Change the times before saving.')
        name = short_text(values['gym'])
        try:
            with self.db:
                old = self.entry(uid, entry_id)
                if old['day'] != values['day'] and self.db.execute('SELECT 1 FROM trainings WHERE user_id=? AND day=? AND id!=?', (uid,values['day'],entry_id)).fetchone():
                    raise ValueError('That day already has a training. Choose another date or cancel.')
                self.db.execute('INSERT OR IGNORE INTO gyms(user_id,name,archived) VALUES (?,?,1)', (uid, name))
                gym_id = self.db.execute('SELECT id FROM gyms WHERE user_id=? AND name=?', (uid, name)).fetchone()[0]
                self.db.execute('UPDATE trainings SET day=?,gym_id=?,start=?,end=? WHERE user_id=? AND id=?',
                                (values['day'], gym_id, start, end, uid, entry_id))
                self._refresh_training_day(uid, old['day'])
                self._refresh_training_day(uid, values['day'])
                self.db.execute('DELETE FROM conversations WHERE user_id=?', (uid,))
        except sqlite3.IntegrityError:
            raise ValueError('An identical training already exists. Change this draft or cancel.') from None

    def delete_training(self, uid, entry_id):
        with self.db:
            old = self.entry(uid, entry_id)
            self.db.execute('DELETE FROM trainings WHERE user_id=? AND id=?', (uid, entry_id))
            self._refresh_training_day(uid, old['day'])
            self.db.execute('DELETE FROM conversations WHERE user_id=?', (uid,))

    def clear_no_training(self, uid, day):
        with self.db:
            self.db.execute("DELETE FROM day_logs WHERE user_id=? AND day=? AND status='no_training'", (uid, day))
