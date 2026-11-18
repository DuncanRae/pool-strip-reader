"""Local preview on http://localhost:8745 with a throwaway database.

Nothing here touches the Home Assistant installation. Pass --demo to start
with a few weeks of invented tests so every screen has something to show.
Pass --login to switch sign-in on, as it is on the standalone address: the
owner and one invented person, with the preview-only passwords below.
"""
import datetime as dt, os, pathlib, sys, tempfile
root = pathlib.Path(__file__).resolve().parent.parent
data = pathlib.Path(os.getenv('POOL_DATA_DIR') or tempfile.mkdtemp(prefix='pool-preview-'))
os.environ.update(POOL_DATA_DIR=str(data), HOST='127.0.0.1', PORT='8745')
sys.path.insert(0, str(root)); os.chdir(root)
OWNER_LOGIN, PERSON_LOGIN = ('owner', 'preview-owner-password'), ('sam', 'preview-sam-password')
if '--login' in sys.argv: os.environ.update(APP_USERNAME=OWNER_LOGIN[0], APP_PASSWORD=OWNER_LOGIN[1])
import server

if '--demo' in sys.argv:
    server.init()
    if not server.get_state()['tests']:
        now = dt.datetime.now(dt.timezone.utc)
        rows = [(21, 2, 100, 7.4, 30), (14, 1, 100, 7.6, 30), (9, 3, 90, 7.5, 40), (4, 2, 90, 7.7, 40), (0.1, 0.5, 70, 7.9, 20)]
        for days, fc, ta, ph, cya in rows:
            server.add_test({'at': (now - dt.timedelta(days=days)).isoformat(), 'source': 'manual',
                'readings': {'fc': {'value': fc}, 'ta': {'value': ta}, 'ph': {'value': ph}, 'cya': {'value': cya}}})
if '--login' in sys.argv:
    server.init()
    if not server.people(): server.add_person({'name': 'Sam', 'username': PERSON_LOGIN[0], 'password': PERSON_LOGIN[1]})
print('Preview data folder:', data, flush=True)
server.main()
