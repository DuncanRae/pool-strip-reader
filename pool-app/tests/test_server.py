"""End-to-end checks against a real HTTP server on a temporary database.

Home Assistant is replaced by a tiny stand-in that records what it is sent.
Nothing here touches a live installation.
"""
import base64, copy, datetime as dt, http.client, json, os, pathlib, socket, sqlite3, sys, tempfile, threading, unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0)); return s.getsockname()[1]


PORT = free_port()
TEMP = tempfile.TemporaryDirectory(prefix='pool-test-')
os.environ.update(POOL_DATA_DIR=TEMP.name, HOST='127.0.0.1', PORT=str(PORT))
for name in ('APP_PASSWORD', 'APP_USERNAME', 'APP_ORIGIN', 'POOL_INGRESS', 'POOL_HA_ENABLED'): os.environ.pop(name, None)
import chemistry, ha_pool, server
server.PBKDF2_ROUNDS = 1000   # real installations use the full count; the tests only need the mechanism

JPEG = 'data:image/jpeg;base64,' + base64.b64encode((ROOT / 'tests/fixtures/dry-strip-on-stone.jpg').read_bytes()[:200000]).decode()


def reading(fc=2, ta=100, ph=7.4, cya=40):
    return {'fc': {'value': fc, 'low': fc, 'high': fc, 'confidence': 'good', 'lab': [80, 5, 5]}, 'ta': {'value': ta}, 'ph': {'value': ph}, 'cya': {'value': cya}}


class Api(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        server.init()
        cls.httpd = ThreadingHTTPServer(('127.0.0.1', PORT), server.Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown(); cls.httpd.server_close()

    def setUp(self):
        def reset(state):
            state.update(tests=[], doses=[], weather={'days': {}}, reminders={'sent': {}, 'shown': []}, settings=copy.deepcopy(chemistry.DEFAULT_SETTINGS))
            return state
        server.save_change(reset)
        server.PASSWORD = ''; server.LOGIN_ATTEMPTS.clear()
        with server.connect() as db: db.execute('UPDATE users SET sessionsFrom=0')
        for person in server.people(): server.change_person({'action': 'remove', 'id': person['id']})

    def call(self, path, body=None, headers=None, raw=False):
        conn = http.client.HTTPConnection('127.0.0.1', PORT, timeout=10)
        send = {'Origin': f'http://127.0.0.1:{PORT}', **(headers or {})}
        if body is not None: send['Content-Type'] = 'application/json'
        conn.request('POST' if body is not None else 'GET', path, json.dumps(body) if body is not None else None, send)
        response = conn.getresponse(); data = response.read(); conn.close()
        return response.status, (data if raw else json.loads(data)), response

    def test_new_installation_is_empty_and_serves_the_app(self):
        status, state, _ = self.call('/api/state')
        self.assertEqual(status, 200)
        self.assertEqual((state['tests'], state['current'], state['settings']['volumeLitres']), ([], None, 120000))
        self.assertEqual(state['chart']['padOrder'], ['fc', 'ta', 'ph', 'cya'])
        self.assertEqual(state['rules']['fc']['ratio'], 20)
        self.assertIn('cdc.gov', state['sources'][0]['url'])
        status, page, response = self.call('/', raw=True)
        self.assertIn(b'<base href="./">', page)
        self.assertIn("script-src 'self'", response.getheader('Content-Security-Policy'))
        self.assertEqual(self.call('/../server.py', raw=True)[0], 404)
        status, _, response = self.call('/app.js', raw=True)
        self.assertEqual((status, response.getheader('Cache-Control')), (200, 'no-cache'))
        self.assertEqual(self.call('/api/state')[2].getheader('Cache-Control'), 'no-store')

    def test_scripts_are_served_under_the_version_so_a_stale_copy_cannot_be_mixed_in(self):
        version = server.APP_VERSION
        page = self.call('/', raw=True)[1].decode()
        for file in ('theme.js', 'style.css', 'app.js'): self.assertIn(f'"v/{version}/{file}"', page)
        self.assertNotIn('__VERSION__', page)
        # The scripts import each other by relative address, so those inherit the version from the importer.
        app = (ROOT / 'public/app.js').read_text()
        self.assertIn("from './strip.js'", app); self.assertIn("from './color.js'", (ROOT / 'public/strip.js').read_text())
        for file in ('app.js', 'strip.js', 'color.js', 'style.css', 'theme.js'):
            status, body, response = self.call(f'/v/{version}/{file}', raw=True)
            self.assertEqual((status, body, response.getheader('Cache-Control')), (200, (ROOT / 'public' / file).read_bytes(), 'public, max-age=31536000, immutable'), file)
        # Any other version's address is refused, never answered with this version's file.
        for path in ('/v/0.6.0/strip.js', '/v//strip.js', f'/v/{version}/', f'/v/{version}/../server.py', f'/v/{version}/missing.js', f'/v/{version}/api/state'):
            status, _, response = self.call(path, raw=True)
            self.assertEqual((status, response.getheader('Cache-Control')), (404, 'no-store'), path)
        # After a versioned file, the next plain one on the same server is still revalidated.
        self.assertEqual(self.call('/strip.js', raw=True)[2].getheader('Cache-Control'), 'no-cache')
        self.assertIn("searchParams.get('v')", (ROOT / 'public/sw.js').read_text())

    def test_saving_a_test_gives_a_plan_and_uses_a_strip(self):
        status, state, _ = self.call('/api/tests', {'id': 'a' * 24, 'source': 'photo', 'readings': reading(fc=0.5, ph=7.9), 'photo': JPEG, 'marks': {'first': {'x': 0.5, 'y': 0.2}, 'last': {'x': 0.5, 'y': 0.8}}})
        self.assertEqual(status, 200)
        self.assertEqual([s['key'] for s in state['current']['steps']], ['ph-down', 'fc-up'])
        self.assertEqual(state['settings']['strips']['remaining'], 24)
        self.assertTrue(state['tests'][0]['hasPhoto'])
        self.assertEqual(state['statuses']['a' * 24], {'fc': 'low', 'ta': 'ok', 'ph': 'high', 'cya': 'ok'})
        status, photo, response = self.call('/api/photo?id=' + 'a' * 24, raw=True)
        self.assertEqual((status, response.getheader('Content-Type'), photo[:3]), (200, 'image/jpeg', b'\xff\xd8\xff'))
        # A retried save (same id) must not double-count.
        status, again, _ = self.call('/api/tests', {'id': 'a' * 24, 'source': 'photo', 'readings': reading(fc=0.5, ph=7.9)})
        self.assertEqual((len(again['tests']), again['settings']['strips']['remaining']), (1, 24))

    def test_preview_plan_saves_nothing(self):
        status, plan, _ = self.call('/api/plan', {'readings': reading(fc=0)})
        self.assertEqual((status, plan['steps'][0]['key']), (200, 'fc-up'))
        self.assertEqual(self.call('/api/state')[1]['tests'], [])

    def test_bad_input_is_refused(self):
        for body in ({'readings': {'fc': {'value': 2}}}, {'readings': reading(ph=3)}, {'readings': reading(fc='2')}, {'readings': reading(), 'at': '2999-01-01T00:00:00+00:00'}, {'readings': reading(), 'photo': 'data:text/html;base64,AAAA'}, {'readings': reading(), 'id': '../../etc'}):
            status, data, _ = self.call('/api/tests', body)
            self.assertEqual(status, 400, body)
        self.assertEqual(self.call('/api/state')[1]['tests'], [])
        self.assertEqual(self.call('/api/tests', {'readings': reading()}, {'Origin': 'https://evil.example'})[0], 403)
        self.assertEqual(self.call('/api/state', headers={'Host': 'evil.example'})[0], 403)

    def test_doses_track_stock_and_can_be_undone(self):
        self.call('/api/settings', {'products': {'chlorine': {'stock': 2000}}})
        state = self.call('/api/tests', {'id': 'b' * 24, 'readings': reading(fc=0.5)})[1]
        step = state['current']['steps'][0]
        state = self.call('/api/doses', {'testId': 'b' * 24, 'stepKey': step['key'], 'product': step['product'], 'amount': step['amount']})[1]
        self.assertEqual(state['settings']['products']['chlorine']['stock'], 1300)   # 700 g: 0.5 up to 4 ppm at 40 ppm stabiliser
        self.assertTrue(state['current']['steps'][0]['doneDoseId'])
        again = self.call('/api/doses', {'testId': 'b' * 24, 'stepKey': step['key'], 'product': step['product'], 'amount': step['amount']})[1]
        self.assertEqual((len(again['doses']), again['settings']['products']['chlorine']['stock']), (1, 1300))
        state = self.call('/api/void', {'group': 'doses', 'id': state['doses'][0]['id']})[1]
        self.assertEqual((state['doses'], state['settings']['products']['chlorine']['stock']), ([], 2000))
        self.assertIsNone(state['current']['steps'][0]['doneDoseId'])

    def test_not_enough_stock_is_flagged(self):
        self.call('/api/settings', {'products': {'chlorine': {'stock': 100}}})
        state = self.call('/api/tests', {'readings': reading(fc=0)})[1]
        self.assertIn('stock-chlorine', [n['key'] for n in state['insights']['nudges']])

    def test_settings_are_validated_and_change_doses(self):
        status, state, _ = self.call('/api/settings', {'volumeLitres': 60000, 'calciumHardness': 250, 'products': {'acid': {'kind': 'dry', 'strength': 93}}})
        self.assertEqual((status, state['stockUnits']['acid']), (200, 'g'))
        state = self.call('/api/tests', {'readings': reading(fc=0.5, ph=7.9)})[1]
        self.assertEqual(state['current']['steps'][0]['unit'], 'g')
        self.assertEqual(state['current']['steps'][1]['amount'], 350)
        self.assertTrue(state['current']['balance']['available'])
        for bad in ({'volumeLitres': 5}, {'nonsense': 1}, {'ideal': {'fc': [0, 9]}}, {'strips': {'expiry': 'soon'}}, {'strips': {'expiry': '0000-05'}}, {'strips': {'expiry': '9999-12'}}, {'products': {'acid': {'kind': 'liquid', 'strength': 80}}}, {'testIntervalDays': 2.5}):
            self.assertEqual(self.call('/api/settings', bad)[0], 400, bad)
        self.assertEqual(self.call('/api/state')[1]['settings']['volumeLitres'], 60000)

    def test_deleting_a_test_falls_back_to_the_previous_one(self):
        old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=3)).isoformat()
        self.call('/api/tests', {'id': 'c' * 24, 'at': old, 'readings': reading()})
        state = self.call('/api/tests', {'id': 'd' * 24, 'readings': reading(fc=0)})[1]
        self.assertEqual(state['current']['testId'], 'd' * 24)
        state = self.call('/api/void', {'group': 'tests', 'id': 'd' * 24})[1]
        self.assertEqual((state['current']['testId'], state['current']['headline'], len(state['tests'])), ('c' * 24, 'Water is balanced', 1))

    def test_a_light_box_calibration_replaces_the_chart_and_can_be_reset(self):
        built_in = self.call('/api/state')[1]['chart']
        self.assertNotIn('custom', built_in)
        params = {key: [[50 + i, 10 * i, -5 * i] for i in range(len(meta['swatches']))] for key, meta in built_in['params'].items()}
        status, state, _ = self.call('/api/chart', {'params': params})
        self.assertEqual(status, 200)
        # Ordinary photos keep the built-in (daylight) chart; only the light box reads against the measured one.
        self.assertEqual(state['chart'], built_in)
        self.assertTrue(state['boxChart']['custom'])
        self.assertEqual(state['boxChart']['params']['ph']['swatches'][2], {'value': 7.2, 'lab': [52, 20, -10]})
        self.assertEqual(state['boxChart']['params']['ph']['ideal'], [7.2, 7.6])
        for bad in ({'params': {**params, 'cya': params['cya'][:3]}}, {'params': {'fc': params['fc']}}, {'params': {**params, 'fc': [[500, 0, 0]] * 6}}, {'params': {**params, 'fc': [['a', 0, 0]] * 6}}):
            self.assertEqual(self.call('/api/chart', bad)[0], 400)
        state = self.call('/api/tests', {'source': 'box', 'readings': reading()})[1]
        self.assertEqual(state['tests'][0]['source'], 'box')
        state = self.call('/api/chart', {'reset': True})[1]
        self.assertEqual((state['chart'], state['boxChart']), (built_in, built_in))

    def test_a_calibration_keeps_its_picture_and_where_the_chart_sat(self):
        built_in = self.call('/api/state')[1]['chart']
        params = {key: [[50 + i, 10 * i, -5 * i] for i in range(len(meta['swatches']))] for key, meta in built_in['params'].items()}
        marks = {'a': {'x': 0.31234567, 'y': 0.4}, 'b': {'x': 0.7, 'y': 0.4}, 'c': {'x': 0.3, 'y': 0.62}}
        self.assertEqual(self.call('/api/photo?id=calibration', raw=True)[0], 404)
        state = self.call('/api/chart', {'params': params, 'marks': marks, 'photo': JPEG})[1]
        self.assertEqual((state['boxChart']['hasPhoto'], state['boxChart']['marks']['a']), (True, {'x': 0.3123, 'y': 0.4}))
        status, raw, headers = self.call('/api/photo?id=calibration', raw=True)
        self.assertEqual((status, raw[:3], headers.getheader("Cache-Control")), (200, b'\xff\xd8\xff', 'no-store'))
        for bad in ({'params': params, 'marks': {'a': marks['a']}}, {'params': params, 'marks': {**marks, 'c': {'x': 9, 'y': 0}}}, {'params': params, 'photo': 'data:image/png;base64,AAAA'}):
            self.assertEqual(self.call('/api/chart', bad)[0], 400)
        # A calibration saved without a picture, and a reset, both drop the old picture.
        self.assertNotIn('hasPhoto', self.call('/api/chart', {'params': params})[1]['boxChart'])
        self.assertEqual(self.call('/api/photo?id=calibration', raw=True)[0], 404)
        self.call('/api/chart', {'params': params, 'photo': JPEG})
        self.call('/api/chart', {'reset': True})
        self.assertEqual(self.call('/api/photo?id=calibration', raw=True)[0], 404)

    def test_password_protects_the_standalone_address(self):
        server.PASSWORD = 'correct horse battery'
        self.assertEqual(self.call('/api/state')[0], 401)
        self.assertEqual(self.call('/api/session')[1], {'authenticated': False, 'passwordRequired': True})
        self.assertEqual(self.call('/api/tests', {'readings': reading()})[0], 401)
        self.assertEqual(self.call('/api/login', {'username': 'owner', 'password': 'wrong'})[0], 401)
        self.assertEqual(self.call('/api/login', {'password': 'correct horse battery'})[0], 401)   # a username is required now
        status, _, response = self.call('/api/login', {'username': ' Owner ', 'password': 'correct horse battery'})
        cookie = response.getheader('Set-Cookie')
        self.assertEqual(status, 200); self.assertIn('HttpOnly', cookie); self.assertIn('SameSite=Strict', cookie)
        status, state, _ = self.call('/api/state', headers={'Cookie': cookie.split(';')[0]})
        self.assertEqual((status, state['me']), (200, {'username': 'owner', 'name': '', 'owner': True, 'session': True}))
        for forged in ('pool_session=9999999999.forged', 'pool_session=1.9999999999.forged', 'pool_session=2.9999999999.forged'):
            self.assertEqual(self.call('/api/state', headers={'Cookie': forged})[0], 401)
        # Changing the configured password ends the sessions signed with the old one.
        server.PASSWORD = 'a different password'
        self.assertEqual(self.call('/api/state', headers={'Cookie': cookie.split(';')[0]})[0], 401)

    def session(self, issued, uid=None, name='pool_session'):
        uid = uid or server.OWNER
        return {'Cookie': f'{name}=' + server.session_value(uid, server.session_mark(uid), str(int(issued)))}

    def test_a_sign_in_does_not_expire_and_is_renewed_by_use(self):
        server.PASSWORD = 'correct horse battery'
        clock = dt.datetime.now().timestamp()
        # Signed in an hour ago: the same cookie is handed back with a new 400-day life.
        recent = self.session(clock - 3600)
        status, _, response = self.call('/api/state', headers=recent)
        renewed = response.getheader('Set-Cookie')
        self.assertEqual(status, 200); self.assertTrue(renewed.startswith(recent['Cookie'] + ';'))
        self.assertIn('Max-Age=34560000', renewed); self.assertIn('HttpOnly', renewed); self.assertIn('SameSite=Strict', renewed)
        # Signed in a year ago and used since: still good, and swapped for a fresh session.
        status, _, response = self.call('/api/state', headers=self.session(clock - 365 * 86400))
        fresh = response.getheader('Set-Cookie').split(';')[0]
        self.assertEqual(status, 200); self.assertNotEqual(fresh, self.session(clock - 365 * 86400)['Cookie'])
        self.assertEqual(self.call('/api/state', headers={'Cookie': fresh})[0], 200)
        # Only a cookie no browser would still be holding is refused: unused for longer than a cookie lives.
        self.assertEqual(self.call('/api/state', headers=self.session(clock - 401 * 86400))[0], 401)
        # Other requests do not hand the cookie out again, and nothing renews a session that is not valid.
        self.assertIsNone(self.call('/api/session', headers=recent)[2].getheader('Set-Cookie'))
        self.assertIsNone(self.call('/api/state', headers={'Cookie': f'pool_session=1.{int(clock)}.' + '0' * 64})[2].getheader('Set-Cookie'))

    def test_a_session_from_before_this_version_keeps_working(self):
        server.PASSWORD = 'correct horse battery'
        clock = dt.datetime.now().timestamp()
        old_style = self.session(clock + 20 * 86400)   # 0.6.0 wrote the expiry there: signed in 10 days ago
        status, _, response = self.call('/api/state', headers=old_style)
        self.assertEqual(status, 200)
        self.assertNotEqual(response.getheader('Set-Cookie').split(';')[0], old_style['Cookie'])   # replaced by a new-style one
        saved = server.ORIGIN
        try:
            # On the real HTTPS address the cookie has a new, host-locked name. The old name is accepted
            # for a pre-0.7.0 session only, never for a new-style one planted under it.
            server.ORIGIN = 'https://pool.example.com'
            host = {'Host': 'pool.example.com'}
            status, _, response = self.call('/api/state', headers={**old_style, **host})
            cookie = response.getheader('Set-Cookie')
            self.assertEqual(status, 200); self.assertTrue(cookie.startswith('__Host-pool_session=')); self.assertIn('; Secure', cookie); self.assertNotIn('Domain', cookie)
            self.assertEqual(self.call('/api/state', headers={'Cookie': cookie.split(';')[0], **host})[0], 200)
            self.assertEqual(self.call('/api/state', headers={**self.session(clock - 60), **host})[0], 401)
            self.assertEqual(self.call('/api/state', headers={**self.session(clock - 60, name='__Host-pool_session'), **host})[0], 200)
        finally:
            server.ORIGIN = saved

    def test_signing_out_ends_the_session_everywhere(self):
        server.PASSWORD = 'correct horse battery'
        good = {'username': 'owner', 'password': 'correct horse battery'}
        phone = {'Cookie': self.call('/api/login', good)[2].getheader('Set-Cookie').split(';')[0]}
        copied = dict(phone)   # the same cookie, lifted from the phone
        tablet = self.session(dt.datetime.now().timestamp() - 5 * 86400)
        self.assertEqual([self.call('/api/state', headers=h)[0] for h in (phone, tablet)], [200, 200])
        self.assertIn('Max-Age=0', self.call('/api/logout', {}, phone)[2].getheader('Set-Cookie'))
        self.assertEqual([self.call('/api/state', headers=h)[0] for h in (copied, tablet)], [401, 401])
        # Signing in again works at once, and a sign-out by someone not signed in ends nobody's session.
        again = {'Cookie': self.call('/api/login', good)[2].getheader('Set-Cookie').split(';')[0]}
        self.assertEqual(self.call('/api/state', headers=again)[0], 200)
        self.call('/api/logout', {}); self.call('/api/logout', {}, {'Cookie': 'pool_session=1.1.forged'})
        self.assertEqual(self.call('/api/state', headers=again)[0], 200)

    def test_malformed_cookies_and_paths_are_refused_quietly(self):
        server.PASSWORD = 'correct horse battery'
        for cookie in ('pool_session="1.1.\303\251"', 'pool_session=99999999999999999999.1.x', 'pool_session=1.1', 'pool_session=-1.1.x', 'pool_session=1.9e9.x', 'pool_session=' + 'a' * 5000):
            self.assertEqual(self.call('/api/state', headers={'Cookie': cookie})[0], 401, cookie)
        self.assertEqual(self.call('/%00', raw=True)[0], 404)
        conn = http.client.HTTPConnection('127.0.0.1', PORT, timeout=5)
        conn.request('HEAD', '/app.js'); self.assertEqual(conn.getresponse().status, 405); conn.close()
        # A request whose headers add up to more than the budget is refused, not swallowed.
        conn = http.client.HTTPConnection('127.0.0.1', PORT, timeout=5)
        try:
            conn.request('GET', '/api/session', headers={f'X-Pad-{i}': 'x' * 4000 for i in range(12)})
            self.assertEqual(conn.getresponse().status, 431)
        except (http.client.HTTPException, OSError): pass   # or the connection is simply dropped
        finally: conn.close()

    def test_a_password_that_cannot_be_text_reveals_nothing_about_usernames(self):
        server.PASSWORD = 'correct horse battery'
        for username in ('owner', 'nobody', 'bad name\ud800'):
            status, data, _ = self.call('/api/login', {'username': username, 'password': '\ud800'})
            self.assertEqual((status, data['error']), (401, 'Username or password not recognised.'), username)

    def test_strip_photos_are_deleted_after_three_months_and_the_readings_stay(self):
        old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=100)).isoformat()
        self.call('/api/tests', {'id': 'a' * 24, 'at': old, 'source': 'photo', 'readings': reading(fc=1.5), 'photo': JPEG})
        self.call('/api/tests', {'id': 'b' * 24, 'source': 'box', 'readings': reading(), 'photo': JPEG})
        aged, fresh = server.PHOTOS / ('a' * 24 + '.jpg'), server.PHOTOS / ('b' * 24 + '.jpg')
        calibration = server.calibration_photo(server.OWNER); calibration.write_bytes(aged.read_bytes())
        try:
            long_ago = dt.datetime.now().timestamp() - 100 * 86400
            os.utime(aged, (long_ago, long_ago)); os.utime(calibration, (long_ago, long_ago))
            self.assertEqual(server.prune_photos(server.OWNER, dt.datetime.now().timestamp() - 20 * 86400), 0)   # 80 days old then: kept
            self.assertEqual(server.prune_photos(server.OWNER), 1)
            self.assertEqual((aged.exists(), fresh.exists(), calibration.exists()), (False, True, True))
            tests = {t['id']: t for t in self.call('/api/state')[1]['tests']}
            self.assertEqual((tests['a' * 24]['hasPhoto'], tests['a' * 24]['readings']['fc']['value'], tests['b' * 24]['hasPhoto']), (False, 1.5, True))
            self.assertEqual(self.call('/api/photo?id=' + 'a' * 24, raw=True)[0], 404)
            revision = server.get_state()['revision']
            self.assertEqual((server.prune_photos(server.OWNER), server.get_state()['revision']), (0, revision))   # nothing to do, nothing rewritten
        finally:
            calibration.unlink()

    def test_one_persons_photos_are_capped_in_number_and_size_and_go_with_a_deleted_test(self):
        saved = server.KEEP_PHOTOS, server.PHOTO_BYTES
        try:
            server.KEEP_PHOTOS = 2
            for i, ident in enumerate(('1' * 24, '2' * 24, '3' * 24)):
                self.call('/api/tests', {'id': ident, 'source': 'photo', 'readings': reading(), 'photo': JPEG})
                stamp = dt.datetime.now().timestamp() - (10 - i) * 60
                os.utime(server.PHOTOS / (ident + '.jpg'), (stamp, stamp))
            server.prune_photos(server.OWNER)
            self.assertEqual(sorted(f.stem[0] for f in server.PHOTOS.glob('*.jpg')), ['2', '3'])
            server.KEEP_PHOTOS, server.PHOTO_BYTES = 300, (server.PHOTOS / ('3' * 24 + '.jpg')).stat().st_size + 10
            server.prune_photos(server.OWNER)
            self.assertEqual([f.stem[0] for f in server.PHOTOS.glob('*.jpg')], ['3'])
            self.assertEqual({t['id'][0]: t['hasPhoto'] for t in server.get_state()['tests']}, {'1': False, '2': False, '3': True})
            self.call('/api/void', {'group': 'tests', 'id': '3' * 24})
            self.assertEqual(list(server.PHOTOS.glob('*.jpg')), [])
        finally:
            server.KEEP_PHOTOS, server.PHOTO_BYTES = saved

    def test_repeated_wrong_passwords_are_locked_out(self):
        server.PASSWORD = 'correct horse battery'
        for _ in range(10): self.assertEqual(self.call('/api/login', {'username': 'owner', 'password': 'guess'})[0], 401)
        self.assertEqual(self.call('/api/login', {'username': 'owner', 'password': 'correct horse battery'})[0], 429)

    def test_sign_in_attempts_are_counted_per_visitor_per_username_and_in_all(self):
        server.PASSWORD = 'correct horse battery'
        good = {'username': 'owner', 'password': 'correct horse battery'}
        attacker, friend = {'CF-Connecting-IP': '203.0.113.5'}, {'CF-Connecting-IP': '198.51.100.7'}
        # One visitor trying many usernames is stopped, and nobody else is.
        for i in range(10): self.assertEqual(self.call('/api/login', {'username': f'guess{i}', 'password': 'x'}, attacker)[0], 401)
        self.assertEqual(self.call('/api/login', good, attacker)[0], 429)
        self.assertEqual(self.call('/api/login', good, friend)[0], 200)
        # A header that is not an address is ignored, not trusted.
        self.assertEqual(self.call('/api/login', good, {'CF-Connecting-IP': 'not-an-address'})[0], 200)
        # Many visitors hammering one username are stopped for that username.
        server.LOGIN_ATTEMPTS.clear()
        for i in range(10): self.call('/api/login', {'username': 'owner', 'password': 'x'}, {'CF-Connecting-IP': f'203.0.113.{i}'})
        self.assertEqual(self.call('/api/login', good, friend)[0], 429)
        # And a flood from everywhere at every username hits the overall limit, which a good sign-in does not reset.
        server.LOGIN_ATTEMPTS.clear(); saved = dict(server.LOGIN_LIMITS)
        try:
            server.LOGIN_LIMITS['all'] = 4
            self.assertEqual(self.call('/api/login', good, friend)[0], 200)
            for i in range(4): self.call('/api/login', {'username': f'u{i}', 'password': 'x'}, {'CF-Connecting-IP': f'203.0.113.{i}'})
            self.assertEqual(self.call('/api/login', good, friend)[0], 429)
            self.assertLess(len(server.LOGIN_ATTEMPTS), 12)
            for times in server.LOGIN_ATTEMPTS.values(): times[:] = [t - 400 for t in times]   # five minutes later
            self.assertEqual(self.call('/api/login', good, friend)[0], 200)
            self.assertEqual(server.LOGIN_ATTEMPTS, {})
        finally:
            server.LOGIN_LIMITS.update(saved)

    def test_attempts_are_counted_before_the_password_is_checked(self):
        keys = ('from:203.0.113.9', 'user:owner', 'all:')
        tickets = [server.password_attempt(keys) for _ in range(12)]   # a burst, none of them answered yet
        self.assertEqual([bool(ticket) for ticket in tickets], [True] * 10 + [False] * 2)
        server.password_accepted(keys, tickets[0])
        self.assertEqual({key: len(times) for key, times in server.LOGIN_ATTEMPTS.items()}, {'all:': 9})

    def test_only_a_photo_upload_may_be_large(self):
        padding = 'x' * 20000
        self.assertEqual(self.call('/api/settings', {'volumeLitres': 50000, 'padding': padding})[0], 400)
        self.assertEqual(self.call('/api/doses', {'product': 'chlorine', 'amount': 100, 'padding': padding})[0], 400)
        self.assertEqual(self.call('/api/state')[1]['settings']['volumeLitres'], 120000)
        self.assertEqual(self.call('/api/tests', {'readings': reading(), 'photo': JPEG})[0], 200)   # about 270 kB
        self.assertEqual(self.call('/api/lab', {'manual': {'at': server.now(), 'values': {'fc': 2, 'ph': 7.4, 'ta': 100, 'cya': 40}}, 'photo': JPEG})[0], 200)
        server.lab_photo(server.OWNER, server.get_state()['tests'][-1]['id']).unlink()

    def test_nothing_sizeable_is_accepted_before_signing_in(self):
        server.PASSWORD = 'correct horse battery'
        self.assertEqual(self.call('/api/login', {'username': 'owner', 'password': 'x' * 5000})[0], 400)
        self.assertEqual(server.LOGIN_ATTEMPTS, {})   # refused for its size, before any password was checked
        # A request that is not signed in is answered from its headers alone; the body it announces is never read.
        conn = http.client.HTTPConnection('127.0.0.1', PORT, timeout=5)
        conn.putrequest('POST', '/api/tests')
        for key, value in {'Origin': f'http://127.0.0.1:{PORT}', 'Content-Type': 'application/json', 'Content-Length': str(5 * 1024 * 1024)}.items(): conn.putheader(key, value)
        conn.endheaders()
        self.assertEqual(conn.getresponse().status, 401); conn.close()

    def test_a_persons_history_cannot_grow_without_limit(self):
        saved = server.MAX_RECORDS
        try:
            server.MAX_RECORDS = 2
            for _ in range(2): self.assertEqual(self.call('/api/tests', {'readings': reading(fc=0.5)})[0], 200)
            status, data, _ = self.call('/api/tests', {'readings': reading()})
            self.assertEqual(status, 400); self.assertIn('history is full', data['error'])
            for _ in range(2): self.assertEqual(self.call('/api/doses', {'product': 'chlorine', 'amount': 100})[0], 200)
            self.assertEqual(self.call('/api/doses', {'product': 'chlorine', 'amount': 100})[0], 400)
            self.assertEqual(len(server.get_state()['tests']), 2)
        finally:
            server.MAX_RECORDS = saved

    def test_responses_carry_the_security_headers_and_do_not_name_the_python_version(self):
        for path in ('/', '/app.js', '/api/session', '/nothing-here'):
            response = self.call(path, raw=True)[2]
            self.assertIn("object-src 'none'", response.getheader('Content-Security-Policy'))
            self.assertEqual((response.getheader('X-Content-Type-Options'), response.getheader('Cross-Origin-Resource-Policy')), ('nosniff', 'same-origin'))
            self.assertNotIn('Python', response.getheader('Server'))
        self.assertIsNone(self.call('/api/session')[2].getheader('Strict-Transport-Security'))   # plain HTTP in the tests
        saved = server.ORIGIN
        try:
            server.ORIGIN = 'https://pool.example.com'
            response = self.call('/api/session', headers={'Host': 'pool.example.com'})[2]
            self.assertEqual(response.getheader('Strict-Transport-Security'), 'max-age=31536000')
        finally:
            server.ORIGIN = saved

    def test_a_flood_of_idle_connections_is_turned_away_and_service_resumes(self):
        saved = server.MAX_CONNECTIONS
        try:
            server.MAX_CONNECTIONS = 2
            httpd = server.BoundedServer(('127.0.0.1', 0), server.Handler)
        finally:
            server.MAX_CONNECTIONS = saved
        port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        def get():
            conn = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
            try:
                conn.request('GET', '/api/session', headers={'Host': f'127.0.0.1:{PORT}'})
                return conn.getresponse().status
            except (http.client.HTTPException, OSError): return None
            finally: conn.close()
        try:
            self.assertEqual(get(), 200)
            idle = [socket.create_connection(('127.0.0.1', port)) for _ in range(2)]   # connected, saying nothing
            deadline = dt.datetime.now() + dt.timedelta(seconds=5)
            while get() is not None and dt.datetime.now() < deadline: pass
            self.assertIsNone(get())
            for sock in idle: sock.close()
            deadline = dt.datetime.now() + dt.timedelta(seconds=5)
            while get() != 200 and dt.datetime.now() < deadline: pass
            self.assertEqual(get(), 200)
        finally:
            httpd.shutdown(); httpd.server_close()

    def test_the_pool_surface_is_a_choice_and_changes_the_advice(self):
        self.assertEqual(self.call('/api/settings', {'surface': 'marble'})[0], 400)
        status, state, _ = self.call('/api/settings', {'surface': 'fibreglass'})
        self.assertEqual((status, state['settings']['surface'], sorted(state['surfaces'])), (200, 'fibreglass', ['fibreglass', 'gunite', 'vinyl']))
        step = self.call('/api/tests', {'readings': reading(fc=0.5)})[1]['current']['steps'][0]
        self.assertIn('Do not let granules settle', step['how'])
        self.assertIn('fibreglass pool makers', step['why'])


CODE = '0123456789abcdef' * 3   # an invented report code, the length of a real one
LINK = server.LAB_PAGE + CODE


def shop_report(days_ago=2, **changes):
    """A WaterLink report as the service sends it, for an invented customer of an invented shop."""
    row = lambda factor, value, unit='ppm': {'testFactorId': factor, 'value': value, 'unitOfMeasure': unit, 'inputSource': 'FromSpinTouch', 'inputMeterId': 'Spin Touch', 'idealRange': None, 'tags': []}
    report = {
        'waterTestId': 'aaaaaaaa-0000-4000-8000-000000000001', 'isPersisted': True,
        'testedSite': {'name': 'home', 'volume': 50000.0, 'volumeUnit': 'Liters'},
        'testedSiteProperty': {'name': 'Jo Bloggs property', 'address': {'line1': '1 Example Street', 'city': 'Exampleton'}, 'phone': '0115550100'},
        'reportContact': {'firstName': 'Jo', 'lastName': 'Bloggs', 'phone': '0115550100', 'email': 'jo@example.com'},
        'ownerPreferences': {'name': 'Example Stores', 'email': 'pools@example.com'},
        'ownerLocation': {'name': 'Example Branch', 'addressLine1': '2 Shop Road'},
        'treatmentProfile': {'name': 'Plaster pools'},
        'waterTestDate': (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days_ago)).isoformat(timespec='milliseconds'),
        'measurements': [row(2, 3.86), row(3, 3.86), row(4, 0.0), row(8, 7.3, None), row(9, 67.0), row(11, 705.0), row(12, 42.0), row(13, 0.0),
                         row(16, 0.5), row(18, 3183.0, 'ppb'), {**row(63, 54.0), 'inputSource': 'Calculated', 'inputMeterId': None}],
        'conditions': [{'waterConditionId': 1, 'present': True}, {'waterConditionId': 6, 'present': False}],
        'recommendations': [
            {'actionText': '<span style="color:#ff0000;"><p><strong>ATTENTION: never mix chemicals.</strong></p></span>', 'triggerType': 'Static', 'triggerId': -1},
            {'actionText': '<p><strong><span style="font-family: Calibri;">Metals (copper) are high. Treat with 2.5l of&nbsp;Metal Remover&reg;.</span></strong></p><p>Circulate for 8 hours.</p>',
             'chemical': {'quantity': 2400.0, 'unitOfMeasure': 'Milliliters'}, 'triggerType': 'TestFactor', 'triggerId': 16}],
        'waterTestNotes': '',
    }
    report.update(changes)
    return report


class PoolShop(unittest.TestCase):
    """A test done at a pool shop is kept with the strip tests. The shop's service is replaced by a
    stand-in here: nothing in these tests leaves this machine."""
    setUpClass, tearDownClass, call = Api.setUpClass.__func__, Api.tearDownClass.__func__, Api.call
    setUpClass, tearDownClass = classmethod(setUpClass), classmethod(tearDownClass)

    def setUp(self):
        Api.setUp(self)
        server.LAB_FETCHES.clear()
        self.asked, self.report = [], shop_report()
        self.real_fetch = server.fetch_report
        server.fetch_report = lambda code: (self.asked.append(code), copy.deepcopy(self.report))[1]
        self.addCleanup(setattr, server, 'fetch_report', self.real_fetch)
        self.addCleanup(lambda: [f.unlink() for f in server.PHOTOS.glob('*.jp*g')])

    def test_a_report_is_imported_from_its_link(self):
        self.call('/api/tests', {'id': 'a' * 24, 'at': (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=5)).isoformat(), 'readings': reading()})
        status, state, _ = self.call('/api/lab', {'url': '  ' + LINK + '\n'})
        self.assertEqual((status, self.asked), (200, [CODE]))
        test = state['tests'][0]
        self.assertEqual((test['source'], test['hasPhoto'], test['at'][:16]), ('lab', False, self.report['waterTestDate'][:16]))
        # Total alkalinity is the app's reading; the shop's "adjusted" figure is kept beside it, not in its place.
        self.assertEqual({key: (row['value'], row['confidence'], row['atMax']) for key, row in test['readings'].items()},
                         {'fc': (3.86, 'lab', False), 'ph': (7.3, 'lab', False), 'ta': (67.0, 'lab', False), 'cya': (42.0, 'lab', False)})
        lab = test['lab']
        self.assertEqual(lab['extras'], {'tc': 3.86, 'cc': 0.0, 'taAdjusted': 54.0, 'ch': 705.0, 'copper': 0.5, 'iron': 0.0, 'phosphate': 3183.0})
        self.assertEqual((lab['origin'], lab['shop'], lab['company'], lab['profile'], lab['meter'], lab['url']), ('waterlink', 'Example Branch', 'Example Stores', 'Plaster pools', 'Spin Touch', LINK))
        self.assertEqual(lab['conditions'], ['Green colour'])
        self.assertEqual(lab['advice'], ['ATTENTION: never mix chemicals.', 'Metals (copper) are high. Treat with 2.5l of Metal Remover®. Circulate for 8 hours.'])
        # It is the newest test, so the plan and Home Assistant follow it, with the app's own rules: alkalinity 67 is topped up to 90.
        self.assertEqual((state['current']['testId'], state['statuses'][test['id']]), (test['id'], {'fc': 'ok', 'ta': 'low', 'ph': 'ok', 'cya': 'ok'}))
        self.assertEqual([s['key'] for s in state['current']['steps']], ['ta-up'])
        sensors = dict((row[0], row) for row in ha_pool.sensors(server.get_state(), server.current_analysis(server.get_state()), []))
        self.assertEqual((sensors['sensor.pool_ph'][1], sensors['sensor.pool_ph'][2]['confidence']), (7.3, 'lab'))
        # The shop's calcium hardness is what the water balance was waiting for. No strip was used.
        self.assertEqual((state['imported'], state['settings']['calciumHardness'], state['current']['balance']['available']), ({'id': test['id'], 'calcium': 705}, 705, True))
        self.assertEqual(state['settings']['strips']['remaining'], 24)   # one for the strip test above, none for this
        # The report names its customer. None of that is kept, here or in a backup.
        kept = json.dumps(self.call('/api/backup')[1])
        for private in ('Bloggs', 'Jo ', 'example.com', '0115550100', 'Example Street', 'Exampleton', 'Shop Road', 'aaaaaaaa-0000'): self.assertNotIn(private, kept)

    def test_only_a_genuine_report_link_is_fetched(self):
        for bad in ('http://solutions.waterlinkconnect.com/treatment-reports/' + CODE, 'https://example.com/treatment-reports/' + CODE,
                    'https://solutions.waterlinkconnect.com.example.com/treatment-reports/' + CODE, 'https://solutions.waterlinkconnect.com@example.com/treatment-reports/' + CODE,
                    server.LAB_PAGE + '../owners/mine', server.LAB_PAGE + CODE + '/../../owners', server.LAB_PAGE + CODE + '?next=https://example.com',
                    server.LAB_PAGE + 'g' * 48, server.LAB_PAGE + CODE[:20], LINK + ' ' + LINK, 'x' * 5000, '', 123, ['a'], {'url': LINK}):
            status, answer, _ = self.call('/api/lab', {'url': bad})
            self.assertEqual((status, 'not a pool shop report link' in answer['error']), (400, True), bad)
        self.assertEqual(self.call('/api/lab', {})[0], 400)
        self.assertEqual((self.asked, self.call('/api/state')[1]['tests']), ([], []))
        # The code on its own will do, in capitals too, and a link is tidied to one form.
        self.assertEqual(self.call('/api/lab', {'url': CODE.upper() + '/'})[1]['tests'][0]['lab']['url'], LINK)
        self.assertEqual(self.asked, [CODE])
        # Nobody can pass a strip test off as a shop's by naming the source.
        self.assertEqual(self.call('/api/tests', {'source': 'lab', 'readings': reading()})[1]['tests'][0]['source'], 'manual')

    def test_a_report_is_kept_once_and_can_come_back_after_being_deleted(self):
        first = self.call('/api/lab', {'url': LINK})[1]['tests'][0]['id']
        status, answer, _ = self.call('/api/lab', {'url': LINK})
        self.assertEqual((status, answer['error'], len(self.asked)), (400, 'That report is already in your history.', 1))
        self.call('/api/void', {'group': 'tests', 'id': first})
        self.assertEqual(self.call('/api/state')[1]['tests'], [])
        state = self.call('/api/lab', {'url': LINK})[1]
        self.assertEqual(([t['id'] for t in state['tests']], len(server.get_state()['tests'])), ([first], 1))

    def test_what_a_report_says_is_not_trusted(self):
        self.report['recommendations'] = [{'actionText': 'Add <b>2 kg</b><script>fetch("//example.com/" + document.cookie)</script><style>p{}</style><img src=x onerror="alert(1)"> now\x00\u202e. &lt;script&gt;x&lt;/script&gt;' + 'y' * 5000}] * 30
        self.report['ownerLocation'] = {'name': '<i>Branch</i>\r\n' + 'z' * 500}
        self.report['measurements'] += [{'testFactorId': [1], 'value': 1}, {'testFactorId': 999, 'value': 5, 'unitOfMeasure': 'ppm'}, 'junk', {'testFactorId': True, 'value': 1},
                                        {'testFactorId': 19, 'value': 3.2, 'unitOfMeasure': 'ppt'}]   # salt in a unit the app does not expect: left out, not guessed at
        self.report['conditions'] = [{'waterConditionId': 99, 'present': True}, {'waterConditionId': '1', 'present': True}, {'waterConditionId': 6, 'present': 'yes'}, None]
        lab = self.call('/api/lab', {'url': LINK})[1]['tests'][0]['lab']
        self.assertEqual(len(lab['advice']), 12)
        self.assertTrue(lab['advice'][0].startswith('Add 2 kg now . <script>x</script>yyy'), lab['advice'][0][:60])   # words only; shown as text, never as a page
        self.assertEqual((len(lab['advice'][0]), 'fetch' in lab['advice'][0], 'onerror' in lab['advice'][0]), (1500, False, False))
        self.assertEqual((lab['shop'], 'conditions' in lab, 'salt' in lab['extras']), (('<i>Branch</i> ' + 'z' * 500)[:60], False, False))
        for wrong, said in (({'measurements': [m for m in shop_report()['measurements'] if m['testFactorId'] != 12]}, 'Missing: stabiliser'),
                            ({'measurements': shop_report()['measurements'][:3] + [{'testFactorId': 8, 'value': 73, 'unitOfMeasure': None}]}, 'pH must be a number between 4 and 11'),
                            ({'measurements': [{**m, 'value': '3.86'} for m in shop_report()['measurements']]}, 'must be a number'),
                            ({'measurements': [{**m, 'value': 1e12} for m in shop_report()['measurements']]}, 'must be a number'),
                            ({'measurements': {'2': 1}}, 'Missing'), ({'waterTestDate': None}, 'no usable test date'), ({'waterTestDate': '2999-01-01T00:00:00+00:00'}, 'no usable test date'),
                            ({'waterTestDate': '2026-01-01T00:00:00'}, 'no usable test date')):
            self.report = shop_report(**wrong)
            status, answer, _ = self.call('/api/lab', {'url': server.LAB_PAGE + 'ab' * 20})
            self.assertEqual((status, said in answer['error']), (400, True), answer)
        for junk in ([], 'text', None, 7):
            self.report = junk
            self.assertEqual(self.call('/api/lab', {'url': server.LAB_PAGE + 'ab' * 20})[0], 400)
        self.assertEqual(len(self.call('/api/state')[1]['tests']), 1)

    def test_a_printed_report_is_typed_in_and_its_picture_is_kept(self):
        when = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)).isoformat()
        body = {'id': 'c' * 24, 'manual': {'at': when, 'shop': ' Corner  pool shop ', 'note': 'Told to add metal remover first.',
                                          'values': {'fc': 1.2, 'ph': 7.9, 'ta': 110, 'cya': 35, 'ch': 320, 'phosphate': 500, 'unknown': 4}}, 'photo': JPEG}
        status, state, _ = self.call('/api/lab', body)
        test = state['tests'][0]
        self.assertEqual((status, test['source'], test['hasPhoto'], test['note'], test['lab']), (200, 'lab', True, 'Told to add metal remover first.',
                         {'origin': 'manual', 'shop': 'Corner pool shop', 'extras': {'ch': 320, 'phosphate': 500}}))
        self.assertEqual((state['statuses']['c' * 24]['ph'], state['settings']['calciumHardness'], state['imported']), ('high', 320, {'id': 'c' * 24, 'calcium': 320}))
        self.assertEqual(len(self.call('/api/lab', body)[1]['tests']), 1)   # a retried save
        status, picture, response = self.call('/api/photo?id=' + 'c' * 24, raw=True)
        self.assertEqual((status, response.getheader('Content-Type'), picture[:3]), (200, 'image/jpeg', b'\xff\xd8\xff'))
        # The picture is the only record of what the shop advised, so it does not age out with the strip photos...
        file = server.lab_photo(server.OWNER, 'c' * 24)
        long_ago = dt.datetime.now().timestamp() - 400 * 86400
        os.utime(file, (long_ago, long_ago))
        self.assertEqual((server.prune_photos(server.OWNER), file.exists(), server.get_state()['tests'][0]['hasPhoto']), (0, True, True))
        # ...only the newest few are kept...
        saved, server.LAB_PHOTOS = server.LAB_PHOTOS, 1
        try:
            self.call('/api/lab', {'id': 'd' * 24, 'manual': {'at': when, 'values': {'fc': 1, 'ph': 7.4, 'ta': 100, 'cya': 40}}, 'photo': JPEG})
            self.assertEqual(({t['id'][0]: t['hasPhoto'] for t in server.get_state()['tests']}, file.exists()), ({'c': False, 'd': True}, False))
        finally: server.LAB_PHOTOS = saved
        # ...and a deleted test's picture goes with it.
        self.call('/api/void', {'group': 'tests', 'id': 'd' * 24})
        self.assertEqual((list(server.PHOTOS.glob('*.jpeg')), self.call('/api/photo?id=' + 'd' * 24, raw=True)[0]), ([], 404))
        for bad in ({'values': {'fc': 1, 'ph': 7.4, 'ta': 100}}, {'values': {'fc': 1, 'ph': 7.4, 'ta': 100, 'cya': 40, 'copper': -1}}, {'values': {'fc': 1, 'ph': 7.4, 'ta': 100, 'cya': True}},
                    {'values': {'fc': 1, 'ph': 7.4, 'ta': 100, 'cya': 40}, 'note': 'n' * 501}, {'values': [1, 7.4, 100, 40]}, {'values': {'fc': 1, 'ph': 7.4, 'ta': 100, 'cya': 40}, 'at': 'yesterday'}):
            self.assertEqual(self.call('/api/lab', {'manual': {'at': when, **bad}})[0], 400, bad)
        self.assertEqual(self.call('/api/lab', {'manual': {'at': when, 'values': {'fc': 1, 'ph': 7.4, 'ta': 100, 'cya': 40}}, 'photo': 'data:image/png;base64,AAAA'})[0], 400)
        self.assertEqual(len(self.call('/api/state')[1]['tests']), 1)

    def test_an_older_report_does_not_undo_a_newer_calcium_figure(self):
        self.assertEqual(self.call('/api/lab', {'url': LINK})[1]['settings']['calciumHardness'], 705)
        self.report = shop_report(days_ago=40)
        self.report['measurements'][5]['value'] = 400.0
        state = self.call('/api/lab', {'url': server.LAB_PAGE + 'cd' * 24})[1]
        self.assertEqual((state['settings']['calciumHardness'], 'calcium' in state['imported'], len(state['tests'])), (705, False, 2))
        self.report = shop_report(days_ago=1)
        self.report['measurements'][5]['value'] = 3000.0   # measured, kept and shown, but beyond what the water balance setting accepts
        state = self.call('/api/lab', {'url': server.LAB_PAGE + 'ef' * 24})[1]
        self.assertEqual((state['settings']['calciumHardness'], state['tests'][0]['lab']['extras']['ch']), (705, 3000.0))

    def test_a_report_added_late_goes_in_at_its_own_time_and_disturbs_nothing_newer(self):
        ago = lambda **gap: (dt.datetime.now(dt.timezone.utc) - dt.timedelta(**gap)).isoformat()
        for ident, days, fc in (('1' * 24, 9, 3), ('2' * 24, 7, 2.5), ('3' * 24, 5, 2), ('4' * 24, 3, 1.5)):
            self.call('/api/tests', {'id': ident, 'at': ago(days=days), 'readings': reading(fc=fc)})
        before = self.call('/api/tests', {'id': '5' * 24, 'at': ago(hours=1), 'readings': reading(fc=0.5, ta=70)})[1]
        dose = self.call('/api/doses', {'testId': '5' * 24, 'stepKey': 'ta-up', 'product': 'alkalinityUp', 'amount': 4000})[1]
        self.report = shop_report(days_ago=2)
        after = self.call('/api/lab', {'url': LINK})[1]
        shop = after['imported']['id']
        # It sits third in the history, where its date puts it...
        self.assertEqual([t['id'] for t in after['tests']][:3], ['5' * 24, shop, '4' * 24])
        # ...and today's test, its plan, what has been ticked off, the reminders and the trends are exactly as they were.
        # The one thing it adds to the Today screen is the water balance, which was waiting for a calcium figure.
        self.assertEqual((dose['current'].pop('balance')['available'], after['current'].pop('balance')['available']), (False, True))
        for key in ('current', 'doses', 'insights'): self.assertEqual(after[key], dose[key], key)
        self.assertEqual((after['current']['testId'], [bool(s['doneDoseId']) for s in after['current']['steps']]), ('5' * 24, [True, False]))
        self.assertEqual(after['settings']['strips'], dose['settings']['strips'])
        sensors = lambda: {row[0]: row[1] for row in ha_pool.sensors(server.get_state(), server.current_analysis(server.get_state()), [])}
        self.assertEqual((sensors()['sensor.pool_free_chlorine'], sensors()['sensor.pool_last_tested']), (0.5, before['tests'][0]['at']))
        # The strip from a full day before is close enough to check the slow readings against, not chlorine or pH.
        self.assertEqual({key: row['verdict'] for key, row in after['stripChecks'][0]['readings'].items()}, {'fc': 'apart', 'ta': 'higher', 'ph': 'apart', 'cya': 'agrees'})
        self.assertEqual((after['stripChecks'][0]['stripId'], after['stripChecks'][0]['stripLater']), ('4' * 24, False))

    def test_a_shop_test_older_than_the_last_chemicals_added_gives_no_amounts(self):
        ago = lambda **gap: (dt.datetime.now(dt.timezone.utc) - dt.timedelta(**gap)).isoformat()
        self.call('/api/tests', {'id': '1' * 24, 'at': ago(days=3), 'readings': reading(ta=60)})
        self.call('/api/doses', {'testId': '1' * 24, 'stepKey': 'ta-up', 'product': 'alkalinityUp', 'amount': 6000, 'at': ago(days=1)})
        self.report = shop_report(days_ago=2)   # sampled before that alkalinity went in, entered after
        state = self.call('/api/lab', {'url': LINK})[1]
        current = state['current']
        self.assertEqual((current['testId'], current['steps'], current['dosedSince'], current['status']['ta']), (state['imported']['id'], [], True, 'low'))
        self.assertIn('HTH Alkalinity Up was added after this sample was taken', current['notes'][0])
        water = {row[0]: row for row in ha_pool.sensors(server.get_state(), server.current_analysis(server.get_state()), [])}['sensor.pool_water_status']
        self.assertEqual((water[2]['actions'], water[2]['notes'][0]), ([], current['notes'][0]))
        # A strip test afterwards is the water as it now is, and gets a plan as usual.
        self.assertEqual([s['key'] for s in self.call('/api/tests', {'readings': reading(fc=0.5)})[1]['current']['steps']], ['fc-up'])

    def test_a_calcium_figure_typed_in_by_hand_is_not_replaced(self):
        self.call('/api/settings', {'calciumHardness': 300})
        state = self.call('/api/lab', {'url': LINK})[1]
        self.assertEqual((state['settings']['calciumHardness'], state['imported'].get('calciumKept'), 'calcium' in state['imported']), (300, 300, False))
        # Taking the report's figure is then one tap, and a later report follows on from it.
        self.call('/api/settings', {'calciumHardness': 705})
        self.report = shop_report(days_ago=1)
        self.report['measurements'][5]['value'] = 650.0
        state = self.call('/api/lab', {'url': server.LAB_PAGE + 'cd' * 24})[1]
        self.assertEqual((state['settings']['calciumHardness'], state['imported'].get('calcium')), (650, 650))

    def test_fetches_are_limited_for_each_person(self):
        codes = [f'{i:02d}' * 24 for i in range(server.LAB_LIMIT + 1)]
        answers = [self.call('/api/lab', {'url': server.LAB_PAGE + code}) for code in codes]
        self.assertEqual([a[0] for a in answers], [200] * server.LAB_LIMIT + [400])
        self.assertEqual(('Too many reports' in answers[-1][1]['error'], len(self.asked)), (True, server.LAB_LIMIT))

    def test_a_shop_service_that_cannot_be_reached_is_reported_plainly(self):
        server.fetch_report = self.real_fetch
        saved, server.LAB_API = server.LAB_API, f'127.0.0.1:{free_port()}'   # nothing is listening there
        try: status, answer, _ = self.call('/api/lab', {'url': LINK})
        finally: server.LAB_API = saved
        self.assertEqual((status, answer['error']), (400, "The pool shop's report could not be fetched just now. Try again later, or enter the printout by hand."))
        self.assertEqual(self.call('/api/state')[1]['tests'], [])


class People(unittest.TestCase):
    """The owner adds people; each has a pool of their own that nobody else can reach."""
    setUpClass, tearDownClass, call = Api.setUpClass.__func__, Api.tearDownClass.__func__, Api.call
    setUpClass, tearDownClass = classmethod(setUpClass), classmethod(tearDownClass)
    SECRET = 'reef-kelp-tide'   # a made-up password for the made-up person below

    def setUp(self):
        Api.setUp(self)
        server.PASSWORD = 'correct horse battery'
        self.owner = self.sign_in('owner', 'correct horse battery')
        status, state, _ = self.call('/api/people', {'action': 'add', 'name': 'Sam', 'username': 'Sam.P', 'password': self.SECRET}, self.owner)
        self.assertEqual(status, 200)
        self.sam_id = state['people'][0]['id']
        self.sam = self.sign_in('sam.p', self.SECRET)

    def sign_in(self, username, password):
        status, _, response = self.call('/api/login', {'username': username, 'password': password})
        self.assertEqual(status, 200)
        return {'Cookie': response.getheader('Set-Cookie').split(';')[0]}

    def test_a_new_person_starts_empty_and_must_describe_their_pool_first(self):
        owner = self.call('/api/state', headers=self.owner)[1]
        self.assertEqual([(p['username'], p['name'], p['disabled']) for p in owner['people']], [('sam.p', 'Sam', False)])
        self.assertNotIn('secret', owner['people'][0])
        self.assertTrue(owner['people'][0]['lastSeenAt'])
        self.assertEqual(owner['runtime']['address'], f'http://127.0.0.1:{PORT}')
        state = self.call('/api/state', headers=self.sam)[1]
        self.assertEqual((state['needsSetup'], state['tests'], state['people']), (True, [], None))
        self.assertEqual(state['me'], {'username': 'sam.p', 'name': 'Sam', 'owner': False, 'session': True})
        # No dose is worked out from the owner's pool size.
        self.assertEqual(self.call('/api/tests', {'readings': reading(fc=0)}, self.sam)[0], 400)
        self.assertEqual(self.call('/api/plan', {'readings': reading(fc=0)}, self.sam)[0], 400)
        for bad in ({'volumeLitres': 30000}, {'volumeLitres': 30000, 'surface': 'sand'}, {'volumeLitres': 10, 'surface': 'vinyl'}):
            self.assertEqual(self.call('/api/setup', bad, self.sam)[0], 400, bad)
        status, state, _ = self.call('/api/setup', {'volumeLitres': 30000, 'surface': 'fibreglass'}, self.sam)
        self.assertEqual((status, state['needsSetup'], state['settings']['volumeLitres'], state['settings']['surface']), (200, False, 30000, 'fibreglass'))
        state = self.call('/api/tests', {'readings': reading(fc=0.5)}, self.sam)[1]
        self.assertEqual(state['current']['steps'][0]['amount'], 180)   # 175 g rounded: a quarter of the owner's 700 g, for a quarter of the water
        self.assertEqual(self.call('/api/state', headers=self.owner)[1]['settings']['volumeLitres'], 120000)

    def test_a_new_person_is_asked_to_calibrate_their_light_box_until_they_have_or_say_they_have_none(self):
        # Before the pool is described that comes first; the calibration is the step after it.
        self.assertEqual(self.call('/api/state', headers=self.sam)[1]['needsCalibration'], True)
        state = self.call('/api/setup', {'volumeLitres': 30000, 'surface': 'fibreglass'}, self.sam)[1]
        self.assertEqual((state['needsSetup'], state['needsCalibration'], state['boxChart'].get('custom')), (False, True, None))
        self.assertEqual(self.call('/api/tests', {'readings': reading()}, self.sam)[1]['needsCalibration'], True)   # it does not stop them testing
        params = {key: [[50 + i, 10 * i, -5 * i] for i in range(len(meta['swatches']))] for key, meta in state['chart']['params'].items()}
        state = self.call('/api/chart', {'params': params, 'photo': JPEG}, self.sam)[1]
        self.assertEqual((state['needsCalibration'], state['boxChart']['custom']), (False, True))
        # Going back to the built-in chart leaves the box uncalibrated again, so the reminder returns...
        self.assertEqual(self.call('/api/chart', {'reset': True}, self.sam)[1]['needsCalibration'], True)
        # ...unless they say they have no box. Calibrating later takes that back.
        for bad in ({}, {'have': 'no'}, {'have': 0}): self.assertEqual(self.call('/api/lightbox', bad, self.sam)[0], 400, bad)
        self.assertEqual(self.call('/api/lightbox', {'have': False}, self.sam)[1]['needsCalibration'], False)
        self.assertEqual(self.call('/api/lightbox', {'have': True}, self.sam)[1]['needsCalibration'], True)
        self.call('/api/lightbox', {'have': False}, self.sam)
        self.call('/api/chart', {'params': params}, self.sam)
        self.assertEqual((self.call('/api/chart', {'reset': True}, self.sam)[1]['needsCalibration'], 'noLightBox' in server.get_state(self.sam_id)), (True, False))
        # One person's answer is theirs alone: the owner, who has not calibrated either, is still asked.
        self.call('/api/lightbox', {'have': False}, self.sam)
        self.assertEqual(self.call('/api/state', headers=self.owner)[1]['needsCalibration'], True)

    def test_tests_photos_calibration_and_backups_are_private(self):
        self.call('/api/setup', {'volumeLitres': 30000, 'surface': 'vinyl'}, self.sam)
        mine = self.call('/api/tests', {'id': 'e' * 24, 'source': 'photo', 'readings': reading(fc=0.5), 'photo': JPEG}, self.sam)[1]
        self.assertEqual(len(mine['tests']), 1)
        self.call('/api/tests', {'id': 'f' * 24, 'source': 'photo', 'readings': reading(), 'photo': JPEG}, self.owner)
        owner = self.call('/api/state', headers=self.owner)[1]
        self.assertEqual([t['id'] for t in owner['tests']], ['f' * 24])
        self.assertEqual(owner['settings']['strips']['remaining'], 24)
        self.assertEqual(self.call('/api/photo?id=' + 'e' * 24, headers=self.sam, raw=True)[0], 200)
        self.assertEqual(self.call('/api/photo?id=' + 'e' * 24, headers=self.owner, raw=True)[0], 404)
        self.assertEqual(self.call('/api/photo?id=' + 'f' * 24, headers=self.sam, raw=True)[0], 404)
        self.assertEqual(self.call('/api/void', {'group': 'tests', 'id': 'f' * 24}, self.sam)[0], 400)
        self.assertEqual([t['id'] for t in self.call('/api/backup', headers=self.sam)[1]['data']['tests']], ['e' * 24])
        built_in = owner['chart']
        params = {key: [[50 + i, 10 * i, -5 * i] for i in range(len(meta['swatches']))] for key, meta in built_in['params'].items()}
        self.assertTrue(self.call('/api/chart', {'params': params, 'photo': JPEG}, self.sam)[1]['boxChart']['custom'])
        self.assertEqual(self.call('/api/state', headers=self.owner)[1]['boxChart'], built_in)
        self.assertEqual(self.call('/api/photo?id=calibration', headers=self.sam, raw=True)[0], 200)
        self.assertEqual(self.call('/api/photo?id=calibration', headers=self.owner, raw=True)[0], 404)
        # A pool shop test and the picture of its printout are no different.
        shop = {'id': '9' * 24, 'manual': {'at': server.now(), 'values': {'fc': 2, 'ph': 7.4, 'ta': 100, 'cya': 40, 'ch': 250}}, 'photo': JPEG}
        self.assertEqual(self.call('/api/lab', shop, self.sam)[1]['settings']['calciumHardness'], 250)
        self.assertEqual((self.call('/api/photo?id=' + '9' * 24, headers=self.sam, raw=True)[0], self.call('/api/photo?id=' + '9' * 24, headers=self.owner, raw=True)[0]), (200, 404))
        owner = self.call('/api/state', headers=self.owner)[1]
        self.assertEqual(([t['id'] for t in owner['tests']], owner['settings']['calciumHardness']), (['f' * 24], None))

    def test_home_assistant_only_ever_sees_the_owners_pool(self):
        self.call('/api/setup', {'volumeLitres': 30000, 'surface': 'vinyl'}, self.sam)
        self.call('/api/settings', {'calciumHardness': 250}, self.sam)
        ha_pool.LATEST.update(waterTempC=31.0); ha_pool.STATUS.update(enabled=True); ha_pool.WAKE.clear()
        try:
            state = self.call('/api/tests', {'readings': reading(fc=0)}, self.sam)[1]
            self.assertFalse(ha_pool.WAKE.is_set())
            self.assertEqual((state['weather']['waterTempC'], state['runtime']['ha']['enabled'], state['runtime']['address']), (None, False, None))
            self.assertEqual(state['current']['balance']['tempC'], 25)   # their own setting, not the owner's sensor
            self.assertIsNone(server.current_analysis(server.get_state()))
            self.assertEqual([entity for entity, _, _ in ha_pool.sensors(server.get_state(), None, [])], ['sensor.pool_test_strips_remaining', 'sensor.pool_reminders'])
            self.call('/api/settings', {'calciumHardness': 250}, self.owner)
            state = self.call('/api/tests', {'readings': reading()}, self.owner)[1]
            self.assertTrue(ha_pool.WAKE.is_set())
            self.assertEqual((state['weather']['waterTempC'], state['current']['balance']['tempC']), (31.0, 31.0))
        finally:
            ha_pool.LATEST.update(waterTempC=None); ha_pool.STATUS.update(enabled=False); ha_pool.WAKE.clear()

    def test_only_the_owner_manages_people(self):
        self.assertEqual(self.call('/api/people', {'action': 'add', 'name': 'Eve', 'username': 'eve', 'password': 'long enough pw'}, self.sam)[0], 403)
        self.assertEqual(self.call('/api/people', {'action': 'remove', 'id': self.sam_id}, self.sam)[0], 403)
        for bad in ({'name': 'A', 'username': 'sam.p', 'password': 'long enough pw'}, {'name': 'A', 'username': 'owner', 'password': 'long enough pw'},
                    {'name': 'A', 'username': 'has space', 'password': 'long enough pw'}, {'name': 'A', 'username': 'ok', 'password': 'short'}, {'name': '', 'username': 'ok', 'password': 'long enough pw'}):
            self.assertEqual(self.call('/api/people', {'action': 'add', **bad}, self.owner)[0], 400, bad)
        for bad in ({'action': 'remove', 'id': server.OWNER}, {'action': 'password', 'id': server.OWNER, 'password': 'long enough pw'}, {'action': 'remove', 'id': 999}, {'action': 'promote', 'id': self.sam_id}):
            self.assertEqual(self.call('/api/people', bad, self.owner)[0], 400, bad)
        self.assertEqual(len(self.call('/api/state', headers=self.owner)[1]['people']), 1)

    def test_switching_access_off_or_setting_a_password_ends_their_session(self):
        self.call('/api/people', {'action': 'disable', 'id': self.sam_id, 'disabled': True}, self.owner)
        self.assertEqual(self.call('/api/state', headers=self.sam)[0], 401)
        self.assertEqual(self.call('/api/login', {'username': 'sam.p', 'password': self.SECRET})[0], 401)
        self.call('/api/people', {'action': 'disable', 'id': self.sam_id, 'disabled': False}, self.owner)
        self.assertEqual(self.call('/api/state', headers=self.sam)[0], 200)
        self.call('/api/people', {'action': 'password', 'id': self.sam_id, 'password': 'tide-kelp-reef'}, self.owner)
        self.assertEqual(self.call('/api/state', headers=self.sam)[0], 401)
        self.assertEqual(self.call('/api/login', {'username': 'sam.p', 'password': self.SECRET})[0], 401)
        self.sign_in('sam.p', 'tide-kelp-reef')

    def test_a_person_can_change_their_own_password_and_sign_out(self):
        self.assertEqual(self.call('/api/password', {'current': 'wrong', 'password': 'another-long-one'}, self.sam)[0], 400)
        self.assertEqual(self.call('/api/password', {'current': self.SECRET, 'password': 'short'}, self.sam)[0], 400)
        self.assertEqual(self.call('/api/password', {'current': 'correct horse battery', 'password': 'another-long-one'}, self.owner)[0], 400)
        status, _, response = self.call('/api/password', {'current': self.SECRET, 'password': 'another-long-one'}, self.sam)
        self.assertEqual(status, 200)
        self.assertEqual(self.call('/api/state', headers=self.sam)[0], 401)   # the old session
        self.assertEqual(self.call('/api/state', headers={'Cookie': response.getheader('Set-Cookie').split(';')[0]})[0], 200)
        self.sign_in('sam.p', 'another-long-one')
        self.assertIn('Max-Age=0', self.call('/api/logout', {})[2].getheader('Set-Cookie'))

    def test_guessing_the_current_password_from_a_session_is_limited_like_signing_in(self):
        for _ in range(10): self.assertEqual(self.call('/api/password', {'current': 'a wrong guess', 'password': 'another-long-one'}, self.sam)[0], 400)
        self.assertEqual(self.call('/api/password', {'current': self.SECRET, 'password': 'another-long-one'}, self.sam)[0], 429)
        self.assertEqual(self.call('/api/state', headers=self.sam)[0], 200)   # still signed in, and the password is unchanged
        server.LOGIN_ATTEMPTS.clear()
        self.assertEqual(self.call('/api/password', {'current': self.SECRET, 'password': 'another-long-one'}, self.sam)[0], 200)

    def test_one_person_signing_out_leaves_everyone_else_signed_in(self):
        self.call('/api/logout', {}, self.sam)
        self.assertEqual((self.call('/api/state', headers=self.sam)[0], self.call('/api/state', headers=self.owner)[0]), (401, 200))

    def test_removing_a_person_deletes_their_data_and_never_reuses_their_place(self):
        self.call('/api/setup', {'volumeLitres': 30000, 'surface': 'vinyl'}, self.sam)
        self.call('/api/tests', {'id': 'e' * 24, 'source': 'photo', 'readings': reading(), 'photo': JPEG}, self.sam)
        folder = server.photo_dir(self.sam_id)
        self.assertTrue((folder / ('e' * 24 + '.jpg')).is_file())
        status, state, _ = self.call('/api/people', {'action': 'remove', 'id': self.sam_id}, self.owner)
        self.assertEqual((status, state['people'], folder.exists()), (200, [], False))
        self.assertEqual(self.call('/api/state', headers=self.sam)[0], 401)
        self.assertRaises(LookupError, server.get_state, self.sam_id)
        again = self.call('/api/people', {'action': 'add', 'name': 'Sam', 'username': 'sam.p', 'password': self.SECRET}, self.owner)[1]
        self.assertGreater(again['people'][0]['id'], self.sam_id)
        self.assertEqual(self.call('/api/state', headers=self.sam)[0], 401)   # the old session does not open the new account


class Upgrade(unittest.TestCase):
    def test_a_database_from_before_people_keeps_everything_as_the_owners(self):
        saved = server.DATA, server.DB, server.PHOTOS, server.SECRET
        with tempfile.TemporaryDirectory(prefix='pool-old-') as folder:
            server.DATA = pathlib.Path(folder); server.DB = server.DATA / 'pool.sqlite3'; server.PHOTOS = server.DATA / 'photos'
            try:
                old = {'schemaVersion': 1, 'settings': copy.deepcopy(chemistry.DEFAULT_SETTINGS), 'tests': [{'id': 'a' * 24, 'at': '2026-11-01T08:00:00+00:00', 'readings': reading()}],
                       'doses': [], 'weather': {'days': {}}, 'reminders': {'sent': {}, 'shown': []}, 'chart': {'calibratedAt': '2026-11-02T08:00:00+00:00', 'params': {}}}
                del old['settings']['surface']
                db = sqlite3.connect(server.DB)
                db.execute('CREATE TABLE state (id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL, payload TEXT NOT NULL)')
                db.execute('INSERT INTO state VALUES (1, 7, ?)', (json.dumps(old),)); db.commit(); db.close()
                server.init()
                state = server.get_state()
                self.assertEqual((state['tests'][0]['id'], state['chart']['calibratedAt'], state['settings']['surface']), ('a' * 24, '2026-11-02T08:00:00+00:00', 'gunite'))
                self.assertNotIn('needsSetup', state)
                self.assertEqual(server.people(), [])
                uid = server.add_person({'name': 'Sam', 'username': 'sam', 'password': 'reef-kelp-tide'})
                self.assertEqual((uid, server.get_state(uid)['tests']), (2, []))
                server.init()   # a restart changes nothing
                self.assertEqual((len(server.get_state()['tests']), len(server.people())), (1, 1))
            finally:
                server.DATA, server.DB, server.PHOTOS, server.SECRET = saved


class Ingress(unittest.TestCase):
    """The Home Assistant sidebar route: HA authenticates, the app trusts only its proxy."""
    @classmethod
    def setUpClass(cls):
        server.init()
        class IngressHandler(server.Handler): ingress_mode = True
        cls.httpd = ThreadingHTTPServer(('127.0.0.1', 0), IngressHandler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown(); cls.httpd.server_close(); server.INGRESS_PEER = '172.30.32.2'

    def call(self, path, body=None, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        send = dict(headers or {})
        if body is not None: send['Content-Type'] = 'application/json'
        conn.request('POST' if body is not None else 'GET', path, json.dumps(body) if body is not None else None, send)
        response = conn.getresponse(); data = response.read(); conn.close()
        return response.status, data

    def test_only_the_supervisor_proxy_is_served(self):
        server.INGRESS_PEER = '172.30.32.2'
        self.assertEqual(self.call('/api/state')[0], 403)
        self.assertEqual(self.call('/')[0], 403)

    def test_pages_are_rebased_and_posts_checked_against_the_forwarded_host(self):
        server.INGRESS_PEER = '127.0.0.1'; server.PASSWORD = 'not used through ingress'
        status, page = self.call('/', headers={'X-Ingress-Path': '/api/hassio_ingress/AbC_123-x'})
        self.assertEqual(status, 200); self.assertIn(b'<base href="/api/hassio_ingress/AbC_123-x/">', page)
        self.assertEqual(self.call('/', headers={'X-Ingress-Path': '/evil"><script>'})[0], 400)
        self.assertEqual(self.call('/api/state')[0], 200)
        good = {'Origin': 'https://ha.example', 'X-Forwarded-Host': 'ha.example'}
        self.assertEqual(self.call('/api/plan', {'readings': reading()}, good)[0], 200)
        self.assertEqual(self.call('/api/plan', {'readings': reading()}, {'Origin': 'https://evil.example', 'X-Forwarded-Host': 'ha.example'})[0], 403)
        server.PASSWORD = ''


class FakeHomeAssistant(BaseHTTPRequestHandler):
    posts, states = [], {'weather.home': {'state': 'pouring', 'attributes': {'temperature': 34.5}}, 'sensor.pool_temp': {'state': '27.5'}}
    def log_message(self, *args): pass
    def reply(self, data):
        raw = json.dumps(data).encode()
        self.send_response(200); self.send_header('Content-Length', str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def do_GET(self):
        self.reply(self.states.get(self.path.split('/states/')[-1], {}))
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.posts.append((self.path, body, self.headers.get('Authorization'))); self.reply({})


class HomeAssistant(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        server.init()
        cls.fake = ThreadingHTTPServer(('127.0.0.1', 0), FakeHomeAssistant)
        threading.Thread(target=cls.fake.serve_forever, daemon=True).start()
        ha_pool.BASE = f'http://127.0.0.1:{cls.fake.server_address[1]}/'
        os.environ['SUPERVISOR_TOKEN'] = 'test-token'
        ha_pool.OPTIONS.update(weather_entity='weather.home', water_temp_entity='sensor.pool_temp', notify_service='notify.mobile_app_phone')
        cls.app = type('App', (), {'get_state': staticmethod(server.get_state), 'save_change': staticmethod(server.save_change), 'current_analysis': staticmethod(server.current_analysis)})

    @classmethod
    def tearDownClass(cls):
        cls.fake.shutdown(); cls.fake.server_close()
        ha_pool.OPTIONS.update(weather_entity='', water_temp_entity='', notify_service=''); ha_pool.LATEST.update(waterTempC=None, airTempC=None, condition=None)

    def setUp(self):
        Api.setUp(self); FakeHomeAssistant.posts.clear()

    def state_with(self, days_ago, **values):
        at = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days_ago)).isoformat()
        return server.add_test({'at': at, 'readings': reading(**values)})

    def test_sensors_carry_readings_status_and_plan(self):
        state = self.state_with(0.01, fc=0.5, ph=7.9)
        analysis = server.current_analysis(state)
        sensors = {entity: (value, attributes) for entity, value, attributes in ha_pool.sensors(state, analysis, [])}
        self.assertEqual(sensors['sensor.pool_free_chlorine'][0], 0.5)
        self.assertEqual(sensors['sensor.pool_free_chlorine'][1]['status'], 'low')
        self.assertEqual(sensors['sensor.pool_free_chlorine'][1]['unit_of_measurement'], 'ppm')
        self.assertEqual((sensors['sensor.pool_free_chlorine'][1]['ideal_low'], sensors['sensor.pool_free_chlorine'][1]['ideal_high']), (2.0, 4.0))
        self.assertNotIn('unit_of_measurement', sensors['sensor.pool_ph'][1])
        self.assertEqual(sensors['sensor.pool_water_status'][0], 'Needs attention')
        self.assertEqual(len(sensors['sensor.pool_water_status'][1]['actions']), 2)
        self.assertEqual(sensors['sensor.pool_last_tested'][1]['device_class'], 'timestamp')
        self.assertNotIn('sensor.pool_water_balance', sensors)

    def test_an_unclear_reading_is_reported_as_such(self):
        r = reading(ph=6.4); r['ph'] = {'value': 6.4, 'confidence': 'poor'}
        state = server.add_test({'readings': r})
        analysis = server.current_analysis(state)
        sensors = {entity: (value, attributes) for entity, value, attributes in ha_pool.sensors(state, analysis, [])}
        self.assertEqual(sensors['sensor.pool_ph'][1]['status'], 'unsure')
        self.assertEqual(sensors['sensor.pool_water_status'][0], 'Unclear reading')
        self.assertEqual(sensors['sensor.pool_water_status'][1]['unclear'], ['ph'])
        view = server.view(state)
        self.assertEqual(view['statuses'][state['tests'][0]['id']]['ph'], 'unsure')
        self.assertEqual((view['current']['steps'], view['current']['unsure']), ([], ['ph']))

    def test_a_cycle_publishes_sensors_reads_weather_and_notifies_once(self):
        self.state_with(9)
        ha_pool.cycle(self.app)
        paths = [path for path, _, _ in FakeHomeAssistant.posts]
        for entity in ('sensor.pool_ph', 'sensor.pool_water_status', 'sensor.pool_next_test_due', 'sensor.pool_reminders'):
            self.assertIn('/states/' + entity, paths)
        self.assertTrue(all(auth == 'Bearer test-token' for _, _, auth in FakeHomeAssistant.posts))
        self.assertIn('/services/persistent_notification/create', paths)
        self.assertEqual(ha_pool.LATEST['waterTempC'], 27.5)
        today = dt.datetime.now(ha_pool.local_zone()).date().isoformat()
        self.assertEqual(server.get_state()['weather']['days'][today]['maxC'], 34.5)
        pushes = paths.count('/services/notify/mobile_app_phone')
        hour = dt.datetime.now(ha_pool.local_zone()).hour
        self.assertEqual(pushes, 1 if 7 <= hour < 20 else 0)
        FakeHomeAssistant.posts.clear(); ha_pool.cycle(self.app)
        self.assertNotIn('/services/notify/mobile_app_phone', [path for path, _, _ in FakeHomeAssistant.posts])
        # A fresh test clears the reminder.
        self.state_with(0); FakeHomeAssistant.posts.clear(); ha_pool.cycle(self.app)
        self.assertIn('/services/persistent_notification/dismiss', [path for path, _, _ in FakeHomeAssistant.posts])

    def test_reminders(self):
        now = dt.datetime.now(dt.timezone.utc)
        keys = lambda state: [n['key'] for n in ha_pool.nudges(state, server.current_analysis(state), now)]
        self.assertEqual(keys(server.get_state()), [])
        self.assertEqual(keys(self.state_with(8)), ['overdue'])
        Api.setUp(self); self.assertEqual(keys(self.state_with(1.2, fc=0)), ['retest'])
        Api.setUp(self); self.assertEqual(keys(self.state_with(0.5, fc=0)), [])
        Api.setUp(self); self.state_with(4)
        zone = ha_pool.local_zone()
        def weather(state):
            for back in (1, 2, 3): state['weather']['days'][(now.astimezone(zone).date() - dt.timedelta(days=back)).isoformat()] = {'maxC': 35, 'rainHours': 2}
            return state
        self.assertEqual(keys(server.save_change(weather)), ['heat', 'rain'])
        def strips(state):
            state['settings']['strips'] = {'remaining': 2, 'expiry': (now - dt.timedelta(days=40)).strftime('%Y-%m')}
            return state
        self.assertEqual(keys(server.save_change(strips))[-2:], ['strips-low', 'strips-expired'])


if __name__ == '__main__':
    unittest.main()
