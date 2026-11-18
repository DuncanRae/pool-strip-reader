"""Home Assistant side of the pool app: sensors, reminders and weather.

Everything goes through the Supervisor's Core API proxy using the add-on's
own token. Outside Home Assistant (local preview, tests) this module does
nothing unless POOL_HA_URL points at a stand-in.
"""
import datetime as dt
import json
import os
import re
import threading
import urllib.error
import urllib.request

import chemistry

BASE = os.getenv('POOL_HA_URL', 'http://supervisor/core/api/')
STATUS = {'enabled': False, 'connected': False, 'lastPublish': None}
LATEST = {'waterTempC': None, 'airTempC': None, 'condition': None}
OPTIONS = {'weather_entity': '', 'water_temp_entity': '', 'notify_service': ''}
WAKE = threading.Event()
RAINY = {'rainy', 'pouring', 'lightning-rainy', 'hail', 'snowy-rainy'}
NOTIFICATION_PREFIX = 'pool_strip_reader_'
INTERVAL = 300


def enabled():
    return os.getenv('POOL_HA_ENABLED') == '1' and bool(os.getenv('SUPERVISOR_TOKEN'))


def call(path, body=None, timeout=10):
    request = urllib.request.Request(BASE + path, method='POST' if body is not None else 'GET',
        data=json.dumps(body).encode() if body is not None else None,
        headers={'Authorization': 'Bearer ' + os.getenv('SUPERVISOR_TOKEN', ''), 'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
    return json.loads(raw) if raw else None


def local_zone():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(os.getenv('TZ') or 'Africa/Johannesburg')
    except Exception:
        return dt.timezone(dt.timedelta(hours=2))


def parse(text):
    return dt.datetime.fromisoformat(text.replace('Z', '+00:00'))


# --- Pure helpers (unit tested) ----------------------------------------------
def latest_test(state):
    live = [t for t in state['tests'] if not t.get('voidedAt')]
    return max(live, key=lambda t: t['at']) if live else None


def next_due(state, analysis=None):
    """When the next test is due: the retest after a treatment, else the routine interval."""
    test = latest_test(state)
    if not test: return None
    hours = (analysis or {}).get('retestHours') or state['settings']['testIntervalDays'] * 24
    hours = min(hours, state['settings']['testIntervalDays'] * 24)
    return (parse(test['at']) + dt.timedelta(hours=hours)).isoformat()


def nudges(state, analysis, now=None):
    """Things worth telling the owner about right now, most pressing first."""
    now = now or dt.datetime.now(dt.timezone.utc)
    settings, out = state['settings'], []
    test = latest_test(state)
    if test:
        tested = parse(test['at'])
        age = (now - tested).total_seconds() / 3600
        treated = bool(analysis and analysis.get('steps'))
        retest = analysis.get('retestHours') if analysis else None
        if treated and retest and age >= retest:
            out.append({'key': 'retest', 'level': 'action', 'title': 'Time to retest',
                'text': 'The last test called for treatment. Test again to see where the water has settled.'})
        elif age >= settings['testIntervalDays'] * 24:
            days = int(age // 24)
            out.append({'key': 'overdue', 'level': 'action', 'title': 'Pool test due',
                'text': f'It has been {days} days since the last test.'})
        else:
            zone = local_zone()
            test_day = tested.astimezone(zone).date().isoformat()
            since = {day: row for day, row in state.get('weather', {}).get('days', {}).items() if day > test_day}
            hot = sum(1 for row in since.values() if (row.get('maxC') or -99) >= settings['hotDayC'])
            rain = sum(row.get('rainHours') or 0 for row in since.values())
            if hot >= 2:
                out.append({'key': 'heat', 'level': 'info', 'title': 'Hot spell since your last test',
                    'text': f'{hot} days at {settings["hotDayC"]}°C or more. Strong sun and warm water use chlorine up faster, so a quick test is worthwhile.'})
            if rain >= 3:
                out.append({'key': 'rain', 'level': 'info', 'title': 'Rain since your last test',
                    'text': f'About {rain:.0f} hours of rain. Heavy rain dilutes chlorine and stabiliser and washes in debris that uses chlorine up, so a quick test is worthwhile.'})
    strips = settings['strips']
    if strips.get('remaining') is not None and strips['remaining'] <= 3:
        out.append({'key': 'strips-low', 'level': 'info', 'title': 'Test strips running out',
            'text': 'No strips left in the bottle.' if strips['remaining'] <= 0 else f'{strips["remaining"]} strip{"s" if strips["remaining"] != 1 else ""} left in the bottle.'})
    if re.fullmatch(r'\d{4}-\d{2}', strips.get('expiry') or ''):
        year, month = map(int, strips['expiry'].split('-'))
        expires = dt.datetime(year + (month == 12), month % 12 + 1, 1, tzinfo=dt.timezone.utc)
        if now >= expires:
            out.append({'key': 'strips-expired', 'level': 'action', 'title': 'Test strips have expired',
                'text': 'Expired strips read wrong. Replace the bottle and update the expiry date in Settings.'})
        elif now >= expires - dt.timedelta(days=45):
            out.append({'key': 'strips-expiring', 'level': 'info', 'title': 'Test strips expire soon',
                'text': f'The bottle expires at the end of {expires - dt.timedelta(days=1):%B %Y}.'})
    done = {d.get('stepKey') for d in state['doses'] if test and d.get('testId') == test['id'] and not d.get('voidedAt')}
    for step in (analysis or {}).get('steps', []):
        stock = settings['products'][step['product']].get('stock')
        if stock is not None and step['key'] not in done and stock < step['amount']:
            out.append({'key': 'stock-' + step['product'], 'level': 'info', 'title': f'Not enough {step["productName"]}',
                'text': f'The plan needs {step["amountText"]} and you have {chemistry.amount_text(stock, step["unit"])} on hand.'})
    return out


def sensors(state, analysis, active, now=None):
    """(entity_id, state, attributes) for everything the app publishes."""
    test = latest_test(state)
    out = [('sensor.pool_test_strips_remaining', state['settings']['strips'].get('remaining'),
            {'friendly_name': 'Pool test strips remaining', 'icon': 'mdi:test-tube', 'unit_of_measurement': 'strips'}),
           ('sensor.pool_reminders', len(active),
            {'friendly_name': 'Pool reminders', 'icon': 'mdi:bell-ring-outline', 'reminders': [n['title'] + ': ' + n['text'] for n in active]})]
    if not test or not analysis: return out
    names = {'fc': ('pool_free_chlorine', 'Pool free chlorine', 'mdi:flask-outline'), 'ta': ('pool_total_alkalinity', 'Pool total alkalinity', 'mdi:waves'),
             'ph': ('pool_ph', 'Pool pH', 'mdi:ph'), 'cya': ('pool_stabiliser', 'Pool stabiliser', 'mdi:shield-sun-outline')}
    ideal = analysis['ideal']
    for key, (entity, friendly, icon) in names.items():
        reading = test['readings'][key]
        attributes = {'friendly_name': friendly, 'icon': icon, 'state_class': 'measurement', 'status': analysis['status'][key],
            'ideal_low': ideal[key][0], 'ideal_high': ideal[key][1], 'likely_low': reading.get('low', reading['value']),
            'likely_high': reading.get('high', reading['value']), 'confidence': reading.get('confidence', 'manual'), 'tested_at': test['at']}
        if chemistry.UNITS[key]: attributes['unit_of_measurement'] = chemistry.UNITS[key]
        out.append(('sensor.' + entity, reading['value'], attributes))
    actions = [f'{s["title"]}: {s["amountText"]} of {s["productName"]}' for s in analysis['steps']]
    out.append(('sensor.pool_water_status', 'Needs attention' if analysis['issues'] else 'Unclear reading' if analysis['unsure'] else 'Balanced',
        {'friendly_name': 'Pool water', 'icon': 'mdi:pool', 'headline': analysis['headline'], 'issues': analysis['issues'], 'unclear': analysis['unsure'],
         'actions': actions, 'notes': analysis['notes'], 'tested_at': test['at']}))
    out.append(('sensor.pool_last_tested', test['at'], {'friendly_name': 'Pool last tested', 'device_class': 'timestamp', 'icon': 'mdi:calendar-check'}))
    due = next_due(state, analysis)
    out.append(('sensor.pool_next_test_due', due, {'friendly_name': 'Pool next test due', 'device_class': 'timestamp', 'icon': 'mdi:calendar-clock'}))
    balance = analysis['balance']
    if balance.get('available'):
        out.append(('sensor.pool_water_balance', balance['index'], {'friendly_name': 'Pool water balance (LSI)', 'icon': 'mdi:scale-balance',
            'state_class': 'measurement', 'condition': balance['state'], 'description': balance['text']}))
    return out


# --- Background work ---------------------------------------------------------
def number(value):
    try:
        result = float(value)
        return result if result == result and abs(result) < 1000 else None
    except (TypeError, ValueError):
        return None


def sample_weather(app):
    """Track today's maximum temperature and hours of rain."""
    changed = False
    water = OPTIONS['water_temp_entity']
    if water:
        LATEST['waterTempC'] = number((call('states/' + water) or {}).get('state'))
    weather = OPTIONS['weather_entity']
    if not weather: return
    data = call('states/' + weather) or {}
    LATEST['condition'] = data.get('state')
    LATEST['airTempC'] = number(data.get('attributes', {}).get('temperature'))
    today = dt.datetime.now(local_zone()).date().isoformat()
    row = app.get_state().get('weather', {}).get('days', {}).get(today, {})
    new = dict(row)
    if LATEST['airTempC'] is not None and LATEST['airTempC'] > (row.get('maxC') if row.get('maxC') is not None else -99) + 0.4:
        new['maxC'] = LATEST['airTempC']
    if LATEST['condition'] in RAINY:
        new['rainHours'] = round((row.get('rainHours') or 0) + INTERVAL / 3600, 2)
    if new != row:
        def op(state):
            days = state.setdefault('weather', {}).setdefault('days', {})
            days[today] = new
            for old in sorted(days)[:-60]: del days[old]
            return state
        app.save_change(op)


def notify(app, active):
    """One push per reminder per day, in daylight hours; clear what has passed."""
    state = app.get_state()
    memory = state.get('reminders', {})
    sent, shown = dict(memory.get('sent', {})), set(memory.get('shown', []))
    local = dt.datetime.now(local_zone())
    today = local.date().isoformat()
    wanted = {n['key'] for n in active} if state['settings'].get('notifications', True) else set()
    for key in shown - wanted:
        call('services/persistent_notification/dismiss', {'notification_id': NOTIFICATION_PREFIX + key})
    for item in active:
        if item['key'] not in wanted: continue
        call('services/persistent_notification/create', {'notification_id': NOTIFICATION_PREFIX + item['key'], 'title': 'Pool · ' + item['title'], 'message': item['text']})
        service = OPTIONS['notify_service']
        if service and sent.get(item['key']) != today and 7 <= local.hour < 20:
            domain, _, name = service.partition('.')
            call(f'services/{domain}/{name}', {'title': 'Pool · ' + item['title'], 'message': item['text']})
            sent[item['key']] = today
    sent = {key: day for key, day in sent.items() if key in wanted}
    if sent != memory.get('sent', {}) or wanted != shown:
        def op(current):
            current['reminders'] = {'sent': sent, 'shown': sorted(wanted)}
            return current
        app.save_change(op)


def cycle(app):
    sample_weather(app)
    state = app.get_state()
    analysis = app.current_analysis(state)
    active = nudges(state, analysis)
    for entity, value, attributes in sensors(state, analysis, active):
        if value is not None:
            call('states/' + entity, {'state': value, 'attributes': attributes})
    notify(app, active)
    STATUS.update(connected=True, lastPublish=dt.datetime.now(dt.timezone.utc).isoformat())


def loop(app):
    while True:
        try:
            cycle(app)
        except (OSError, urllib.error.URLError, ValueError, KeyError) as error:
            STATUS.update(connected=False)
            print(f'Pool HA sync failed: {type(error).__name__}', flush=True)
        WAKE.wait(INTERVAL)
        WAKE.clear()


def start(app, options=None):
    for key in OPTIONS:
        value = str((options or {}).get(key, '') or '').strip()
        OPTIONS[key] = value if re.fullmatch(r'[a-z_]+\.[a-z0-9_]+', value) else ''
    STATUS['enabled'] = enabled()
    if STATUS['enabled']:
        threading.Thread(target=loop, args=(app,), name='pool-ha', daemon=True).start()
