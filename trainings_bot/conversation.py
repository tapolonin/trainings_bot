"""Conversation logic kept independent of Telegram for end-to-end flow tests."""
from dataclasses import asdict, dataclass, field
from uuid import uuid4
from .history import History, month_calendar
from .reports import report_data, summary
from .notification_settings import NotificationSettings
from .admin import Admin, ADMIN_FLOWS

from .domain import (DEFAULT_GYMS, local_today, month_value, profile_value,
                     clock_time, quick_entry, short_text, time_range, training_date, time_suggestions, time_prompt)

FIELDS = ['first_name', 'last_name', 'sparte', 'iban', 'rate', 'timezone', 'reminder_time', 'gyms']
PROMPTS = {
    'first_name': 'What is your first name?',
    'last_name': 'What is your last name?',
    'sparte': 'What is your sport / department (Sparte)?',
    'iban': 'Enter your IBAN as you want it to appear on your reports. Spaces and hyphens are welcome; your formatting will be kept. Use one line (up to 100 characters).',
    'rate': 'What is your hourly rate in euros?',
    'timezone': 'Choose your timezone or type one such as Europe/Berlin.',
    'reminder_time': 'Choose a reminder time or type HH:MM. Evening and month-end reminders use this time.',
    'gyms': 'Add your gyms, remove any you do not need, then choose Next.',
}
DEFAULTS = {'sparte': 'RSG', 'timezone': 'Europe/Berlin', 'reminder_time': '20:00', 'gyms': 'Use suggested gyms'}
SETTINGS = {'First name': 'first_name', 'Last name': 'last_name', 'Sparte': 'sparte',
            'IBAN': 'iban', 'Hourly rate': 'rate', 'Timezone': 'timezone', 'Reminder time': 'reminder_time'}
BUTTON_LABELS = {
    '/day': 'Log day',
    '/train': 'Log training', '/no_training': 'No training',
    '/admin': 'Admin', '/log': 'Calendar', '/earnings': 'Earnings', '/report': 'Reports', '/settings': 'Settings',
    '/cancel': 'Cancel', '/start': 'Start setup', '/help': 'Help',
}
BUTTON_COMMANDS = {label: command for command, label in BUTTON_LABELS.items()}
BUTTON_COMMANDS['View trainings'] = '/log'
BUTTON_COMMANDS['View calendar'] = '/log'
BUTTON_COMMANDS['Main menu'] = '/start'
HOME = ['Log day', 'Calendar', 'Earnings', 'Reports', 'Settings', 'Help']
COMPETITION_NAMES = {'wettkampf', 'wettkämpf'}
TRAINING_FLOWS = {'day_choice', 'day_date', 'day_status', 'train_date', 'train_gym',
                  'train_new_gym', 'train_save_gym', 'competition_city', 'train_start', 'train_end',
                  'train_time', 'confirm', 'no_training', 'no_confirm'}
BROWSING_FLOWS = {None, 'settings', 'gym_menu', 'history', 'history_day_action', 'help', 'reports', 'report_preview', 'notification_list', 'notification_view'} | ADMIN_FLOWS


def in_action(state):
    return state.get('flow') not in BROWSING_FLOWS


@dataclass
class Reply:
    text: str
    buttons: list[str] = field(default_factory=lambda: HOME.copy())
    calendar: list = field(default_factory=list)
    calendar_title: str = ''
    day_details: bool = False
    detail_buttons: list = field(default_factory=list)
    restored_details: str = ''
    report_month: str = ''
    report_user_id: int | None = None
    admin_view: bool = False
    calendar_picker: bool = False
    replace_screen: bool = False

    def __post_init__(self):
        self.buttons = [BUTTON_LABELS.get(button, button) for button in self.buttons]


class Conversation:
    def __init__(self, store, setup_code=None, *, admin_ids=()):
        # setup_code is accepted for older callers; registration is now open.
        self.store = store
        self.admin_ids = frozenset(admin_ids)

    def is_admin(self, uid):
        return uid in self.admin_ids

    def admin(self):
        return Admin(self.store,Reply,self.admin_ids)

    def admin_action(self, uid, data):
        before = self.store.conversation(uid)
        return self.remember_navigation(uid,before,self.admin().callback(uid,data))

    def calendar_action(self, uid, data):
        before = self.store.conversation(uid)
        reply = self._calendar_action(uid, data)
        return self.remember_navigation(uid, before, reply)

    def _calendar_action(self, uid, data):
        if not self.store.profile(uid):
            raise ValueError('Complete your profile with /start first.')
        parts = data.split(':')
        state = self.store.conversation(uid)
        if (len(parts) != 3 or parts[0] != 'cal' or state.get('flow') not in ('history','day_date')
                or parts[1] != state.get('calendar_token')):
            raise ValueError('Open View calendar again to use the calendar.')
        action = parts[2]
        if action == 'noop':
            return None
        today = local_today(self.store.profile(uid)['timezone'])
        if state.get('flow') == 'day_date':
            if action in ('prev','next'):
                year, month = map(int, state['month'].split('-'))
                index = year * 12 + month - 1 + (-1 if action == 'prev' else 1)
                year, month = index // 12, index % 12 + 1
                if not 1 <= year <= 9999:
                    raise ValueError('No more months in that direction.')
                return self.date_picker(uid, f'{year:04d}-{month:02d}')
            day = training_date(action, today)
            if day[:7] != state['month']:
                raise ValueError('Choose a day in the displayed month.')
            return self.choose_day_status(uid, day)
        history = History(self.store, Reply)
        if action in ('prev', 'next'):
            return history.handle(uid, 'Previous month' if action == 'prev' else 'Next month', state, today)
        if action == 'choosemonth':
            return history.handle(uid, 'Choose month', state, today)
        if action in ('pageprev', 'pagenext'):
            return history.handle(uid, 'Previous entries' if action == 'pageprev' else 'Next entries', state, today)
        if action.startswith('button'):
            index = action.removeprefix('button')
            if not index.isascii() or not index.isdigit() or int(index) >= len(state['buttons']):
                raise ValueError('Open View calendar again to use these buttons.')
            return self.handle(uid, state['buttons'][int(index)])
        if action in ('detailprev', 'detailnext'):
            day = state.get('selected_day')
            if not day:
                raise ValueError('Tap a day first.')
            return history.day_details(uid, day, state,
                                       state.get('detail_page', 0) + (-1 if action == 'detailprev' else 1))
        if action.startswith('update'):
            day = action.removeprefix('update')
            if day != state.get('selected_day'):
                raise ValueError('Tap this day again before updating it.')
            history.handle(uid, 'Correct day', state, today)
            return history.handle(uid, day, self.store.conversation(uid), today)
        day = training_date(action, today)
        if state.get('view') == 'earnings':
            raise ValueError('Open Calendar to select a day.')
        if day[:7] != state['month']:
            raise ValueError('Choose a day in the displayed month.')
        return history.day_details(uid, day, state)

    def notification_action(self, uid, data, message_id):
        if not self.store.profile(uid):
            raise ValueError('Complete your profile with /start first.')
        parts = data.split(':')
        if len(parts) != 3 or parts[0] != 'notice':
            raise ValueError('Invalid reminder.')
        kind, period = parts[1:]
        record = self.store.notification(uid, kind, period)
        if record is None or record['message_id'] != message_id:
            raise ValueError('This reminder is no longer available. Open the main menu.')
        before = self.store.conversation(uid)
        if in_action(before):
            raise ValueError('Finish your current action or choose Cancel before opening this reminder.')
        if kind == 'evening':
            today = local_today(self.store.profile(uid)['timezone'])
            day = training_date(period, today)
            status = self.store.day_statuses(uid, day[:7]).get(day)
            if status:
                raise ValueError('This day is already logged. Open Calendar to review or edit it.')
            reply = self.choose_day_status(uid, day)
        elif kind == 'month_end':
            reply = self.report_preview(uid, month_value(period))
        else:
            raise ValueError('Invalid reminder.')
        return self.remember_navigation(uid, before, reply)

    def handle(self, uid, text):
        text = text.strip()
        state = self.store.conversation(uid)
        if state.get('flow','').startswith('admin_') and not self.is_admin(uid):
            self.store.save_conversation(uid,{})
            return Reply('Admin access is not enabled for your account.')
        if text in ('Admin','/admin') and not self.is_admin(uid):
            return Reply('Admin access is not enabled for your account.',self.buttons_for(state))
        if text == '/myid':
            return self.remember_navigation(uid,state,Reply(f'Your Telegram user ID: {uid}',self.buttons_for(state)))
        if text in ('Admin','/admin') and not in_action(state):
            return self.remember_navigation(uid,state,self.admin().users(uid))
        if (not self.store.profile(uid) and state.get('flow') != 'setup'
                and not (self.is_admin(uid) and state.get('flow','').startswith('admin_'))):
            return self.start_setup(uid)
        try:
            before = self.store.conversation(uid)
            if text == 'Back' and before.get('flow') in TRAINING_FLOWS and before.get('_steps'):
                steps = before['_steps'].copy()
                previous = steps.pop()
                previous['_steps'] = steps
                self.store.save_conversation(uid, previous)
                return Reply(**previous['_step_reply'])
            return self.remember_navigation(uid, before, self.authorized(uid, text))
        except ValueError as exc:
            state = self.store.conversation(uid)
            return Reply(str(exc), self.buttons_for(state))

    def screen_context(self, state):
        return {k: state[k] for k in ('flow','month','view','page','selected_day','day','schedule_id','target_uid') if k in state}

    def remember_navigation(self, uid, before, reply):
        if reply is None:
            return None
        state = self.store.conversation(uid)
        if reply.buttons == HOME and self.is_admin(uid):
            reply.buttons = HOME + ['Admin']
        if in_action(state):
            state['return_to'] = before.get('return_to', {}) if in_action(before) else self.screen_context(before)
            reply.buttons = [b for b in reply.buttons if b not in ('Main menu','Back')]
            if 'Cancel' not in reply.buttons:
                reply.buttons.append('Cancel')
        if state.get('flow') in TRAINING_FLOWS:
            steps = before.get('_steps', []).copy() if before.get('flow') in TRAINING_FLOWS else []
            if before.get('flow') != state['flow'] and before.get('_step_reply'):
                steps.append({k: v for k, v in before.items() if k not in
                              ('_steps', 'calendar_message_id', 'detail_message_id',
                               'keyboard_message_id', 'detail_text', 'detail_token')})
            state['_steps'] = steps
            if steps:
                reply.buttons.insert(len(reply.buttons) - 1, 'Back')
            state['_step_reply'] = asdict(reply)
        if state:
            state['_buttons'] = reply.buttons
            self.store.save_conversation(uid, state)
        return reply

    def home(self, uid):
        self.store.save_conversation(uid, {})
        return Reply('Main menu. Choose what you would like to do.',HOME + (['Admin'] if self.is_admin(uid) else []))

    def restore_screen(self, uid, context):
        flow = context.get('flow')
        if flow == 'admin_users':
            return self.admin().users(uid,context.get('page',0))
        if flow == 'admin_user':
            return self.admin().user(uid,context['target_uid'])
        if flow == 'admin_notifications':
            return self.admin().notifications(uid,context['target_uid'],context.get('page',0))
        if flow == 'admin_calendar':
            reply = self.admin().calendar(uid,context['target_uid'],context['month'])
            if context.get('selected_day'):
                details = self.admin().day(uid,self.store.conversation(uid),context['selected_day'])
                reply.restored_details = details.text
            return reply
        if flow == 'notification_list':
            return NotificationSettings(self.store,Reply).menu(uid)
        if flow == 'notification_view':
            return NotificationSettings(self.store,Reply).view(uid,context['schedule_id'])
        if flow == 'reports':
            return self.reports_menu(uid)
        if flow == 'report_preview':
            return self.report_preview(uid, context['month'])
        if flow == 'settings':
            return self.settings(uid)
        if flow == 'gym_menu':
            return self.gym_menu(uid)
        if flow in ('history', 'history_day_action'):
            today = local_today(self.store.profile(uid)['timezone'])
            history = History(self.store, Reply)
            reply = history.overview(uid, context['month'], today, context.get('page',0), view=context.get('view','calendar'))
            day = context.get('selected_day') or context.get('day')
            if day:
                details = history.day_details(uid, day, self.store.conversation(uid))
                reply.buttons = details.buttons
                reply.restored_details = details.text
                reply.detail_buttons = details.detail_buttons
            return reply
        return self.home(uid)

    def go_back(self, uid, state):
        if state.get('flow') == 'admin_calendar' and state.get('selected_day'):
            return self.admin().calendar(uid,state['target_uid'],state['month'])
        if state.get('flow') in ('admin_calendar','admin_notifications'):
            return self.admin().user(uid,state['target_uid'])
        if state.get('flow') == 'admin_user':
            return self.admin().users(uid)
        if state.get('flow') == 'notification_list':
            return self.settings(uid)
        if state.get('flow') == 'notification_view':
            return NotificationSettings(self.store,Reply).menu(uid)
        if state.get('flow') == 'report_preview':
            return self.reports_menu(uid)
        if state.get('flow') == 'gym_menu':
            return self.settings(uid)
        if state.get('flow') in ('history','history_day_action') and (state.get('selected_day') or state.get('day')):
            return self.restore_screen(uid, {k:v for k,v in self.screen_context(state).items() if k not in ('selected_day','day')})
        return self.home(uid)

    def buttons_for(self, state):
        if state.get('flow') == 'setup':
            key = FIELDS[state['index']]
            if key == 'gyms':
                if state.get('gym_mode') == 'add':
                    return ['Done', 'Next', 'Cancel']
                if state.get('gym_mode') == 'remove':
                    return ['Remove: ' + g for g in state.get('gym_draft',[])] + ['Done','Next','Cancel']
                return ['Add gym'] + (['Remove gym'] if state.get('gym_draft') else []) + ['Use suggested gyms','Next','Cancel']
            return ([DEFAULTS[key]] if key in DEFAULTS else []) + ['Cancel']
        if state.get('_buttons'):
            return state['_buttons']
        if state.get('flow', '').startswith('history'):
            return state.get('buttons', ['Cancel'])
        if state.get('flow') == 'day_choice':
            return ['Today', 'Other day', 'Cancel']
        if state.get('flow') == 'day_status':
            return ['Training', 'No training', 'Cancel']
        if state.get('flow') == 'confirm':
            return ['Save', 'Cancel'] + (['Save and add gym'] if state.get('new_gym') else [])
        return ['/cancel'] if state else HOME.copy()

    def reports_menu(self, uid):
        self.store.save_conversation(uid, dict(flow='reports'))
        return Reply('Reports. Choose a month for your PDF.',
                     ['Current month', 'Previous month', 'Choose month', 'Back', 'Main menu'])

    def report_preview(self, uid, month):
        data = report_data(self.store, uid, month)
        self.store.save_conversation(uid, dict(flow='report_preview', month=month, report_token=uuid4().hex))
        text = 'Monthly report\n' + summary(data)
        if data['missing']:
            text += f"\nDays without information: {data['missing']}. Only recorded trainings appear in the PDF."
        if not data['count']:
            text += '\nNo trainings to export for this month.'
        return Reply(text, (['Generate PDF'] if data['count'] else []) + ['Choose month', 'Back', 'Main menu'])

    def setup_gyms(self, uid, state, mode='list', replace=False):
        state['gym_mode'] = mode
        gyms = state.setdefault('gym_draft',[])
        self.store.save_conversation(uid,state)
        listing = '\n'.join('• '+gym for gym in gyms) if gyms else 'No gyms added yet.'
        prompt = ('Type a gym name, or several separated by semicolons.' if mode == 'add' else
                  'Tap a gym below to remove it. Choose Done to return to the list.' if mode == 'remove' else
                  'Choose Add gym or type gym names separated by semicolons. You can remove gyms before continuing.')
        return Reply('Your gyms\n'+listing+'\n\n'+prompt+'\nChoose Next when ready to finish setup. You can add more gyms later in Settings.',
                     self.buttons_for(state),replace_screen=replace)

    def handle_setup_gyms(self, uid, text, state):
        gyms = state.setdefault('gym_draft',[])
        if text.casefold() == 'next':
            self.store.setup(uid,state['values'],gyms)
            return Reply('Setup complete. Choose Log day to record a day, or Settings to change your profile.')
        if text == 'Done':
            return self.setup_gyms(uid,state)
        if state.get('gym_mode') == 'remove':
            choices = {'Remove: '+gym:gym for gym in gyms}
            if text not in choices:
                raise ValueError('Tap a gym to remove it, or choose Done or Next.')
            gyms.remove(choices[text])
            return self.setup_gyms(uid,state,mode='remove',replace=True)
        if text == 'Add gym' and state.get('gym_mode') != 'add':
            return self.setup_gyms(uid,state,mode='add')
        if text == 'Remove gym' and state.get('gym_mode') != 'add':
            return self.setup_gyms(uid,state,mode='remove')
        additions = DEFAULT_GYMS.copy() if text == 'Use suggested gyms' else [short_text(g) for g in text.split(';')]
        combined = list(dict.fromkeys(gyms+additions))
        if len(combined)>30:
            raise ValueError('Use at most 30 gyms. Remove one before adding another.')
        state['gym_draft'] = combined
        return self.setup_gyms(uid,state)

    def start_setup(self, uid):
        self.store.save_conversation(uid, dict(flow='setup', index=0, values={}))
        return Reply('Let’s set up your profile. You can change it later in Settings.\n' + PROMPTS['first_name'], ['/cancel'])

    def date_picker(self, uid, month=None):
        today = local_today(self.store.profile(uid)['timezone'])
        month = month_value(month or today.strftime('%Y-%m'))
        token = uuid4().hex[:12]
        self.store.save_conversation(uid, dict(flow='day_date', month=month, calendar_token=token))
        return Reply('Choose the day to log, or type a date as DD.MM.YYYY.', ['Cancel'],
                     calendar=month_calendar(month, self.store.day_statuses(uid,month),today,token),
                     calendar_title='★ = information recorded · · = future', calendar_picker=True)

    def choose_gym(self, uid, day):
        self.store.save_conversation(uid, dict(flow='train_gym', day=day))
        gyms = [g['name'] for g in self.store.gyms(uid)
                if g['name'].casefold() not in COMPETITION_NAMES]
        return Reply('Choose a gym, Wettkampf for a competition, or type a place for this session.', gyms + ['Add new gym', 'Wettkampf', 'Cancel'])

    def choose_day_status(self, uid, day):
        self.store.save_conversation(uid, dict(flow='day_status', day=day))
        return Reply(f'Did you have training on {day}?', ['Training', 'No training', 'Cancel'])

    def settings(self, uid):
        p = self.store.profile(uid)
        today = local_today(p['timezone'])
        rate = self.store.rate(uid, today.strftime('%Y-%m')) / 100
        self.store.save_conversation(uid, dict(flow='settings'))
        return Reply(f"Settings\n{p['first_name']} {p['last_name']} · {p['sparte']}\n"
                     f"IBAN: …{p['iban'][-4:]}\nCurrent rate: €{rate:.2f}/hour\n"
                     f"Timezone: {p['timezone']}\n"
                     'Choose what to change.', [label for label in SETTINGS if label != 'Reminder time'] + ['Notifications', 'Gyms', 'Back', 'Main menu'])

    def gym_menu(self, uid):
        gyms = self.store.gyms(uid)
        self.store.save_conversation(uid, dict(flow='gym_menu'))
        listing = '\n'.join(f"{g['id']}: {g['name']}" for g in gyms) or 'No saved gyms.'
        return Reply('Saved gyms\n' + listing + '\nChoose an action. Archiving keeps old logs.',
                     ['Add gym', 'Rename gym', 'Archive gym', 'Back', 'Main menu'])

    def confirm(self, uid, entry):
        if entry['gym'].casefold() in COMPETITION_NAMES:
            self.store.save_conversation(uid, dict(flow='competition_city', **entry))
            return Reply('Which city was the competition in?', ['/cancel'])
        is_new = entry['gym'] not in [g['name'] for g in self.store.gyms(uid)]
        if self.store.conversation(uid).get('gym_list_decided') == entry['gym']:
            is_new = False
        state = dict(flow='confirm', entry=entry, request_id=uuid4().hex, new_gym=is_new)
        self.store.save_conversation(uid, state)
        return Reply(f"Save this training?\n{entry['day']} · {entry['gym']}\n{entry['start']}–{entry['end']}\n"
                     'Participants will be left blank.' + ('\nYou can also add this gym to your saved list.' if is_new else ''), self.buttons_for(state))

    def authorized(self, uid, text):
        p = self.store.profile(uid)
        state = self.store.conversation(uid)
        if text in ('/cancel', 'Cancel'):
            if in_action(state):
                if not p and not state.get('flow','').startswith('admin_'):
                    self.store.save_conversation(uid, {})
                    return Reply('Setup cancelled. Choose Start setup to begin again.', ['Start setup'])
                reply = self.restore_screen(uid, state.get('return_to', {}))
                reply.text = 'Action cancelled.\n\n' + reply.text
                return reply
            return self.go_back(uid, state)
        if in_action(state) and (text in ('Main menu','Back') or text.startswith('/')):
            raise ValueError('Finish this action or choose Cancel before leaving it.')
        if text == 'Back':
            return self.go_back(uid, state)
        if not state or state.get('flow') in ('settings', 'gym_menu', 'history', 'reports', 'report_preview', 'notification_list', 'notification_view'):
            text = BUTTON_COMMANDS.get(text, text)
        elif text == 'Main menu':
            return self.home(uid)
        if not text:
            raise ValueError('Please enter a value or choose Cancel.')
        if text == '/start':
            if not p:
                return self.start_setup(uid)
            self.store.save_conversation(uid, {})
            return Reply('Welcome back. Choose Log day to record a day, or send:\ntoday Herschelschule 16:00-20:00')
        state = self.store.conversation(uid)
        if state.get('flow') == 'setup':
            if text.startswith('/'):
                raise ValueError('Complete this setup step, or choose Cancel to leave setup.')
            key = FIELDS[state['index']]
            if key == 'gyms':
                return self.handle_setup_gyms(uid,text,state)
            state['values'][key] = profile_value(key, text)
            state['index'] += 1
            if FIELDS[state['index']] == 'gyms':
                return self.setup_gyms(uid,state)
            self.store.save_conversation(uid, state)
            return Reply(PROMPTS[FIELDS[state['index']]], self.buttons_for(state))
        if state.get('flow','').startswith('admin_') and not text.startswith('/'):
            return self.admin().handle(uid,text,state)
        if not p:
            return self.start_setup(uid)
        today = local_today(p['timezone'])
        if text == '/help':
            self.store.save_conversation(uid, dict(flow='help'))
            return Reply('Log day — record training or no training\nCalendar — browse dates and manage training records\nEarnings — see amounts per training and monthly totals\nReports — receive a monthly PDF\nSettings — profile, rate, timezone and gyms\nCancel — discard the unfinished action and return to where it started\nBack — previous step while logging, or previous screen while browsing\nMain menu — go home while browsing\n\nUse the month arrows or Choose month in either section.\nQuick entry: 28.09 Herschelschule 16:00-20:00\nDates without a year use the current year. Use DD.MM.YYYY for older years.', ['Back','Main menu'])
        if text == '/report' or text.startswith('/report '):
            if ' ' in text:
                return self.report_preview(uid, month_value(text.split(maxsplit=1)[1]))
            return self.reports_menu(uid)
        if state.get('flow') in ('reports', 'report_preview') and not text.startswith('/'):
            if text == 'Choose month':
                self.store.save_conversation(uid, dict(flow='report_month'))
                return Reply('Which month? Enter YYYY-MM, for example 2026-08.', ['Cancel'])
            if text == 'Current month':
                return self.report_preview(uid, today.strftime('%Y-%m'))
            if text == 'Previous month':
                from datetime import timedelta
                return self.report_preview(uid, (today.replace(day=1) - timedelta(days=1)).strftime('%Y-%m'))
            if text == 'Generate PDF' and state['flow'] == 'report_preview':
                reply = self.report_preview(uid, state['month'])
                if report_data(self.store, uid, state['month'])['count']:
                    reply.report_month = state['month']
                return reply
            raise ValueError('Choose a month or an action below the report.')
        if state.get('flow') == 'report_month':
            return self.report_preview(uid, month_value(text))
        if text == '/day':
            self.store.save_conversation(uid, dict(flow='day_choice'))
            return Reply('Which day would you like to log?', ['Today', 'Other day', 'Cancel'])
        if text == '/settings':
            return self.settings(uid)
        if text == '/train':
            self.store.save_conversation(uid, dict(flow='train_date'))
            return Reply('Which day? Use today, DD.MM or DD.MM.YYYY.', ['today', '/cancel'])
        if text == '/no_training':
            self.store.save_conversation(uid, dict(flow='no_training'))
            return Reply('Which day had no training?', ['today', '/cancel'])
        if text == '/log' or text.startswith('/log '):
            month = month_value(text.split(maxsplit=1)[1] if ' ' in text else today.strftime('%Y-%m'))
            return History(self.store, Reply).overview(uid, month, today)
        if text == '/earnings' or text.startswith('/earnings '):
            month = month_value(text.split(maxsplit=1)[1] if ' ' in text else today.strftime('%Y-%m'))
            return History(self.store, Reply).overview(uid, month, today, view='earnings')
        flow = state.get('flow')
        if flow and flow.startswith('notification_'):
            return NotificationSettings(self.store,Reply).handle(uid,text,state)
        if flow and flow.startswith('history'):
            return History(self.store, Reply).handle(uid, text, state, today)
        if flow == 'day_choice':
            if text.casefold() == 'today':
                return self.choose_day_status(uid, today.isoformat())
            if text == 'Other day':
                return self.date_picker(uid)
            raise ValueError('Choose Today or Other day.')
        if flow == 'day_date':
            return self.choose_day_status(uid, training_date(text, today))
        if flow == 'day_status':
            if text == 'Training':
                return self.choose_gym(uid, state['day'])
            if text == 'No training':
                self.store.save_conversation(uid, dict(flow='no_confirm', day=state['day']))
                return Reply(f"Record no training on {state['day']}?", ['Confirm no training', 'Cancel'])
            raise ValueError('Choose Training or No training.')
        if flow == 'settings':
            if text == 'Notifications':
                return NotificationSettings(self.store,Reply).menu(uid)
            if text in SETTINGS:
                key = SETTINGS[text]
                self.store.save_conversation(uid, dict(flow='setting', key=key))
                return Reply(PROMPTS[key], ([DEFAULTS[key]] if key in DEFAULTS else []) + ['/cancel'])
            if text == 'Toggle reminders':
                self.store.update_profile(uid, 'reminders_enabled', int(not p['reminders_enabled']))
                return self.settings(uid)
            if text == 'Gyms':
                return self.gym_menu(uid)
            raise ValueError('Choose a setting from the keyboard.')
        if flow == 'setting':
            key = state['key']
            value = profile_value(key, text)
            if key == 'rate':
                self.store.save_conversation(uid, dict(flow='rate_month', cents=value))
                return Reply('From which month should this rate apply? Use YYYY-MM. Earlier months keep their rates.', [today.strftime('%Y-%m'), '/cancel'])
            self.store.update_profile(uid, key, value)
            return self.settings(uid)
        if flow == 'rate_month':
            month = month_value(text)
            self.store.save_conversation(uid, dict(flow='rate_confirm', month=month, cents=state['cents']))
            return Reply(f"Apply €{state['cents']/100:.2f}/hour from {month}? This also changes reports for recorded trainings from that month until the next scheduled rate change.", ['Apply rate', '/cancel'])
        if flow == 'rate_confirm':
            if text != 'Apply rate':
                raise ValueError('Choose Apply rate or Cancel.')
            self.store.set_rate(uid, state['month'], state['cents'])
            return self.settings(uid)
        if flow == 'gym_menu':
            if text not in ('Add gym', 'Rename gym', 'Archive gym'):
                raise ValueError('Choose Add gym, Rename gym or Archive gym.')
            self.store.save_conversation(uid, dict(flow='gym_action', action=text))
            prompt = {'Add gym': 'Enter the new gym name.', 'Rename gym': 'Enter the gym number and new name, for example: 1 New name', 'Archive gym': 'Enter the gym number to archive.'}[text]
            return Reply(prompt, ['/cancel'])
        if flow == 'gym_action':
            if state['action'] == 'Add gym':
                if len(self.store.gyms(uid)) >= 30:
                    raise ValueError('Archive a gym before adding another (30 saved gyms maximum).')
                self.store.add_gym(uid, text)
            else:
                parts = text.split(maxsplit=1)
                if not parts[0].isdigit() or int(parts[0]) not in [g['id'] for g in self.store.gyms(uid)]:
                    raise ValueError('Enter a gym number from your saved list.')
                if state['action'] == 'Rename gym' and len(parts) != 2:
                    raise ValueError('Include the new name after the gym number.')
                self.store.change_gym(uid, int(parts[0]), parts[1] if state['action'] == 'Rename gym' else None)
            return self.gym_menu(uid)
        if flow == 'train_date':
            day = training_date(text, today)
            return self.choose_gym(uid, day)
        if flow == 'train_new_gym':
            gym = short_text(text)
            state.update(flow='train_save_gym', gym=gym)
            self.store.save_conversation(uid, state)
            return Reply(f'Add {gym} to your saved gym list? Choose No to use it only for this training.', ['Yes, add to my gyms', 'No, just this training', 'Cancel'])
        if flow == 'train_save_gym':
            if text not in ('Yes, add to my gyms', 'No, just this training'):
                raise ValueError('Choose Yes to save the gym, or No to use it only for this training.')
            if text == 'Yes, add to my gyms':
                if state['gym'] not in [g['name'] for g in self.store.gyms(uid)] and len(self.store.gyms(uid)) >= 30:
                    raise ValueError('Your gym list is full (30 maximum). Choose No to use this gym just for this training, or Cancel and archive a gym in Settings.')
                self.store.add_gym(uid, state['gym'])
            state.update(flow='train_start', gym_list_decided=state['gym'])
            self.store.save_conversation(uid, state)
            return Reply(time_prompt(suggestions=bool(time_suggestions(state['day']))), time_suggestions(state['day']) + ['Cancel'])
        if flow == 'train_gym':
            if text == 'Add new gym':
                state.update(flow='train_new_gym')
                self.store.save_conversation(uid,state)
                return Reply('Enter the new gym name. Next, you can choose whether to add it to your saved list.', ['Cancel'])
            if text.casefold() in COMPETITION_NAMES:
                state.update(flow='competition_city')
                self.store.save_conversation(uid, state)
                return Reply('Which city was the competition in?', ['/cancel'])
            state.update(flow='train_start', gym=short_text(text))
            self.store.save_conversation(uid, state)
            return Reply(time_prompt(suggestions=bool(time_suggestions(state['day']))), time_suggestions(state['day']) + ['Cancel'])
        if flow == 'competition_city':
            gym = short_text('Wettkampf ' + short_text(text))
            if 'start' in state:
                return self.confirm(uid, dict(day=state['day'], gym=gym,
                                              start=state['start'], end=state['end']))
            state.update(flow='train_start', gym=gym)
            self.store.save_conversation(uid, state)
            return Reply(time_prompt(suggestions=bool(time_suggestions(state['day']))), time_suggestions(state['day']) + ['Cancel'])
        if flow == 'train_start':
            state.update(flow='train_end', start=clock_time(text))
            self.store.save_conversation(uid, state)
            suggestions = [t for t in time_suggestions(state['day'], finish=True) if clock_time(t) > state['start']]
            return Reply(time_prompt(finish=True, suggestions=bool(suggestions)), suggestions + ['Cancel'])
        if flow == 'train_end':
            start, end = time_range(state['start'] + '-' + clock_time(text))
            return self.confirm(uid, dict(day=state['day'], gym=state['gym'], start=start, end=end))
        # Support drafts created before start and finish became separate steps.
        if flow == 'train_time':
            start, end = time_range(text)
            return self.confirm(uid, dict(day=state['day'], gym=state['gym'], start=start, end=end))
        if flow == 'confirm':
            if text not in ('Save', 'Save and add gym'):
                raise ValueError('Choose Save or Cancel.')
            saved = self.store.save_training(uid, state['entry'], state['request_id'], text == 'Save and add gym')
            return Reply('Training saved.' if saved else 'This training was already saved; no duplicate was added.')
        if flow == 'no_training':
            day = training_date(text, today)
            self.store.save_conversation(uid, dict(flow='no_confirm', day=day))
            return Reply(f'Record no training on {day}?', ['Confirm no training', '/cancel'])
        if flow == 'no_confirm':
            if text != 'Confirm no training':
                raise ValueError('Choose Confirm no training or Cancel.')
            self.store.no_training(uid, state['day'])
            self.store.save_conversation(uid, {})
            return Reply(f"Recorded no training on {state['day']}.")
        return self.confirm(uid, quick_entry(text, today))
