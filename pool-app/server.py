"""Pool Strip Reader. Python stdlib + SQLite; no cloud service required.

Same hosting model as 43 Links Power: Home Assistant ingress on one port and,
optionally, a password-protected HTTPS listener of its own on another.

The app is installed for one owner, who can add other people. Each person has
a pool of their own; Home Assistant only ever sees the owner's.
"""
import base64, contextlib, copy, datetime as dt, hashlib, hmac, html, http.client, ipaddress, json, math, os, pathlib, re, secrets, shutil, socket, sqlite3, sys, threading, time
from html.parser import HTMLParser
from http import cookies
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from types import SimpleNamespace
from urllib.parse import urlsplit, parse_qs

import chemistry
import ha_pool
import tls_manager

ROOT = pathlib.Path(__file__).resolve().parent
DATA = pathlib.Path(os.getenv('POOL_DATA_DIR', str(ROOT / 'data')))
DB = DATA / 'pool.sqlite3'
PHOTOS = DATA / 'photos'
HOST = os.getenv('HOST', '127.0.0.1')
PORT = int(os.getenv('PORT', '8745'))
DIRECT_PORT = 8746
PASSWORD = os.getenv('APP_PASSWORD', '')
USERNAME = (os.getenv('APP_USERNAME') or 'owner').strip().lower()
USERNAME_RULE = re.compile(r'[a-z0-9][a-z0-9._-]{1,31}')
OWNER = 1                # row 1 of `state` and `users`: whoever the app is installed for
PBKDF2_ROUNDS = 600000
DUMMY_SECRET = ''
ORIGIN = os.getenv('APP_ORIGIN', f'http://127.0.0.1:{PORT}').rstrip('/')
INGRESS = os.getenv('POOL_INGRESS') == '1'
INGRESS_PEER = '172.30.32.2'
APP_VERSION = '0.9.1'
MAX_BODY = 6 * 1024 * 1024        # a test, a calibration or a shop's printout: room for one photo
SMALL_BODY = 16 * 1024            # every other request from a signed-in person is a few fields
SIGN_IN_BODY = 4096               # anything sent before signing in is a username and a password, nothing more
MAX_HEADERS = 32 * 1024           # the request line and all its headers together
MAX_RECORDS = 5000                # tests, and doses, one person can hold: decades of use, but not a full disk
MAX_CONNECTIONS = 48              # requests served at once per listener; the rest are turned away at the door
REQUEST_TIMEOUT = 20              # seconds a connection may sit silent before it is dropped
REQUEST_DEADLINE = 120            # seconds a request may take from first byte to last, however it is trickled
SESSION_REFRESH = 86400           # a session older than this is swapped for a fresh one when the app is opened
LEGACY_SESSION = 30 * 86400       # before 0.7.0 a session carried its expiry, 30 days after it was issued
KEEP_PHOTOS = 300                 # strip photos kept per person, newest first
PHOTO_DAYS = 92                   # a strip photo is deleted three months after it was taken; its readings stay
PHOTO_BYTES = 150 * 1024 * 1024   # and one person's photos never take more room than this
COOKIE_AGE = 400 * 86400          # the longest a browser keeps a cookie; renewed every time the app is opened
LOCK = threading.RLock()
SECRET = b''
# Failed sign-ins in the last five minutes, by who was asking, by the username tried, and in all.
LOGIN_ATTEMPTS = {}
LOGIN_WINDOW = 300
LOGIN_LIMITS = {'from': 10, 'user': 10, 'all': 100}
LOGIN_LOCK = threading.Lock()
# A password check is deliberately slow. Two at a time is plenty, and stops a flood of them tying up the machine.
LOGIN_SLOTS = threading.BoundedSemaphore(2)
# Taking in and parsing a photo-sized request costs many times its size in memory, so two at a time.
UPLOAD_SLOTS = threading.BoundedSemaphore(2)
REFUSAL_LOGGED = 0
# Pool shop tests. Shops that test with a LaMotte meter (Builders among them) send a link to a WaterLink
# report. The page behind that link gets its figures from this one host, and so does the app.
LAB_API = 'wls-api.waterlinkconnect.com'
LAB_PAGE = 'https://solutions.waterlinkconnect.com/treatment-reports/'
LAB_LINK = re.compile(r'(?:' + re.escape(LAB_PAGE) + r')?([0-9a-fA-F]{32,64})/?')
LAB_REPORT_BYTES = 256 * 1024     # a report is about 15 kB; anything far beyond that is not one
LAB_PHOTOS = 40                   # pictures of printed reports kept per person, newest first
LAB_FETCHES = {}                  # when each person last had a report fetched
LAB_WINDOW, LAB_LIMIT = 600, 12   # a dozen fetches in ten minutes per person, so the app cannot be used to hammer the shop's service
LAB_SLOTS = threading.BoundedSemaphore(2)
# What a shop's meter measures: key, name, unit, the lowest and highest value it could possibly be, and
# WaterLink's number for it. The first four are the app's own readings. The rest are kept with the test and
# shown, but no dose is ever worked out from them.
LAB_FACTORS = (
    ('fc', 'Free chlorine', 'ppm', 0, 50, 2), ('ph', 'pH', '', 4, 11, 8), ('ta', 'Total alkalinity', 'ppm', 0, 1000, 9), ('cya', 'Stabiliser', 'ppm', 0, 1000, 12),
    ('tc', 'Total chlorine', 'ppm', 0, 50, 3), ('cc', 'Combined chlorine', 'ppm', 0, 50, 4), ('taAdjusted', 'Adjusted alkalinity', 'ppm', 0, 1000, 63),
    ('ch', 'Calcium hardness', 'ppm', 0, 5000, 11), ('th', 'Total hardness', 'ppm', 0, 5000, 10), ('copper', 'Copper', 'ppm', 0, 50, 16), ('iron', 'Iron', 'ppm', 0, 50, 13),
    ('phosphate', 'Phosphate', 'ppb', 0, 100000, 18), ('salt', 'Salt', 'ppm', 0, 100000, 19), ('borate', 'Borate', 'ppm', 0, 1000, 17),
)
# What the person at the counter can tick about the water, by WaterLink's number.
LAB_CONDITIONS = {1: 'Green colour', 2: 'Yellow colour', 3: 'Black colour', 4: 'Brown colour', 5: 'Pink colour', 6: 'Cloudy water',
                  7: 'Foamy water', 8: 'Stains', 9: 'Dirty surface', 10: 'Dirty filter', 11: 'Dirty cover'}
CHART = json.loads((ROOT / 'chart.json').read_text(encoding='utf-8'))


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


@contextlib.contextmanager
def connect():
    db = sqlite3.connect(DB, timeout=15)
    try:
        with db: yield db
    finally: db.close()


def fill_defaults(target, defaults):
    """Add settings introduced by newer versions without touching saved ones."""
    for key, value in defaults.items():
        if key not in target: target[key] = copy.deepcopy(value)
        elif isinstance(value, dict) and isinstance(target[key], dict): fill_defaults(target[key], value)
    return target


def seed_state(owner):
    state = {'schemaVersion': 1, 'settings': copy.deepcopy(chemistry.DEFAULT_SETTINGS), 'tests': [], 'doses': [], 'weather': {'days': {}}, 'reminders': {'sent': {}, 'shown': []}}
    # The defaults describe the owner's pool. Anyone else must say what theirs
    # is before the app works out a single dose for it.
    if not owner: state['needsSetup'] = True
    return state


def init():
    global SECRET, DUMMY_SECRET
    DATA.mkdir(parents=True, exist_ok=True); PHOTOS.mkdir(exist_ok=True)
    key = DATA / '.session-key'
    # A key cut short by an interrupted first write would sign sessions with next to nothing: replace it.
    if not key.exists() or len(key.read_bytes()) != 32:
        key.write_bytes(secrets.token_bytes(32)); key.chmod(0o600)
    SECRET = key.read_bytes()
    DUMMY_SECRET = hash_password(secrets.token_hex(16))
    with connect() as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('BEGIN IMMEDIATE')
        # Before 0.5.0 the table could only hold row 1. That row stays the owner's.
        old = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='state'").fetchone()
        if old and 'CHECK' in old[0].upper():
            db.execute('ALTER TABLE state RENAME TO state_single')
            db.execute('CREATE TABLE state (id INTEGER PRIMARY KEY, revision INTEGER NOT NULL, payload TEXT NOT NULL)')
            db.execute('INSERT INTO state SELECT id, revision, payload FROM state_single')
            db.execute('DROP TABLE state_single')
        db.execute('CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY, revision INTEGER NOT NULL, payload TEXT NOT NULL)')
        db.execute('INSERT OR IGNORE INTO state VALUES (?, 1, ?)', (OWNER, json.dumps(seed_state(owner=True))))
        # AUTOINCREMENT: an id is never handed out twice, so a removed person's
        # session or data can never become someone else's.
        db.execute('CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE, name TEXT NOT NULL, secret TEXT NOT NULL, disabled INTEGER NOT NULL DEFAULT 0, createdAt TEXT NOT NULL, lastSeenAt TEXT)')
        # The owner's row only reserves id 1; their sign-in comes from the app configuration.
        db.execute("INSERT OR IGNORE INTO users (id, username, name, secret, createdAt) VALUES (?, '*owner', 'Owner', '', ?)", (OWNER, now()))
        # 0.7.0: signing out ends every session issued up to that moment (the owner's is kept on their placeholder row).
        if 'sessionsFrom' not in [row[1] for row in db.execute('PRAGMA table_info(users)')]:
            db.execute('ALTER TABLE users ADD COLUMN sessionsFrom INTEGER NOT NULL DEFAULT 0')
        ids = [row[0] for row in db.execute('SELECT id FROM state')]
    for uid in ids: refresh_settings(uid)


def refresh_settings(uid):
    current = get_state(uid)
    filled = fill_defaults(copy.deepcopy(current['settings']), chemistry.DEFAULT_SETTINGS)
    # Stock tracking was removed from the app (0.3.1): nobody keeps it up to date.
    # Clear anything entered earlier so it cannot raise "not enough" reminders.
    for product in filled['products'].values(): product['stock'] = None
    # Ideal ranges and aims are not user settings: they follow the published
    # guidance built into this version, so an update always refreshes them.
    filled['ideal'] = copy.deepcopy(chemistry.DEFAULT_SETTINGS['ideal'])
    filled['aim'] = copy.deepcopy(chemistry.DEFAULT_SETTINGS['aim'])
    if filled != current['settings']:
        def op(state):
            state['settings'] = filled
            return state
        save_change(op, uid)


def get_state(uid=OWNER):
    with connect() as db:
        row = db.execute('SELECT revision, payload FROM state WHERE id=?', (uid,)).fetchone()
    if not row: raise LookupError('This person has no pool data.')
    state = json.loads(row[1]); state['revision'] = row[0]
    return state


def save_change(operation, uid=OWNER):
    with LOCK:
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            revision, payload = db.execute('SELECT revision, payload FROM state WHERE id=?', (uid,)).fetchone()
            result = operation(json.loads(payload))
            result.pop('revision', None)
            db.execute('UPDATE state SET revision=?, payload=? WHERE id=?', (revision + 1, json.dumps(result, ensure_ascii=False), uid))
    return get_state(uid)


def photo_dir(uid):
    """The owner's photos stay where they always were; everyone else has a folder."""
    return PHOTOS if uid == OWNER else PHOTOS / f'u{uid}'


# --- People --------------------------------------------------------------------
# The owner is whoever installed the app. The Home Assistant sidebar is always
# theirs, and on the standalone address they sign in with the username and
# password from the app configuration. Everyone else is added by the owner
# inside the app, and gets settings, tests, doses, photos and a light-box
# calibration of their own. Nobody can read another person's pool.
PERSON_FIELDS = ('id', 'username', 'name', 'secret', 'disabled', 'createdAt', 'lastSeenAt')


def hash_password(password, salt=None, rounds=None):
    salt = salt or secrets.token_bytes(16); rounds = rounds or PBKDF2_ROUNDS
    return f'pbkdf2${rounds}${salt.hex()}${hashlib.pbkdf2_hmac("sha256", password.encode(), salt, rounds).hex()}'


def password_matches(password, secret):
    try:
        _, rounds, salt, _ = secret.split('$')
        return hmac.compare_digest(hash_password(password, bytes.fromhex(salt), int(rounds)), secret)
    except ValueError: return False


def find_person(uid=None, username=None):
    """Anyone but the owner, by id or by username."""
    with connect() as db:
        row = db.execute(f'SELECT {", ".join(PERSON_FIELDS)} FROM users WHERE id != ? AND ' + ('id=?' if uid is not None else 'username=?'), (OWNER, uid if uid is not None else username)).fetchone()
    return dict(zip(PERSON_FIELDS, row)) if row else None


def people():
    with connect() as db:
        rows = db.execute(f'SELECT {", ".join(PERSON_FIELDS)} FROM users WHERE id != ? ORDER BY name COLLATE NOCASE', (OWNER,)).fetchall()
    return [{**{k: v for k, v in zip(PERSON_FIELDS, row) if k != 'secret'}, 'disabled': bool(row[4])} for row in rows]


def owner_person(session=False):
    return {'id': OWNER, 'username': USERNAME, 'name': '', 'owner': True, 'session': session}


def session_mark(uid, secret=None):
    """What a session is tied to: changing the password ends every session signed with the old one."""
    return hashlib.sha256(PASSWORD.encode()).hexdigest() if uid == OWNER else secret


def session_value(uid, mark, issued=None):
    """A signed session. It records when it was issued and has no expiry of its own. It ends when the
    password it was signed against changes, when access is switched off, when the person signs out
    (which ends their sessions everywhere), or when it has gone unused for as long as a browser keeps a cookie."""
    issued = issued or str(max(int(time.time()), sessions_from(uid)))
    return f'{uid}.{issued}.' + hmac.new(SECRET, f'{uid}.{issued}.{mark}'.encode(), hashlib.sha256).hexdigest()


def sessions_from(uid):
    """The moment this person last signed out: sessions issued before it are no longer accepted."""
    with connect() as db:
        row = db.execute('SELECT sessionsFrom FROM users WHERE id=?', (uid,)).fetchone()
    return row[0] if row else 0


def end_sessions(uid):
    with connect() as db: db.execute('UPDATE users SET sessionsFrom=? WHERE id=?', (int(time.time()) + 1, uid))


def legacy_session(issued):
    """Before 0.7.0 a session carried its expiry where the issue time now is, so its number lies in the future."""
    return issued > time.time() + 300


def session_issued(issued):
    """When a session was issued; for a pre-0.7.0 one, 30 days before the expiry it carries."""
    return issued - LEGACY_SESSION if legacy_session(issued) else issued


def sign_in(username, password):
    """The person these credentials belong to, or None. Always costs one password hash, known username or not."""
    try: password.encode()
    except UnicodeEncodeError: return None   # text that cannot be a password, whoever it is tried against
    row = find_person(username=username) if username != USERNAME and USERNAME_RULE.fullmatch(username) else None
    hashed = password_matches(password, row['secret'] if row else DUMMY_SECRET)
    if username == USERNAME:
        return owner_person(True) if PASSWORD and hmac.compare_digest(password.encode(), PASSWORD.encode()) else None
    if not row or row['disabled'] or not hashed: return None
    with connect() as db: db.execute('UPDATE users SET lastSeenAt=? WHERE id=?', (now(), row['id']))
    return {'id': row['id'], 'username': row['username'], 'name': row['name'], 'owner': False, 'session': True, 'secret': row['secret']}


def password_attempt(keys):
    """Counts one password attempt against each key (the visitor, the username, everyone) and returns a
    ticket, or None when any of them has used up its attempts. The attempt is counted before the password
    is checked, so a burst of guesses cannot slip under the limit; a correct password hands it back."""
    global REFUSAL_LOGGED
    moment = time.time()
    with LOGIN_LOCK:
        for key in [key for key, times in LOGIN_ATTEMPTS.items() if times[-1] <= moment - LOGIN_WINDOW]: del LOGIN_ATTEMPTS[key]
        recent = {key: [t for t in LOGIN_ATTEMPTS.get(key, []) if t > moment - LOGIN_WINDOW] for key in keys}
        if any(len(times) >= LOGIN_LIMITS[key.split(':', 1)[0]] for key, times in recent.items()):
            if moment - REFUSAL_LOGGED > 60:   # one line a minute, however hard someone is hammering
                REFUSAL_LOGGED = moment
                print(f'Pool: password attempts refused for five minutes, too many wrong ones (latest from {keys[0].split(":", 1)[1]}).', flush=True)
            return None
        for key in keys: LOGIN_ATTEMPTS[key] = recent[key] + [moment]
        return moment


def password_accepted(keys, ticket):
    """Clears the visitor's and the username's counts, and takes this one attempt back out of the total."""
    with LOGIN_LOCK:
        for key in keys[:2]: LOGIN_ATTEMPTS.pop(key, None)
        times = LOGIN_ATTEMPTS.get(keys[2], [])
        if ticket in times: times.remove(ticket)
        if not times: LOGIN_ATTEMPTS.pop(keys[2], None)


def clean_password(value):
    if not isinstance(value, str) or not 10 <= len(value) <= 200: raise ValueError('A password needs at least 10 characters.')
    try: value.encode()
    except UnicodeEncodeError: raise ValueError('That password contains characters that cannot be stored.')
    return value


def add_person(body):
    username, name = body.get('username'), body.get('name')
    if not isinstance(username, str) or not USERNAME_RULE.fullmatch(username.strip().lower()):
        raise ValueError('A username is 2 to 32 letters, digits, dots, dashes or underscores, with no spaces.')
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 40: raise ValueError('A name is 1 to 40 characters.')
    username, secret = username.strip().lower(), hash_password(clean_password(body.get('password')))
    with LOCK:
        with connect() as db:
            if username == USERNAME or db.execute('SELECT 1 FROM users WHERE username=?', (username,)).fetchone(): raise ValueError('That username is already in use.')
            uid = db.execute('INSERT INTO users (username, name, secret, createdAt) VALUES (?, ?, ?, ?)', (username, name.strip(), secret, now())).lastrowid
            db.execute('INSERT INTO state VALUES (?, 1, ?)', (uid, json.dumps(seed_state(owner=False))))
    return uid


def change_person(body):
    uid, action = body.get('id'), body.get('action')
    if isinstance(uid, bool) or not isinstance(uid, int) or not find_person(uid): raise ValueError('Person not found.')
    if action == 'password':
        # A new password also signs them out everywhere, because sessions are tied to the old one.
        change = ('UPDATE users SET secret=? WHERE id=?', (hash_password(clean_password(body.get('password'))), uid))
    elif action == 'disable':
        if not isinstance(body.get('disabled'), bool): raise ValueError('Access must be on or off.')
        change = ('UPDATE users SET disabled=? WHERE id=?', (int(body['disabled']), uid))
    elif action == 'remove':
        with LOCK:
            with connect() as db:
                db.execute('DELETE FROM users WHERE id=?', (uid,)); db.execute('DELETE FROM state WHERE id=?', (uid,))
        shutil.rmtree(photo_dir(uid), ignore_errors=True)
        return
    else: raise ValueError('Unknown action.')
    with connect() as db: db.execute(*change)


def change_own_password(person, body):
    if person['owner']: raise ValueError('Your password is set in the app configuration in Home Assistant.')
    new, current = clean_password(body.get('password')), str(body.get('current', ''))[:1024]
    try: current.encode()
    except UnicodeEncodeError: current = ''
    if not password_matches(current, person['secret']): raise ValueError('The current password is not right.')
    secret = hash_password(new)
    with connect() as db: db.execute('UPDATE users SET secret=? WHERE id=?', (secret, person['id']))
    return secret


# --- Validation ----------------------------------------------------------------
def number(value, name, low, high, nullable=False):
    if value is None and nullable: return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f'{name} must be a number between {low:g} and {high:g}.')
    return value


def moment(value):
    if not isinstance(value, str) or len(value) > 40: raise ValueError('Invalid date.')
    try: when = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError: raise ValueError('Invalid date.')
    if when.tzinfo is None: raise ValueError('A timezone is required.')
    if when > dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=5): raise ValueError('A test cannot be dated in the future.')
    return when.astimezone(dt.timezone.utc).isoformat()


def clean_readings(body):
    if not isinstance(body, dict) or set(body) != set(chemistry.PARAMS): raise ValueError('All four readings are required.')
    out = {}
    for key in chemistry.PARAMS:
        raw = body[key]
        if not isinstance(raw, dict): raise ValueError('Invalid reading.')
        low, high = chemistry.SCALE[key]
        label = chemistry.LABELS[key]
        row = {'value': number(raw.get('value'), label, low, high)}
        for edge in ('low', 'high'):
            if raw.get(edge) is not None: row[edge] = number(raw[edge], label, low, high)
        row['low'] = min(row.get('low', row['value']), row['value']); row['high'] = max(row.get('high', row['value']), row['value'])
        row['confidence'] = raw.get('confidence') if raw.get('confidence') in ('good', 'fair', 'poor') else 'manual'
        if raw.get('manual') is True: row['manual'] = True; row['confidence'] = 'manual'
        row['atMin'] = row['value'] <= low; row['atMax'] = row['value'] >= high
        lab = raw.get('lab')
        if isinstance(lab, list) and len(lab) == 3 and all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and abs(v) < 500 for v in lab):
            row['lab'] = [round(v, 1) for v in lab]
        out[key] = row
    return out


def validate_settings(settings):
    number(settings.get('volumeLitres'), 'Pool volume', 1000, 2000000)
    if settings.get('surface') not in chemistry.SURFACES: raise ValueError('Choose the pool surface from the list.')
    number(settings.get('calciumHardness'), 'Calcium hardness', 10, 2000, nullable=True)
    number(settings.get('tds'), 'TDS', 100, 20000)
    number(settings.get('waterTempC'), 'Water temperature', 0, 45)
    number(settings.get('hotDayC'), 'Hot-day temperature', 20, 50)
    if isinstance(settings.get('testIntervalDays'), bool) or not isinstance(settings.get('testIntervalDays'), int) or not 1 <= settings['testIntervalDays'] <= 30:
        raise ValueError('Test interval must be between 1 and 30 days.')
    if not isinstance(settings.get('notifications'), bool): raise ValueError('Notifications must be on or off.')
    strips = settings.get('strips')
    if not isinstance(strips, dict): raise ValueError('Invalid strip details.')
    number(strips.get('remaining'), 'Strips remaining', 0, 500)
    if not re.fullmatch(r'20\d{2}-(0[1-9]|1[0-2])', strips.get('expiry') or ''): raise ValueError('Strip expiry must be a month, for example 2028-04.')
    products = settings.get('products')
    if not isinstance(products, dict) or set(products) != set(chemistry.DEFAULT_SETTINGS['products']): raise ValueError('Invalid products.')
    for key, product in products.items():
        if not isinstance(product.get('name'), str) or not 1 <= len(product['name'].strip()) <= 60: raise ValueError('Product names must be 1 to 60 characters.')
        number(product.get('strength'), product['name'] + ' strength', 1, 100)
        number(product.get('stock'), product['name'] + ' stock', 0, 1000000, nullable=True)
    if products['acid'].get('kind') not in ('liquid', 'dry'): raise ValueError('Acid must be liquid or dry.')
    if products['acid']['kind'] == 'liquid' and products['acid']['strength'] > 37: raise ValueError('Liquid pool acid is at most 37% hydrochloric acid.')
    return settings


def merge(target, patch, shape):
    """Apply a partial settings update, accepting only known keys."""
    if not isinstance(patch, dict): raise ValueError('Invalid settings.')
    for key, value in patch.items():
        if key not in shape or key in ('ideal', 'aim'): raise ValueError(f'Unknown setting: {key}.')
        if isinstance(shape[key], dict): merge(target[key], value, shape[key])
        else: target[key] = value.strip() if isinstance(value, str) else value
    return target


def clean_chart(body):
    """A chart measured in the light box: one Lab colour per swatch of the built-in chart."""
    params = body.get('params')
    if not isinstance(params, dict) or set(params) != set(CHART['params']): raise ValueError('Calibration must cover all four pads.')
    out = {}
    for key, meta in CHART['params'].items():
        rows = params[key]
        if not isinstance(rows, list) or len(rows) != len(meta['swatches']): raise ValueError(f'{meta["label"]} needs {len(meta["swatches"])} colours.')
        for lab in rows:
            if not isinstance(lab, list) or len(lab) != 3 or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in lab): raise ValueError('Invalid colour.')
            if not 0 <= lab[0] <= 110 or abs(lab[1]) > 130 or abs(lab[2]) > 130: raise ValueError('A colour is outside the possible range.')
        out[key] = [[round(v, 1) for v in lab] for lab in rows]
    chart = {'calibratedAt': now(), 'params': out}
    marks = body.get('marks')
    if marks is not None:
        # Where the chart sat in the calibration picture, as fractions of it (three corner swatches).
        if not isinstance(marks, dict) or set(marks) != {'a', 'b', 'c'}: raise ValueError('Invalid chart position.')
        for point in marks.values():
            if not isinstance(point, dict) or set(point) != {'x', 'y'} or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not -1 <= v <= 2 for v in point.values()): raise ValueError('Invalid chart position.')
        chart['marks'] = {name: {axis: round(v, 4) for axis, v in point.items()} for name, point in marks.items()}
    return chart


def effective_chart(state):
    custom = state.get('chart')
    if not custom: return CHART
    chart = copy.deepcopy(CHART)
    for key, rows in custom['params'].items():
        chart['params'][key]['swatches'] = [{'value': s['value'], 'lab': lab} for s, lab in zip(chart['params'][key]['swatches'], rows)]
    chart.update(custom=True, calibratedAt=custom['calibratedAt'], basis='measured from the bottle\'s chart in the light box')
    if custom.get('marks'): chart['marks'] = custom['marks']
    if custom.get('hasPhoto'): chart['hasPhoto'] = True
    return chart


def stock_unit(settings, product):
    return 'g' if product == 'acid' and settings['products']['acid'].get('kind') == 'dry' else chemistry.STOCK_UNITS[product]


# --- Views -----------------------------------------------------------------------
def water_temp(owner):
    """The live water temperature comes from the owner's Home Assistant, so it describes the owner's pool only."""
    return ha_pool.LATEST.get('waterTempC') if owner else None


def current_analysis(state, owner=True):
    test = ha_pool.latest_test(state)
    if not test: return None
    analysis = chemistry.analyse(test['readings'], state['settings'], water_temp(owner))
    # The newest test on record can still be older than the last thing added to the pool (a shop's report
    # entered afterwards, for one). Then its readings stand, but no amounts are given from it.
    return chemistry.hold_if_dosed_since(analysis, test, state['doses'], state['settings'])


def view(state, person=None):
    """Everything the phone needs in one response. Without a person, the owner's view."""
    person = person or owner_person()
    owner = person['owner']
    live = sorted((t for t in state['tests'] if not t.get('voidedAt')), key=lambda t: t['at'], reverse=True)
    doses = sorted((d for d in state['doses'] if not d.get('voidedAt')), key=lambda d: d['at'], reverse=True)
    analysis = current_analysis(state, owner)
    current = None
    if analysis:
        test = live[0]
        done = {d.get('stepKey'): d['id'] for d in doses if d.get('testId') == test['id']}
        for step in analysis['steps']: step['doneDoseId'] = done.get(step['key'])
        current = {**analysis, 'testId': test['id'], 'at': test['at']}
    statuses = {}
    for test in live[:200]:
        statuses[test['id']] = chemistry.test_statuses(test['readings'], state['settings'])
    return {
        # Two charts: the built-in one (daylight) for ordinary photos, and the one the light box is read against,
        # which is the built-in chart until the person measures the bottle's chart inside their box.
        'version': APP_VERSION, 'revision': state['revision'], 'settings': state['settings'], 'chart': CHART, 'boxChart': effective_chart(state),
        'tests': live[:200], 'statuses': statuses, 'doses': doses[:200], 'current': current,
        'insights': {'nudges': ha_pool.nudges(state, analysis), 'trends': chemistry.trends(state['tests'], state['doses'], state['settings']), 'nextTestDue': ha_pool.next_due(state, analysis)},
        'stockUnits': {p: stock_unit(state['settings'], p) for p in state['settings']['products']},
        'rules': {'fc': chemistry.FC_RULE, 'cyaPhWindow': chemistry.CYA_PH_WINDOW}, 'sources': chemistry.SOURCES,
        'surfaces': {key: surface['label'] for key, surface in chemistry.SURFACES.items()},
        'stripChecks': chemistry.strip_checks(state['tests'], state['doses'], CHART), 'checkWindow': chemistry.CHECK_WINDOW,
        'labFactors': [{'key': key, 'label': label, 'unit': unit, 'min': low, 'max': high} for key, label, unit, low, high, _ in LAB_FACTORS],
        'needsSetup': bool(state.get('needsSetup')),
        # Until a person has measured the bottle's chart in their own light box, strips read in it are compared
        # with a chart measured in daylight. So everyone is asked to calibrate, straight after describing their
        # pool, and reminded until they have, or until they say they have no light box.
        'needsCalibration': not state.get('chart') and not state.get('noLightBox'),
        'me': {key: person[key] for key in ('username', 'name', 'owner', 'session')},
        'people': people() if owner else None,
        # Home Assistant, its weather and the app's own address are the owner's business only.
        'weather': {k: v if owner else None for k, v in ha_pool.LATEST.items()},
        'runtime': {'ingress': INGRESS, 'ha': dict(ha_pool.STATUS), 'https': tls_manager.status(), 'address': ORIGIN if PASSWORD else None} if owner
                   else {'ingress': False, 'ha': {'enabled': False, 'connected': False, 'lastPublish': None}, 'https': {}, 'address': None},
    }


# --- Actions ---------------------------------------------------------------------
def decode_photo(data_url):
    if not isinstance(data_url, str) or not data_url.startswith('data:image/jpeg;base64,') or len(data_url) > 4500000: raise ValueError('The photo must be a JPEG under 3 MB.')
    raw = base64.b64decode(data_url.split(',', 1)[1], validate=True)
    if raw[:3] != b'\xff\xd8\xff': raise ValueError('The photo is not a valid JPEG.')
    return raw


def save_photo(ident, data_url, uid=OWNER):
    raw = decode_photo(data_url)
    folder = photo_dir(uid); folder.mkdir(exist_ok=True)
    target = folder / (ident + '.jpg')
    target.write_bytes(raw); target.chmod(0o600)
    prune_photos(uid)


def lab_photo(uid, ident):
    """The picture of a pool shop's printed report. Its name keeps it out of the three-month rule for strip photos."""
    return photo_dir(uid) / (ident + '.lab.jpeg')


def prune_photos(uid, moment=None):
    """Delete one person's strip photos that are too old, too many or too much, oldest first.

    Only the pictures go: the tests they belong to keep every reading, and are
    marked as no longer having a photo. The calibration picture is not a strip
    photo (it is a .jpeg, replaced by the next calibration) and is left alone.
    A picture of a pool shop's printout is the only record of what the shop
    advised, so it does not age out: only the newest LAB_PHOTOS are kept.
    """
    folder = photo_dir(uid)
    if not folder.is_dir(): return 0
    cutoff = (moment or time.time()) - PHOTO_DAYS * 86400
    kept, room, gone = 0, PHOTO_BYTES, 0
    for size, modified, file in sorted(((f.stat().st_size, f.stat().st_mtime, f) for f in folder.glob('*.jpg')), key=lambda row: row[1], reverse=True):
        kept += 1; room -= size
        if modified < cutoff or kept > KEEP_PHOTOS or room < 0:
            file.unlink(missing_ok=True); gone += 1
    for file in sorted(folder.glob('*.lab.jpeg'), key=lambda f: f.stat().st_mtime, reverse=True)[LAB_PHOTOS:]:
        file.unlink(missing_ok=True); gone += 1
    have = lambda: {f.name.split('.', 1)[0] for f in folder.iterdir() if f.name.endswith(('.jpg', '.lab.jpeg'))}
    if any(t.get('hasPhoto') and t['id'] not in have() for t in get_state(uid)['tests']):
        def op(state):
            # Looked up again inside the lock: a photo is always written before its test is recorded.
            present = have()
            for test in state['tests']:
                if test.get('hasPhoto') and test['id'] not in present: test['hasPhoto'] = False
            return state
        save_change(op, uid)
    return gone


def housekeeping(interval=6 * 3600):
    """Runs at start-up and a few times a day, so photos age out even for someone who never tests again."""
    while True:
        try:
            with connect() as db: ids = [row[0] for row in db.execute('SELECT id FROM state')]
            gone = sum(prune_photos(uid) for uid in ids)
            if gone: print(f'Pool housekeeping: {gone} old strip photo{"" if gone == 1 else "s"} deleted.', flush=True)
        except Exception as error:
            print(f'Pool housekeeping failed: {type(error).__name__}', flush=True)
        time.sleep(interval)


def calibration_photo(uid):
    """The picture a light-box calibration was measured from. Its name keeps it out of the pruning of old test photos."""
    return photo_dir(uid) / 'calibration.jpeg'


def set_chart(body, uid):
    if body.get('reset') is True:
        custom, raw = None, None
    else:
        custom = clean_chart(body)
        raw = decode_photo(body['photo']) if body.get('photo') is not None else None
    target = calibration_photo(uid)
    with LOCK:
        if raw:
            target.parent.mkdir(exist_ok=True); target.write_bytes(raw); target.chmod(0o600)
            custom['hasPhoto'] = True
        else:
            target.unlink(missing_ok=True)
        def op(state):
            if custom: state['chart'] = custom; state.pop('noLightBox', None)   # calibrating says there is a box after all
            else: state.pop('chart', None)
            return state
        return save_change(op, uid)


def set_light_box(body, uid):
    """Whether this person has a light box at all. Saying no ends the reminders to calibrate one."""
    if not isinstance(body.get('have'), bool): raise ValueError('Say whether you have a light box.')
    def op(state):
        if body['have']: state.pop('noLightBox', None)
        else: state['noLightBox'] = True
        return state
    return save_change(op, uid)


def require_setup(state):
    if state.get('needsSetup'): raise ValueError('Tell the app about your pool first.')


def set_up(body, uid):
    """A new person's first step: their own pool's volume and surface, in place of the owner's defaults."""
    def op(state):
        state['settings'] = validate_settings(merge(state['settings'], {'volumeLitres': body.get('volumeLitres'), 'surface': body.get('surface')}, chemistry.DEFAULT_SETTINGS))
        state.pop('needsSetup', None)
        return state
    return save_change(op, uid)


def add_test(body, uid=OWNER):
    require_setup(get_state(uid))
    ident = body.get('id') or secrets.token_hex(12)
    if not isinstance(ident, str) or not re.fullmatch(r'[a-f0-9]{16,40}', ident): raise ValueError('Invalid test id.')
    record = {'id': ident, 'at': moment(body['at']) if body.get('at') else now(), 'createdAt': now(),
              'source': body['source'] if body.get('source') in ('photo', 'box') else 'manual', 'readings': clean_readings(body.get('readings')), 'hasPhoto': False}
    note = body.get('note')
    if note is not None:
        if not isinstance(note, str) or len(note) > 500: raise ValueError('Notes are limited to 500 characters.')
        if note.strip(): record['note'] = note.strip()
    marks = body.get('marks')
    if isinstance(marks, dict) and all(isinstance(marks.get(k), dict) and all(isinstance(marks[k].get(a), (int, float)) and 0 <= marks[k][a] <= 1 for a in ('x', 'y')) for k in ('first', 'last')):
        record['marks'] = {k: {'x': round(marks[k]['x'], 4), 'y': round(marks[k]['y'], 4)} for k in ('first', 'last')}
    if body.get('photo'):
        save_photo(ident, body['photo'], uid); record['hasPhoto'] = True
    def op(state):
        if any(t['id'] == ident for t in state['tests']): return state  # a retried save
        if len(state['tests']) >= MAX_RECORDS: raise ValueError('The history is full. Download a backup, then ask the owner to make room.')
        state['tests'].append(record)
        strips = state['settings']['strips']
        if body.get('usedStrip', True) and strips.get('remaining'): strips['remaining'] -= 1
        return state
    return save_change(op, uid)


# --- Pool shop tests -------------------------------------------------------------
class TextOnly(HTMLParser):
    """Keeps the words of a piece of HTML and nothing else: no tags, no attributes, no scripts, no styles."""
    BLOCKS = {'p', 'br', 'div', 'li', 'ul', 'ol', 'tr', 'td', 'th', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}

    def __init__(self):
        super().__init__(convert_charrefs=True); self.parts, self.hidden = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'): self.hidden += 1
        if tag in self.BLOCKS: self.parts.append(' ')

    def handle_endtag(self, tag):
        if tag in ('script', 'style') and self.hidden: self.hidden -= 1
        if tag in self.BLOCKS: self.parts.append(' ')

    def handle_data(self, data):
        if not self.hidden: self.parts.append(data)


def plain_text(value, limit, markup=False):
    """Text from outside, made safe to keep: plain words on one line, no markup, no control characters, cut to length."""
    if not isinstance(value, str): return ''
    value = value[:20000]
    if markup:
        parser = TextOnly(); parser.feed(value); parser.close()
        value = ''.join(parser.parts)
    return ' '.join(''.join(ch if ch.isprintable() or ch.isspace() else ' ' for ch in value).split())[:limit].strip()


def lab_values(raw):
    """A shop's readings, each checked against what that reading could possibly be. The four the app doses from are required."""
    if not isinstance(raw, dict): raise ValueError('Invalid readings.')
    out = {key: round(number(raw[key], label, low, high), 2) for key, label, unit, low, high, _ in LAB_FACTORS if raw.get(key) is not None}
    missing = [chemistry.LABELS[key].lower() if key != 'ph' else 'pH' for key in chemistry.PARAMS if key not in out]
    if missing: raise ValueError('A pool shop test needs free chlorine, pH, total alkalinity and stabiliser. Missing: ' + ', '.join(missing) + '.')
    return out


def lab_allowance(uid):
    """Counts one fetch against this person, and refuses once they have used up their share."""
    clock = time.time()
    with LOGIN_LOCK:
        recent = [t for t in LAB_FETCHES.get(uid, []) if t > clock - LAB_WINDOW]
        if len(recent) >= LAB_LIMIT: raise ValueError('Too many reports fetched in a short time. Try again in ten minutes.')
        LAB_FETCHES[uid] = recent + [clock]


def fetch_report(token):
    """The figures behind a shared WaterLink report, as that service sends them.

    The host is fixed. The only part of the address that comes from the person
    is the report's code, already checked to be nothing but hexadecimal digits.
    The certificate is verified and a redirect is not followed, so this cannot
    be pointed at any other machine. What comes back is untrusted: read_report
    picks out the few things the app keeps and checks each one.
    """
    if not LAB_SLOTS.acquire(timeout=10): raise ValueError('Busy. Try again in a moment.')
    try:
        conn = http.client.HTTPSConnection(LAB_API, timeout=10)
        try:
            conn.request('GET', '/treatment-reports/' + token, headers={'Accept': 'application/json'})
            response = conn.getresponse()
            if response.status == 404: raise ValueError('The pool shop has no report at that link. Check that the whole link was copied.')
            if response.status != 200: raise OSError('unexpected answer')
            raw = response.read(LAB_REPORT_BYTES + 1)
        finally: conn.close()
        if len(raw) > LAB_REPORT_BYTES: raise OSError('too large')
        return json.loads(raw)
    except (OSError, http.client.HTTPException, json.JSONDecodeError, UnicodeDecodeError, RecursionError) as error:
        print(f'Pool: a pool shop report could not be fetched ({type(error).__name__}).', flush=True)
        raise ValueError('The pool shop\'s report could not be fetched just now. Try again later, or enter the printout by hand.')
    finally: LAB_SLOTS.release()


def read_report(data):
    """When the water was tested, what was measured and what the shop said, out of a WaterLink report.

    The report also carries the customer's name, address, phone number and
    email address. None of that is read, so none of it is kept.
    """
    if not isinstance(data, dict): raise ValueError('That report could not be read.')
    by_number = {row[5]: row for row in LAB_FACTORS}
    listed = lambda name: [item for item in (data.get(name) if isinstance(data.get(name), list) else [])[:80] if isinstance(item, dict)]
    found, meter = {}, ''
    for item in listed('measurements'):
        factor = by_number.get(item.get('testFactorId')) if type(item.get('testFactorId')) is int else None
        unit = item.get('unitOfMeasure')
        # A figure in a unit the app does not expect is left out, never converted by guesswork.
        if not factor or (unit.lower() if isinstance(unit, str) else '') != factor[2] or factor[0] in found: continue
        found[factor[0]] = item.get('value')
        meter = meter or plain_text(item.get('inputMeterId'), 40)
    try: at = moment(data.get('waterTestDate'))
    except ValueError: raise ValueError('That report has no usable test date.')
    conditions = [LAB_CONDITIONS[item['waterConditionId']] for item in listed('conditions')
                  if item.get('present') is True and type(item.get('waterConditionId')) is int and item['waterConditionId'] in LAB_CONDITIONS]
    advice = [text for text in (plain_text(item.get('actionText'), 1500, markup=True) for item in listed('recommendations')[:12]) if text]
    name = lambda part: plain_text(data[part].get('name'), 60) if isinstance(data.get(part), dict) else ''
    return at, lab_values(found), {'origin': 'waterlink', 'shop': name('ownerLocation'), 'company': name('ownerPreferences'), 'profile': name('treatmentProfile'),
                                   'meter': meter, 'conditions': list(dict.fromkeys(conditions)), 'advice': advice}


def add_lab_test(body, uid=OWNER):
    """A test done at a pool shop, kept beside the strip tests: fetched from the link the shop sends, or typed in from its printout."""
    state = get_state(uid); require_setup(state)
    photo, note = None, None
    if body.get('url') is not None:
        link = LAB_LINK.fullmatch(body['url'].strip()) if isinstance(body['url'], str) and len(body['url']) <= 300 else None
        if not link: raise ValueError('That is not a pool shop report link. It starts with ' + LAB_PAGE + ' and ends in a long code.')
        token = link.group(1).lower()
        # The same report always gets the same id, so it cannot be imported twice.
        ident = hashlib.sha256(('waterlink:' + token).encode()).hexdigest()[:24]
        if any(t['id'] == ident and not t.get('voidedAt') for t in state['tests']): raise ValueError('That report is already in your history.')
        lab_allowance(uid)
        at, values, lab = read_report(fetch_report(token))
        lab['url'] = LAB_PAGE + token
    else:
        manual = body.get('manual')
        if not isinstance(manual, dict): raise ValueError('Give the report\'s link, or its readings.')
        ident = body.get('id') or secrets.token_hex(12)
        if not isinstance(ident, str) or not re.fullmatch(r'[a-f0-9]{16,40}', ident): raise ValueError('Invalid test id.')
        at, values = moment(manual.get('at')), lab_values(manual.get('values'))
        lab = {'origin': 'manual', 'shop': plain_text(manual.get('shop'), 60)}
        note = manual.get('note')
        if note is not None and (not isinstance(note, str) or len(note) > 500): raise ValueError('Notes are limited to 500 characters.')
        if body.get('photo') is not None: photo = decode_photo(body['photo'])
    record = {'id': ident, 'at': at, 'createdAt': now(), 'source': 'lab', 'hasPhoto': bool(photo),
              # A meter gives a figure, not a colour step: no range around it, and never "at the end of the chart".
              'readings': {key: {'value': values[key], 'low': values[key], 'high': values[key], 'confidence': 'lab', 'atMin': False, 'atMax': False} for key in chemistry.PARAMS},
              'lab': {**{key: value for key, value in lab.items() if value}, 'extras': {key: value for key, value in values.items() if key not in chemistry.PARAMS}}}
    if note and plain_text(note, 500): record['note'] = plain_text(note, 500)
    outcome = {'id': ident}

    def op(state):
        old = next((t for t in state['tests'] if t['id'] == ident), None)
        if old and not old.get('voidedAt'): return state   # a retried save
        if old: state['tests'].remove(old)                 # deleted earlier and now wanted back
        elif len(state['tests']) >= MAX_RECORDS: raise ValueError('The history is full. Download a backup, then ask the owner to make room.')
        state['tests'].append(record)
        # Calcium hardness is the one figure the water balance needs that a strip cannot give, so a shop test
        # fills it in. It never undoes anything newer: not the figure from a later shop test, and not one typed
        # into Settings by hand (which carries no date, so it is taken to be the latest word). Then the setting
        # is left alone and the person is told, and can adopt the report's figure with one tap.
        calcium, current = values.get('ch'), state['settings'].get('calciumHardness')
        others = [t for t in state['tests'] if t is not record and t.get('source') == 'lab' and not t.get('voidedAt') and 'ch' in t.get('lab', {}).get('extras', {})]
        by_hand = current is not None and not any(round(t['lab']['extras']['ch']) == current for t in others)
        newer = any(t['at'] > at for t in others)
        if calcium is not None and 10 <= calcium <= 2000 and current != round(calcium):
            if by_hand or newer: outcome['calciumKept'] = current
            else: state['settings']['calciumHardness'] = outcome['calcium'] = round(calcium)
        return state

    target = lab_photo(uid, ident)
    if photo:
        target.parent.mkdir(exist_ok=True); target.write_bytes(photo); target.chmod(0o600)
    try: result = save_change(op, uid)
    except Exception:
        if photo: target.unlink(missing_ok=True)
        raise
    if photo: prune_photos(uid)
    return result, outcome


def add_dose(body, uid=OWNER):
    product = body.get('product')
    if product not in chemistry.DEFAULT_SETTINGS['products']: raise ValueError('Unknown product.')
    amount = number(body.get('amount'), 'Amount', 0.1, 1000000)
    record = {'id': secrets.token_hex(12), 'at': moment(body['at']) if body.get('at') else now(), 'product': product, 'amount': amount}
    for key in ('testId', 'stepKey'):
        if isinstance(body.get(key), str) and len(body[key]) <= 40: record[key] = body[key]
    def op(state):
        if record.get('testId') and any(d.get('testId') == record['testId'] and d.get('stepKey') == record.get('stepKey') and not d.get('voidedAt') for d in state['doses']): return state
        if len(state['doses']) >= MAX_RECORDS: raise ValueError('The history is full. Download a backup, then ask the owner to make room.')
        record['unit'] = stock_unit(state['settings'], product)
        item = state['settings']['products'][product]
        if item.get('stock') is not None: item['stock'] = max(0, round(item['stock'] - amount, 1))
        state['doses'].append(record)
        return state
    return save_change(op, uid)


def void(group, ident, uid=OWNER):
    def op(state):
        record = next((r for r in state[group] if r['id'] == ident and not r.get('voidedAt')), None)
        if not record: raise ValueError('Record not found.')
        record['voidedAt'] = now()
        if group == 'tests' and record.get('hasPhoto'):
            # A deleted test's picture goes with it.
            (photo_dir(uid) / (record['id'] + '.jpg')).unlink(missing_ok=True); lab_photo(uid, record['id']).unlink(missing_ok=True); record['hasPhoto'] = False
        if group == 'doses':
            item = state['settings']['products'][record['product']]
            if item.get('stock') is not None: item['stock'] = round(item['stock'] + record['amount'], 1)
        return state
    return save_change(op, uid)


class BoundedServer(ThreadingHTTPServer):
    """Serves a limited number of requests at once. Beyond that a connection is closed unanswered, so a
    flood of slow or idle connections costs the machine a fixed, small amount and no more."""
    daemon_threads = True

    def __init__(self, *args, **kwargs):
        self.slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False): return self.shutdown_request(request)
        super().process_request(request, client_address)

    def process_request_thread(self, request, client_address):
        try: super().process_request_thread(request, client_address)
        finally: self.slots.release()

    def handle_error(self, request, client_address):
        # One line, no traceback: malformed requests arrive all day on a public port, and their content stays out of the log.
        print(f'Pool: a request was dropped ({type(sys.exc_info()[1]).__name__}).', flush=True)


class HeaderBudget:
    """The request's stream, with a ceiling on how much the request line and headers may add up to."""
    def __init__(self, stream, limit): self.stream, self.left = stream, limit
    def readline(self, *args):
        line = self.stream.readline(*args)
        self.left -= len(line)
        if self.left < 0: raise http.client.LineTooLong('header')
        return line
    def __getattr__(self, name): return getattr(self.stream, name)


class PublicServer(BoundedServer, tls_manager.TLSHTTPServer): pass


class Handler(SimpleHTTPRequestHandler):
    server_version = 'PoolStripReader'
    sys_version = ''              # do not advertise the Python version
    ingress_mode = INGRESS
    timeout = REQUEST_TIMEOUT

    def setup(self):
        super().setup()
        self.rfile = HeaderBudget(self.rfile, MAX_HEADERS)
        # The timeout above only limits silence. This limits the whole request, so one fed a byte at a time is cut off too.
        self.deadline = threading.Timer(REQUEST_DEADLINE, self.cut_off); self.deadline.daemon = True; self.deadline.start()

    def cut_off(self):
        try: self.connection.shutdown(socket.SHUT_RDWR)
        except OSError: pass

    def finish(self):
        self.deadline.cancel()
        try: super().finish()
        except OSError: pass   # the other end, or the deadline, already closed it

    def do_HEAD(self): self.send_error(405)

    def __init__(self, *args, **kwargs): super().__init__(*args, directory=str(ROOT / 'public'), **kwargs)
    def log_message(self, *args): pass

    def send_header(self, key, value):
        if key.lower() == 'cache-control': self.cache_set = True
        super().send_header(key, value)

    def end_headers(self):
        # A file asked for under this version's own address can be kept for good: the next version has a
        # different address. Anything else is revalidated on every load.
        if not getattr(self, 'cache_set', False): self.send_header('Cache-Control', 'public, max-age=31536000, immutable' if getattr(self, 'versioned', False) else 'no-cache')
        self.cache_set = self.versioned = False
        self.send_header('Permissions-Policy', 'camera=(self)')
        self.send_header('X-Content-Type-Options', 'nosniff'); self.send_header('Referrer-Policy', 'same-origin'); self.send_header('X-Frame-Options', 'SAMEORIGIN')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; object-src 'none'; frame-ancestors 'self'; base-uri 'self'; form-action 'self'")
        self.send_header('Cross-Origin-Resource-Policy', 'same-origin')
        if not self.ingress_mode and ORIGIN.startswith('https://'): self.send_header('Strict-Transport-Security', 'max-age=31536000')
        super().end_headers()

    def json(self, data, status=200, extra=None):
        raw = json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
        self.send_response(status); self.send_header('Content-Type', 'application/json'); self.send_header('Cache-Control', 'no-store'); self.send_header('Content-Length', str(len(raw)))
        for k, v in (extra or {}).items(): self.send_header(k, v)
        self.end_headers(); self.wfile.write(raw)

    def cookie_name(self):
        # The __Host- prefix makes the browser refuse this cookie from anywhere but this exact HTTPS host,
        # so another site on the same domain cannot plant a session here. It needs HTTPS, so not in local preview.
        return '__Host-pool_session' if ORIGIN.startswith('https://') else 'pool_session'

    def who(self):
        """The person making this request, or None. Home Assistant's sidebar is always the owner."""
        if self.ingress_mode: return owner_person() if self.client_address[0] == INGRESS_PEER else None
        # With no password configured the app is open, which is only ever right on this machine itself.
        if not PASSWORD: return owner_person() if HOST == '127.0.0.1' else None
        try:
            jar = cookies.SimpleCookie(self.headers.get('Cookie', ''))
            name = self.cookie_name()
            legacy = name not in jar
            uid, issued, _ = value = jar['pool_session' if legacy else name].value.split('.')
            uid, issued = int(uid), int(issued)
            # Under its old name the cookie is only believed if it is a pre-0.7.0 session. None can be
            # made any more, so this door shuts by itself within 30 days.
            if legacy and name != 'pool_session' and not legacy_session(issued): return None
            since, moment = session_issued(issued), time.time()
            if not 0 < uid < 2 ** 31 or moment - since > COOKIE_AGE: return None
            if uid == OWNER: person = owner_person(True)
            else:
                row = find_person(uid)
                if not row or row['disabled']: return None
                person = {'id': uid, 'username': row['username'], 'name': row['name'], 'owner': False, 'session': True, 'secret': row['secret']}
            mark = session_mark(uid, person.get('secret'))
            if not hmac.compare_digest('.'.join(value).encode(), session_value(uid, mark, str(issued)).encode()): return None
            if since < sessions_from(uid): return None   # they have signed out since it was issued
            # Opening the app keeps the session going: the same one while it is fresh, a new one once a day.
            fresh = not legacy_session(issued) and moment - since < SESSION_REFRESH
            person['cookie'] = '.'.join(value) if fresh else session_value(uid, mark)
            return person
        except Exception: return None   # whatever was in the Cookie header, a session it is not

    def visitor(self):
        """The address to count password attempts against. Through the Cloudflare Tunnel every request arrives
        from the tunnel's own address, and Cloudflare names the real visitor in CF-Connecting-IP (it replaces
        anything the visitor sent under that name). The header is believed only when the connection itself
        comes from a private address, which is where the tunnel is. An IPv6 visitor is counted by their
        /64, since one connection has that many addresses to try from."""
        address = self.client_address[0]
        try:
            claimed = self.headers.get('CF-Connecting-IP', '').strip()
            if ipaddress.ip_address(address).is_private and re.fullmatch(r'[0-9A-Fa-f:.]{2,45}', claimed): address = str(ipaddress.ip_address(claimed))
            if ':' in address: address = str(ipaddress.ip_network(address + '/64', strict=False).network_address)
        except ValueError: pass
        return address

    def session_cookie(self, value, age=COOKIE_AGE):
        secure = '; Secure' if ORIGIN.startswith('https://') else ''
        return {'Set-Cookie': f'{self.cookie_name()}={value}; HttpOnly; SameSite=Strict; Path=/; Max-Age={age}{secure}'}

    def allowed_host(self):
        if self.ingress_mode: return self.client_address[0] == INGRESS_PEER
        allowed = {urlsplit(ORIGIN).netloc}
        if HOST == '127.0.0.1': allowed.add(f'localhost:{PORT}')
        return self.headers.get('Host') in allowed

    def do_GET(self):
        parts = urlsplit(self.path); path = parts.path
        if not self.allowed_host(): return self.json({'error': 'Host not configured. Set the app address to the public HTTPS address.'}, 403)
        if path.startswith('/api/'):
            person = self.who()
            if path == '/api/session': return self.json({'authenticated': bool(person), 'passwordRequired': bool(PASSWORD) and not self.ingress_mode})
            if not person: return self.json({'error': 'Please sign in.'}, 401)
            uid = person['id']
            # Opening the app renews the cookie, so a phone in regular use is never asked to sign in again.
            if path == '/api/state': return self.json(view(get_state(uid), person), extra=self.session_cookie(person['cookie']) if person.get('cookie') else None)
            if path == '/api/backup':
                return self.json({'format': 'pool-strip-reader', 'version': 1, 'exportedAt': now(), 'data': get_state(uid)}, extra={'Content-Disposition': 'attachment; filename="pool-strip-reader-backup.json"'})
            if path == '/api/photo':
                ident = parse_qs(parts.query).get('id', [''])[0]
                calibration = ident == 'calibration'
                if not (calibration or re.fullmatch(r'[a-f0-9]{16,40}', ident)): return self.json({'error': 'Photo not found.'}, 404)
                file = calibration_photo(uid) if calibration else photo_dir(uid) / (ident + '.jpg')
                if not calibration and not file.is_file(): file = lab_photo(uid, ident)   # a pool shop's printout
                if not file.is_file(): return self.json({'error': 'Photo not found.'}, 404)
                raw = file.read_bytes()
                # A test's photo never changes; the calibration picture is replaced by the next calibration.
                self.send_response(200); self.send_header('Content-Type', 'image/jpeg'); self.send_header('Cache-Control', 'no-store' if calibration else 'private, max-age=86400'); self.send_header('Content-Length', str(len(raw)))
                self.end_headers(); self.wfile.write(raw); return
            return self.json({'error': 'Not found.'}, 404)
        if path == '/': path = '/index.html'
        if path == '/index.html':
            base = './'
            if self.ingress_mode:
                prefix = self.headers.get('X-Ingress-Path', '').rstrip('/')
                if not re.fullmatch(r'/api/hassio_ingress/[A-Za-z0-9_-]+', prefix): return self.json({'error': 'Home Assistant ingress path is missing or invalid.'}, 400)
                base = prefix + '/'
            # The page names its scripts and styles under v/<this version>/. Their imports of each other are
            # relative, so they carry the version too, and a copy cached from an older version (by a browser,
            # or by a proxy that sets its own cache times, as Cloudflare does) can never be mixed with this one.
            raw = (ROOT / 'public' / 'index.html').read_text(encoding='utf-8').replace('<base href="./">', f'<base href="{html.escape(base, quote=True)}">').replace('__VERSION__', APP_VERSION).encode()
            self.send_response(200); self.send_header('Content-Type', 'text/html; charset=utf-8'); self.send_header('Cache-Control', 'no-cache'); self.send_header('Content-Length', str(len(raw)))
            self.end_headers(); self.wfile.write(raw); return
        versioned = re.fullmatch(r'/v/([^/]+)/(.+)', path)
        if versioned:
            # Another version's address is not served at all, so it cannot be answered with this version's file.
            if versioned.group(1) != APP_VERSION: return self.json({'error': 'Not found.'}, 404)
            path = '/' + versioned.group(2)
        try: file = (ROOT / 'public' / path.lstrip('/')).resolve()
        except ValueError: file = None   # a path no file could have
        if not file or not file.is_relative_to(ROOT / 'public') or not file.is_file(): return self.json({'error': 'Not found.'}, 404)
        self.path = path; self.versioned = bool(versioned)
        return super().do_GET()

    def do_POST(self):
        if not self.allowed_host(): return self.json({'error': 'Host not configured.'}, 403)
        if self.ingress_mode:
            # HA has already authenticated this request. Trust forwarded headers
            # only after checking the actual socket peer.
            origin = self.headers.get('Origin', '')
            hosts = {self.headers.get('Host', ''), self.headers.get('X-Forwarded-Host', '').split(',')[0].strip()}
            origin_ok = urlsplit(origin).scheme in ('http', 'https') and bool(urlsplit(origin).netloc) and urlsplit(origin).netloc in hosts
        else:
            allowed = {ORIGIN} | ({f'http://localhost:{PORT}'} if HOST == '127.0.0.1' else set())
            origin_ok = self.headers.get('Origin') in allowed
        if not origin_ok: return self.json({'error': 'Request origin does not match this app.'}, 403)
        try:
            path = urlsplit(self.path).path
            before_sign_in = path in ('/api/login', '/api/logout')
            # Who is asking is settled from the headers, before a byte of the body is read:
            # only a signed-in person can make the server take in and parse anything sizeable.
            person = None if before_sign_in else self.who()
            if not before_sign_in and not person: return self.json({'error': 'Please sign in again.'}, 401)
            size = int(self.headers.get('Content-Length', '0'))
            uploads = path in ('/api/tests', '/api/chart', '/api/lab')
            if not 0 < size <= (SIGN_IN_BODY if before_sign_in else MAX_BODY if uploads else SMALL_BODY): raise ValueError('The request is empty or too large.')
            if 'application/json' not in self.headers.get('Content-Type', ''): raise ValueError('JSON is required.')
            if uploads and size > SMALL_BODY:
                if not UPLOAD_SLOTS.acquire(timeout=15): return self.json({'error': 'Busy. Try again in a moment.'}, 429)
                try: body = json.loads(self.rfile.read(size))
                finally: UPLOAD_SLOTS.release()
            else: body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict): raise ValueError('Invalid request.')
            if path == '/api/login':
                username, password = str(body.get('username', '')).strip().lower()[:64], str(body.get('password', ''))[:1024]
                visitor = self.visitor()
                keys = ('from:' + visitor, 'user:' + username, 'all:')
                ticket = password_attempt(keys)
                if not ticket: return self.json({'error': 'Too many attempts. Try again in five minutes.'}, 429)
                if not LOGIN_SLOTS.acquire(timeout=5): return self.json({'error': 'Busy. Try again in a moment.'}, 429)
                try: person = sign_in(username, password)
                finally: LOGIN_SLOTS.release()
                if not person:
                    print(f'Pool sign-in failed from {visitor}.', flush=True)
                    return self.json({'error': 'Username or password not recognised.'}, 401)
                password_accepted(keys, ticket)
                return self.json({'ok': True}, extra=self.session_cookie(session_value(person['id'], session_mark(person['id'], person.get('secret')))))
            if path == '/api/logout':
                # Signing out ends this person's sessions on every device, so a copied cookie stops working too.
                person = self.who()
                if person and person['session']: end_sessions(person['id'])
                return self.json({'ok': True}, extra=self.session_cookie('', 0))
            uid = person['id']
            if path == '/api/password':
                # Guessing the current password from a signed-in session is limited exactly like guessing it at the door.
                keys = ('from:' + self.visitor(), 'user:' + person['username'], 'all:')
                ticket = password_attempt(keys)
                if not ticket: return self.json({'error': 'Too many attempts. Try again in five minutes.'}, 429)
                if not LOGIN_SLOTS.acquire(timeout=5): return self.json({'error': 'Busy. Try again in a moment.'}, 429)
                try: secret = change_own_password(person, body)
                finally: LOGIN_SLOTS.release()
                password_accepted(keys, ticket)
                return self.json({'ok': True}, extra=self.session_cookie(session_value(uid, secret)))
            if path == '/api/people':
                if not person['owner']: return self.json({'error': 'Only the owner can manage people.'}, 403)
                if body.get('action') == 'add': add_person(body)
                else: change_person(body)
                return self.json(view(get_state(uid), person))
            if path == '/api/plan':
                state = get_state(uid); require_setup(state)
                return self.json(chemistry.analyse(clean_readings(body.get('readings')), state['settings'], water_temp(person['owner'])))
            if path == '/api/lab':
                result, outcome = add_lab_test(body, uid)
                if person['owner']: ha_pool.WAKE.set()
                return self.json({**view(result, person), 'imported': outcome})
            if path == '/api/setup': result = set_up(body, uid)
            elif path == '/api/tests': result = add_test(body, uid)
            elif path == '/api/doses': result = add_dose(body, uid)
            elif path == '/api/void':
                if body.get('group') not in ('tests', 'doses') or not isinstance(body.get('id'), str): raise ValueError('This record cannot be removed.')
                result = void(body['group'], body['id'], uid)
            elif path == '/api/chart': result = set_chart(body, uid)
            elif path == '/api/lightbox': result = set_light_box(body, uid)
            elif path == '/api/settings':
                def op(state):
                    state['settings'] = validate_settings(merge(state['settings'], body, chemistry.DEFAULT_SETTINGS))
                    return state
                result = save_change(op, uid)
            else: return self.json({'error': 'Not found.'}, 404)
            # Home Assistant only mirrors the owner's pool, so only the owner's changes need publishing.
            if person['owner']: ha_pool.WAKE.set()
            return self.json(view(result, person))
        except (ValueError, TypeError, KeyError) as error:
            return self.json({'error': str(error) or 'Invalid request.'}, 400)
        except Exception as error:
            print(f'Request failed: {type(error).__name__}', flush=True)
            return self.json({'error': 'Could not save. Nothing was changed; try again.'}, 500)


def main(options=None):
    if not INGRESS and HOST != '127.0.0.1' and (len(PASSWORD) < 12 or not os.getenv('APP_ORIGIN')):
        raise SystemExit('Network hosting requires APP_PASSWORD (at least 12 characters) and APP_ORIGIN.')
    if os.getenv('POOL_DIRECT') == '1' and len(PASSWORD) < 12:
        raise SystemExit('The standalone address needs a password of at least 12 characters.')
    if PASSWORD and not USERNAME_RULE.fullmatch(USERNAME):
        raise SystemExit('The owner username must be 2 to 32 letters, digits, dots, dashes or underscores, with no spaces.')
    init()
    ha_pool.start(SimpleNamespace(get_state=get_state, save_change=save_change, current_analysis=current_analysis), options)
    threading.Thread(target=housekeeping, name='pool-housekeeping', daemon=True).start()
    print(f'Pool Strip Reader {APP_VERSION}: http://{HOST}:{PORT}', flush=True)
    if INGRESS and os.getenv('POOL_DIRECT') == '1':
        class StandaloneHandler(Handler): ingress_mode = False
        direct = None
        def activate(context):
            nonlocal direct
            if direct is None:
                direct = PublicServer((HOST, DIRECT_PORT), StandaloneHandler, context)
                threading.Thread(target=direct.serve_forever, daemon=True).start()
                print(f'Standalone HTTPS listener ready on {DIRECT_PORT}.', flush=True)
            else: direct.context = context
        try:
            tls_manager.CertificateManager(options or {'standalone_url': ORIGIN}, DATA, activate).start()
        except ValueError as error:
            tls_manager.STATUS.update(enabled=True, state='configuration_required')
            print('Pool HTTPS: ' + str(error), flush=True)
    BoundedServer((HOST, PORT), Handler).serve_forever()


if __name__ == '__main__':
    main()
