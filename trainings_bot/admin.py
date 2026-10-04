"""Read-only admin views; target users' conversation state is never touched."""
from datetime import date
from uuid import uuid4

from .domain import euros, local_today, month_value, training_amount
from .history import month_calendar
from .notification_settings import describe
from .reports import report_data

ADMIN_FLOWS = {'admin_users','admin_user','admin_calendar','admin_notifications'}
PAGE_SIZE = 8


def parse_admin_ids(value):
    if not value.strip():
        return frozenset()
    parts = [part.strip() for part in value.split(',')]
    if any(not p.isascii() or not p.isdigit() or int(p)<=0 for p in parts):
        raise ValueError('ADMIN_USER_IDS must contain positive numeric Telegram user IDs separated by commas.')
    return frozenset(map(int,parts))


class Admin:
    def __init__(self, store, reply, admin_ids):
        self.store,self.reply,self.admin_ids = store,reply,admin_ids

    def require(self, actor):
        if actor not in self.admin_ids:
            raise ValueError('Admin access is not enabled for your account.')

    def profile(self, target):
        p = self.store.profile(target)
        if not p:
            raise ValueError('This user is no longer available.')
        return p

    def users(self, actor, page=0):
        self.require(actor)
        users = self.store.user_ids()
        page = max(0,min(page,max(0,(len(users)-1)//PAGE_SIZE)))
        choices = {}
        for uid in users[page*PAGE_SIZE:(page+1)*PAGE_SIZE]:
            p = self.profile(uid)
            choices[f"{p['first_name']} {p['last_name']} · {uid}"] = uid
        self.store.save_conversation(actor,dict(flow='admin_users',page=page,choices=choices))
        buttons = list(choices)
        if page:
            buttons.append('Previous users')
        if (page+1)*PAGE_SIZE < len(users):
            buttons.append('Next users')
        return self.reply(f'Admin · Registered users ({len(users)})\nChoose a user to view their information.\nPage {page+1} of {max(1,(len(users)+PAGE_SIZE-1)//PAGE_SIZE)}',buttons+['Back','Main menu'])

    def user(self, actor, target):
        self.require(actor)
        p = self.profile(target)
        month = local_today(p['timezone']).strftime('%Y-%m')
        gyms = ', '.join(g['name'] for g in self.store.gyms(target)) or 'None'
        self.store.save_conversation(actor,dict(flow='admin_user',target_uid=target))
        return self.reply(f"Admin · {p['first_name']} {p['last_name']}\nTelegram ID: {target}\n"
                          f"Department: {p['sparte']}\nTimezone: {p['timezone']}\n"
                          f"Hourly rate: {euros(self.store.rate(target,month)/100)}\nIBAN: …{p['iban'][-4:]}\nGyms: {gyms}",
                          ['Training calendar','Notification status','Back','Main menu'])

    def calendar(self, actor, target, month=None):
        self.require(actor)
        p = self.profile(target)
        today = local_today(p['timezone'])
        month = month_value(month or today.strftime('%Y-%m'))
        rows = self.store.entries(target,month)
        rate = self.store.rate(target,month)
        total = sum(training_amount(r['start'],r['end'],rate) for r in rows)
        minutes = sum((int(r['end'][:2])-int(r['start'][:2]))*60+int(r['end'][3:])-int(r['start'][3:]) for r in rows)
        token = uuid4().hex[:12]
        self.store.save_conversation(actor,dict(flow='admin_calendar',target_uid=target,month=month,calendar_token=token))
        grid = [[(label,data.replace('cal:','adm:',1)) for label,data in row]
                for row in month_calendar(month,self.store.day_statuses(target,month),today,token)]
        grid.append([('Choose month',f'adm:{token}:choosemonth')])
        buttons = (['Download PDF'] if rows and month<=today.strftime('%Y-%m') else [])+['Back','Main menu']
        return self.reply(f"Admin · {p['first_name']} {p['last_name']} · {month}\n"
                          f"{len(rows)} trainings · {minutes/60:.2f} hours · {euros(total)}",
                          buttons,calendar=grid,admin_view=True,
                          calendar_title='★ = information recorded · Tap a date to view its details.')

    def day(self, actor, state, day):
        self.require(actor)
        target = state['target_uid']
        if day[:7] != state['month']:
            raise ValueError('Choose a date in the displayed month.')
        date.fromisoformat(day)
        p = self.profile(target)
        rows = [r for r in self.store.entries(target,state['month']) if r['day']==day]
        rate = self.store.rate(target,state['month'])
        lines = [f"Admin · {p['first_name']} {p['last_name']} · {day}"]
        for r in rows:
            lines.append(f"{r['gym']} · {r['start']}–{r['end']} · {euros(training_amount(r['start'],r['end'],rate))}")
        if not rows:
            status = self.store.day_statuses(target,state['month']).get(day)
            lines.append('No training recorded.' if status=='no_training' else 'No information recorded.')
        state['selected_day']=day
        self.store.save_conversation(actor,state)
        return self.reply('\n'.join(lines),['Back','Main menu'],day_details=True,admin_view=True)

    def notifications(self, actor, target, page=0):
        self.require(actor)
        p = self.profile(target)
        # Do not call schedules(): that helper creates default records lazily.
        rows = [dict(r) for r in self.store.db.execute('SELECT * FROM schedules WHERE user_id=? ORDER BY id',(target,))]
        kinds = {r['kind'] for r in rows}
        for kind,name,frequency in [('evening','Evening log reminder','weekly'),('month_end','Monthly report reminder','monthly')]:
            if kind not in kinds:
                rows.append(dict(name=name,frequency=frequency,weekdays='0,1,2,3,4,5,6',month_day=0,
                                 time=p['reminder_time'],message='Default reminder',enabled=p['reminders_enabled']))
        page = max(0,min(page,max(0,(len(rows)-1)//3)))
        text = f"Admin · {p['first_name']} {p['last_name']} · Notifications\nTimezone: {p['timezone']}\n\n"
        text += '\n\n'.join(describe(r) for r in rows[page*3:(page+1)*3])
        buttons = (['Previous notifications'] if page else []) + (['Next notifications'] if (page+1)*3<len(rows) else [])
        self.store.save_conversation(actor,dict(flow='admin_notifications',target_uid=target,page=page))
        return self.reply(text,buttons+['Back','Main menu'])

    def handle(self, actor, text, state):
        self.require(actor)
        flow = state['flow']
        if flow=='admin_users':
            if text in state['choices']:
                return self.user(actor,state['choices'][text])
            if text in ('Previous users','Next users'):
                return self.users(actor,state['page']+(-1 if text=='Previous users' else 1))
        elif flow=='admin_user':
            if text=='Training calendar':
                return self.calendar(actor,state['target_uid'])
            if text=='Notification status':
                return self.notifications(actor,state['target_uid'])
        elif flow=='admin_notifications' and text in ('Previous notifications','Next notifications'):
            return self.notifications(actor,state['target_uid'],state['page']+(-1 if text=='Previous notifications' else 1))
        elif flow=='admin_calendar':
            if text=='Download PDF':
                data = report_data(self.store,state['target_uid'],state['month'])
                if not data['count']:
                    raise ValueError('No trainings to export for this month.')
                reply = self.calendar(actor,state['target_uid'],state['month'])
                reply.report_month,reply.report_user_id = state['month'],state['target_uid']
                return reply
        elif flow=='admin_month':
            return self.calendar(actor,state['target_uid'],month_value(text))
        raise ValueError('Choose a viewing option from the Admin menu.')

    def callback(self, actor, data):
        self.require(actor)
        state = self.store.conversation(actor)
        parts = data.split(':')
        if len(parts)!=3 or parts[0]!='adm' or state.get('flow')!='admin_calendar' or parts[1]!=state.get('calendar_token'):
            raise ValueError('Open the Admin calendar again to use these buttons.')
        action = parts[2]
        if action=='noop':
            return None
        if action in ('prev','next'):
            year,month=map(int,state['month'].split('-'))
            index=year*12+month-1+(-1 if action=='prev' else 1)
            if not 12 <= index < 120000:
                raise ValueError('No more months in that direction.')
            return self.calendar(actor,state['target_uid'],f'{index//12:04d}-{index%12+1:02d}')
        if action=='choosemonth':
            self.store.save_conversation(actor,dict(flow='admin_month',target_uid=state['target_uid']))
            return self.reply('Enter the month to view as YYYY-MM.', ['Cancel'])
        return self.day(actor,state,action)
