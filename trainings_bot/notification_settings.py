"""Notification menus and cancellable editing drafts."""
from .domain import clock_time, short_text

DAYS = ['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday']


def describe(row):
    when = ', '.join(DAYS[int(d)][:3] for d in row['weekdays'].split(',') if d)
    if row['frequency'] == 'monthly':
        when = 'Last day of each month' if not row['month_day'] else f"Day {row['month_day']} each month (last day in shorter months)"
    return f"{row['name']} · {'On' if row['enabled'] else 'Off'}\n{when} at {row['time']}\n\n{row['message']}"


class NotificationSettings:
    def __init__(self, store, reply):
        self.store, self.reply = store, reply

    def menu(self, uid):
        rows = self.store.schedules(uid)
        choices = {f"{r['name']} · {'On' if r['enabled'] else 'Off'} · {r['id']}": r['id'] for r in rows}
        self.store.save_conversation(uid, dict(flow='notification_list', choices=choices))
        return self.reply('Notifications\nChoose one to change its schedule, time or message. Each can be switched on or off independently.',
                          list(choices) + ['New notification','Back','Main menu'])

    def view(self, uid, schedule_id):
        row = self.store.schedule(uid,schedule_id)
        self.store.save_conversation(uid,dict(flow='notification_view',schedule_id=schedule_id))
        buttons = ['Change name','Change schedule','Change time','Change message',
                   'Turn off' if row['enabled'] else 'Turn on']
        if row['kind'] == 'custom':
            buttons.append('Delete notification')
        return self.reply(describe(row) + '\n\nTimezone: ' + self.store.profile(uid)['timezone'],buttons+['Back','Main menu'])

    def prompt(self, uid, state, step, replace=False):
        state.update(flow='notification_edit',step=step)
        self.store.save_conversation(uid,state)
        draft = state['draft']
        if step == 'name':
            text, buttons = 'Enter a name for this notification.', []
        elif step == 'frequency':
            text, buttons = 'How often should this notification repeat?', ['Weekly','Monthly']
        elif step == 'weekdays':
            selected = draft['weekdays'].split(',')
            text = 'Select one or more weekdays. Tap again to deselect, then choose Done.'
            buttons = [('✓ ' if str(i) in selected else '○ ') + day for i,day in enumerate(DAYS)] + ['Done']
        elif step == 'month_day':
            text,buttons = 'Enter a day of the month (1–31), or choose Last day. Shorter months use their last day.', ['Last day']
        elif step == 'time':
            text,buttons = 'At what time? Enter HH:MM in your configured timezone.', ['20:00']
        elif step == 'message':
            text,buttons = 'Enter the notification message (up to 1000 characters). Optional: {date} and {month} insert the current date or month.', []
        else:
            text,buttons = 'Save this notification?\n\n'+describe(draft), ['Save notification']
        return self.reply(text,buttons+['Cancel'],replace_screen=replace)

    def handle(self, uid, text, state):
        flow = state['flow']
        if flow == 'notification_list':
            if text in state['choices']:
                return self.view(uid,state['choices'][text])
            if text == 'New notification':
                draft = dict(name='',kind='custom',frequency='weekly',weekdays='',month_day=0,time='20:00',message='',enabled=1)
                return self.prompt(uid,dict(draft=draft,new=True),'name')
            raise ValueError('Choose a notification or New notification.')
        if flow == 'notification_view':
            row = self.store.schedule(uid,state['schedule_id'])
            if text in ('Turn on','Turn off'):
                row['enabled'] = int(text=='Turn on')
                self.store.save_schedule(uid,row)
                return self.view(uid,row['id'])
            if text == 'Delete notification' and row['kind'] == 'custom':
                self.store.save_conversation(uid,dict(flow='notification_delete',schedule_id=row['id']))
                return self.reply(f"Delete {row['name']}?",['Delete notification','Cancel'])
            steps = {'Change name':'name','Change schedule':'frequency','Change time':'time','Change message':'message'}
            if text not in steps:
                raise ValueError('Choose an action for this notification.')
            step = steps[text]
            if step == 'frequency' and row['kind'] != 'custom':
                step = 'weekdays' if row['kind']=='evening' else 'month_day'
            return self.prompt(uid,dict(draft=row,new=False),step)
        if flow == 'notification_delete':
            if text != 'Delete notification':
                raise ValueError('Choose Delete notification or Cancel.')
            self.store.delete_schedule(uid,state['schedule_id'])
            return self.menu(uid)
        step, draft = state['step'], state['draft']
        if step == 'name':
            draft['name'] = short_text(text)
            return self.prompt(uid,state,'frequency' if state['new'] else 'confirm')
        if step == 'frequency':
            if text not in ('Weekly','Monthly'):
                raise ValueError('Choose Weekly or Monthly.')
            draft['frequency'] = text.lower()
            return self.prompt(uid,state,'weekdays' if text=='Weekly' else 'month_day')
        if step == 'weekdays':
            selected = set(draft['weekdays'].split(',')) - {''}
            if text == 'Done':
                if not selected:
                    raise ValueError('Select at least one weekday.')
                return self.prompt(uid,state,'time' if state['new'] else 'confirm')
            day = text.removeprefix('✓ ').removeprefix('○ ')
            if day not in DAYS:
                raise ValueError('Tap weekdays, then choose Done.')
            index = str(DAYS.index(day))
            selected.symmetric_difference_update({index})
            draft['weekdays'] = ','.join(sorted(selected))
            return self.prompt(uid,state,'weekdays',replace=True)
        if step == 'month_day':
            if text == 'Last day':
                draft['month_day'] = 0
            elif text.isascii() and text.isdigit() and 1 <= int(text) <= 31:
                draft['month_day'] = int(text)
            else:
                raise ValueError('Enter 1–31 or choose Last day.')
            return self.prompt(uid,state,'time' if state['new'] else 'confirm')
        if step == 'time':
            draft['time'] = clock_time(text)
            return self.prompt(uid,state,'message' if state['new'] else 'confirm')
        if step == 'message':
            if not text or len(text)>1000:
                raise ValueError('Enter message text between 1 and 1000 characters.')
            draft['message'] = text
            return self.prompt(uid,state,'confirm')
        if text != 'Save notification':
            raise ValueError('Choose Save notification or Cancel.')
        schedule_id = self.store.save_schedule(uid,draft)
        return self.view(uid,schedule_id)
