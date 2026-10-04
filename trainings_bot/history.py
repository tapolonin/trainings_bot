"""Monthly overview and confirmed changes to existing records."""
from calendar import Calendar, month_name
from datetime import date
from uuid import uuid4

from .domain import (clock_time, euros, month_value, short_text,
                     training_amount, training_date, time_suggestions, time_prompt)

PAGE_SIZE = 10
EDIT_BUTTONS = ['Change date', 'Change gym', 'Change start', 'Change finish', 'Save changes', 'Cancel']
BOLD_DIGITS = str.maketrans('0123456789', '𝟬𝟭𝟮𝟯𝟰𝟱𝟲𝟳𝟴𝟵')
HISTORY_BUTTONS = ['Back', 'Main menu']
DAY_BUTTONS = ['Log this day', 'Edit training', 'Delete training',
               'Clear no-training record', 'Back', 'Main menu']


def month_calendar(month, statuses, today, token):
    year, number = map(int, month.split('-'))
    def button(label, action='noop'):
        return (label, f'cal:{token}:{action}')
    grid = [[button('‹', 'prev'), button(f'{month_name[number]} {year}'), button('›', 'next')],
            [button(label) for label in ('Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su')]]
    for week in Calendar(firstweekday=0).monthdayscalendar(year, number):
        row = []
        for day in week:
            if not day:
                row.append(button(' '))
                continue
            value = date(year, number, day)
            label = str(day)
            if value.isoformat() in statuses:
                label = '★' + label.translate(BOLD_DIGITS)
            elif value > today:
                label += '·'
            row.append(button(label, value.isoformat()))
        grid.append(row)
    return grid


class History:
    def __init__(self, store, reply):
        self.store, self.reply = store, reply

    def day_details(self, uid, day, state, page=0):
        rows = [r for r in self.store.entries(uid, state['month']) if r['day'] == day]
        page = max(0, min(page, max(0, (len(rows)-1)//PAGE_SIZE)))
        status = self.store.day_statuses(uid, state['month']).get(day, 'unknown')
        lines = [f'Day details · {day}']
        if rows:
            for row in rows[page*PAGE_SIZE:(page+1)*PAGE_SIZE]:
                lines.append(f"{row['gym']} · {row['start']}–{row['end']}")
            if len(rows) > PAGE_SIZE:
                lines.append(f'Entries page {page+1} of {(len(rows)+PAGE_SIZE-1)//PAGE_SIZE}')
        else:
            lines.append('No training recorded.' if status == 'no_training' else 'Missing information — this day has not been logged.')
        lines.append('\nTap another date in the calendar to see its details.')
        token = state['calendar_token']
        buttons = []
        navigation = []
        if page:
            navigation.append(('Previous entries', f'cal:{token}:detailprev'))
        if (page+1)*PAGE_SIZE < len(rows):
            navigation.append(('Next entries', f'cal:{token}:detailnext'))
        if navigation:
            buttons.insert(0, navigation)
        state.update(selected_day=day, detail_page=page, buttons=DAY_BUTTONS.copy())
        self.store.save_conversation(uid, state)
        return self.reply('\n'.join(lines), DAY_BUTTONS.copy(), day_details=True, detail_buttons=buttons)

    def overview(self, uid, month, today, page=0, notice='', view='calendar'):
        month = month_value(month)
        rows = self.store.entries(uid, month)
        page = max(0, min(page, max(0, (len(rows)-1)//PAGE_SIZE)))
        selected = rows[page*PAGE_SIZE:(page+1)*PAGE_SIZE]
        rate = self.store.rate(uid, month)
        amounts = [training_amount(r['start'], r['end'], rate) for r in rows]
        lines = [f'Trainings for {month} ({len(rows)})']
        if notice:
            lines.insert(0, notice + '\n')
        if not rows:
            lines.append(f'No trainings recorded for {month}.')
        for r in selected:
            amount = training_amount(r['start'], r['end'], rate)
            lines.append(f"{r['day']} · {r['gym']} · {r['start']}–{r['end']} · {euros(amount)}")
        lines.append(f'\nTotal earned: {euros(sum(amounts))}')
        if len(rows) > PAGE_SIZE:
            lines.append(f'Entries page {page+1} of {(len(rows)+PAGE_SIZE-1)//PAGE_SIZE}')
        if view == 'calendar':
            lines = ([notice] if notice else []) + [f'Calendar · {month}', f'{len(rows)} training sessions recorded.']
        else:
            lines[0] = f'Earnings for {month} ({len(rows)} trainings)'
        statuses = self.store.day_statuses(uid, month)
        buttons = HISTORY_BUTTONS.copy()
        token = uuid4().hex[:12]
        state = dict(flow='history', view=view, month=month, page=page, ids=[r['id'] for r in selected], buttons=buttons, calendar_token=token)
        self.store.save_conversation(uid, state)
        calendar = month_calendar(month, statuses, today, token)
        if view == 'earnings':
            calendar = calendar[:1]
        calendar.append([('Choose month', f'cal:{token}:choosemonth')])
        navigation = []
        if view == 'earnings' and page:
            navigation.append(('Previous entries', f'cal:{token}:pageprev'))
        if view == 'earnings' and (page+1)*PAGE_SIZE < len(rows):
            navigation.append(('Next entries', f'cal:{token}:pagenext'))
        if navigation:
            calendar.append(navigation)
        return self.reply('\n'.join(lines), buttons,
                          calendar=calendar,
                          calendar_title=('📅 Monthly calendar\n★𝟭 = information recorded · 1 = missing information · 1· = future\nTap a day to view or update it.' if view == 'calendar' else ''))

    def draft(self, uid, state):
        state['flow'] = 'history_edit'
        state['buttons'] = EDIT_BUTTONS
        self.store.save_conversation(uid, state)
        e = state['entry']
        if e['end'] > e['start']:
            amount = training_amount(e['start'], e['end'], self.store.rate(uid, e['day'][:7]))
            earnings = f"Earnings with this month's rate: {euros(amount)}"
        else:
            earnings = 'Finish must be after start. Change the times before saving.'
        return self.reply(f"Edit training\n{e['day']} · {e['gym']}\n{e['start']}–{e['end']}\n"
                          f"{earnings}\nChanges are saved only when you choose Save changes.", EDIT_BUTTONS)

    def open_training(self, uid, state, row):
        state.update(id=row['id'], entry={k:row[k] for k in ('day','gym','start','end')})
        if state['action'] == 'Edit training':
            return self.draft(uid, state)
        state.update(flow='history_delete', buttons=['Confirm delete', 'Cancel'])
        self.store.save_conversation(uid, state)
        return self.reply(f"Delete training?\n{row['day']} · {row['gym']} · {row['start']}–{row['end']}\n"
                          'The day will become missing information if no training remains.', state['buttons'])

    def handle(self, uid, text, state, today):
        flow = state['flow']
        month = state['month']
        if flow == 'history':
            if text in ('Log this day', 'Clear no-training record'):
                day = state.get('selected_day')
                if not day:
                    raise ValueError('Tap a day in the calendar first.')
                if text == 'Log this day':
                    self.store.save_conversation(uid, dict(flow='day_status', day=day))
                    return self.reply(f'Did you have training on {day}?', ['Training', 'No training', 'Cancel'])
                if self.store.day_statuses(uid, month).get(day) != 'no_training':
                    raise ValueError('The selected day has no no-training record to clear.')
                state.update(flow='history_clear', day=day, buttons=['Confirm clear','Cancel'])
                self.store.save_conversation(uid, state)
                return self.reply(f'Clear the no-training record for {day}? The day will become missing information.', state['buttons'])
            if text in ('Previous month', 'Next month'):
                year, number = map(int, month.split('-'))
                index = year*12 + number-1 + (-1 if text == 'Previous month' else 1)
                year, number = divmod(index, 12)
                if not 1 <= year <= 9999:
                    raise ValueError('There are no more months in that direction.')
                return self.overview(uid, f'{year:04d}-{number+1:02d}', today, view=state.get('view','calendar'))
            if text in ('Previous entries', 'Next entries'):
                return self.overview(uid, month, today, state['page'] + (-1 if text == 'Previous entries' else 1), view=state.get('view','calendar'))
            if text == 'Choose month':
                state.update(flow='history_month', buttons=['Cancel'])
                self.store.save_conversation(uid, state)
                return self.reply('Which month? Enter YYYY-MM, for example 2026-08.', ['Cancel'])
            if text in ('Edit training', 'Delete training'):
                if not state.get('selected_day'):
                    raise ValueError('Tap a day in Calendar first.')
                rows = [r for r in self.store.entries(uid, month) if r['day'] == state['selected_day']]
                if not rows:
                    raise ValueError('There is no training on the selected day.')
                state['action'] = text
                if len(rows) == 1:
                    return self.open_training(uid, state, rows[0])
                # Keep older multi-session days accessible without removing data.
                choices = {f"{r['start']}–{r['end']} · {r['gym']}":r['id'] for r in rows}
                buttons = list(choices) + ['Cancel']
                state.update(flow='history_select', choices=choices, ids=list(choices.values()), buttons=buttons)
                self.store.save_conversation(uid, state)
                return self.reply('This day has older multiple entries. Choose the session by time and location.', buttons)
            if text == 'Correct day':
                state.update(flow='history_day', buttons=['Cancel'])
                self.store.save_conversation(uid, state)
                return self.reply(f'Which day in {month}? Enter its day number or full date.', ['Cancel'])
            raise ValueError('Choose a month, Edit training, Delete training, or Correct day.')
        if flow == 'history_month':
            return self.overview(uid, month_value(text), today, view=state.get('view','calendar'))
        if flow == 'history_select':
            if state.get('choices'):
                if text not in state['choices']:
                    raise ValueError('Choose a session by time and location.')
                return self.open_training(uid, state, self.store.entry(uid, state['choices'][text]))
            value = text.removeprefix('Training #').removeprefix('#')
            if not value.isdigit() or int(value) not in state['ids']:
                raise ValueError('Choose a training number from this page.')
            row = self.store.entry(uid, int(value))
            return self.open_training(uid, state, row)
        if flow == 'history_delete':
            if text != 'Confirm delete':
                raise ValueError('Choose Confirm delete or Cancel.')
            self.store.delete_training(uid, state['id'])
            return self.overview(uid, month, today, state['page'], 'Training deleted.')
        if flow == 'history_edit':
            if text == 'Save changes':
                training_date(state['entry']['day'], today)
                self.store.edit_training(uid, state['id'], state['entry'])
                return self.overview(uid, state['entry']['day'][:7], today, notice='Training updated.')
            fields = {'Change date':'day', 'Change gym':'gym', 'Change start':'start', 'Change finish':'end'}
            if text not in fields:
                raise ValueError('Choose a field to change, Save changes, or Cancel.')
            key = fields[text]
            prompts = {'day':'Enter the new date (DD.MM.YYYY or YYYY-MM-DD).',
                       'gym':'Choose a gym, Wettkampf, or type a location.',
                       'start':'What time did it start? For example 9 or 09:30.',
                       'end':'What time did it finish? For example 12 or 12:30.'}
            buttons = ([g['name'] for g in self.store.gyms(uid)] + ['Add new gym','Wettkampf'] if key == 'gym' else time_suggestions(state['entry']['day'],finish=key=='end') if key in ('start','end') else []) + ['Cancel']
            if key in ('start','end'):
                prompts[key] = time_prompt(finish=key=='end', suggestions=len(buttons)>1)
            state.update(flow='history_field', key=key, buttons=buttons)
            self.store.save_conversation(uid, state)
            return self.reply(prompts[key], buttons)
        if flow == 'history_new_gym':
            gym = short_text(text)
            if len(self.store.gyms(uid)) >= 30:
                raise ValueError('Archive a gym in Settings before adding another (30 maximum).')
            self.store.add_gym(uid,gym)
            state['entry']['gym'] = gym
            return self.draft(uid,state)
        if flow == 'history_field':
            key = state['key']
            if key == 'gym' and text == 'Add new gym':
                state.update(flow='history_new_gym',buttons=['Cancel'])
                self.store.save_conversation(uid,state)
                return self.reply('Enter the new gym name. It will be saved for future trainings.', ['Cancel'])
            if key == 'gym' and text.casefold() in ('wettkampf','wettkämpf'):
                state.update(flow='history_city', buttons=['Cancel'])
                self.store.save_conversation(uid, state)
                return self.reply('Which city was the competition in?', ['Cancel'])
            value = training_date(text, today) if key == 'day' else short_text(text) if key == 'gym' else clock_time(text)
            state['entry'][key] = value
            return self.draft(uid, state)
        if flow == 'history_city':
            state['entry']['gym'] = short_text('Wettkampf ' + short_text(text))
            return self.draft(uid, state)
        if flow == 'history_day':
            value = f'{month}-{int(text):02d}' if text.isascii() and text.isdigit() and len(text) <= 2 else text
            day = training_date(value, today)
            if day[:7] != month:
                raise ValueError(f'Choose a day in {month}, or cancel and choose another month.')
            status = self.store.day_statuses(uid, month).get(day, 'unknown')
            buttons = ['Log this day']
            if status == 'no_training':
                buttons += ['Clear no-training record']
            buttons += ['Back', 'Main menu']
            state.update(flow='history_day_action', day=day, buttons=buttons)
            self.store.save_conversation(uid, state)
            label = {'training':'Training recorded', 'no_training':'No training recorded', 'unknown':'Missing information'}[status]
            return self.reply(f'{day}: {label}.', buttons)
        if flow == 'history_day_action':
            if text == 'Log this day':
                self.store.save_conversation(uid, dict(flow='day_status', day=state['day']))
                return self.reply(f"Did you have training on {state['day']}?", ['Training', 'No training', 'Cancel'])
            if text == 'Clear no-training record' and text in state['buttons']:
                state.update(flow='history_clear', buttons=['Confirm clear', 'Cancel'])
                self.store.save_conversation(uid, state)
                return self.reply(f"Clear the no-training record for {state['day']}? The day will become missing information.", state['buttons'])
            raise ValueError('Choose one of the day actions or Cancel.')
        if flow == 'history_clear':
            if text != 'Confirm clear':
                raise ValueError('Choose Confirm clear or Cancel.')
            self.store.clear_no_training(uid, state['day'])
            return self.overview(uid, month, today, notice='No-training record cleared.')
        raise ValueError('Open View trainings to continue.')
