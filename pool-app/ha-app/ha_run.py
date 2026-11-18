"""Translate HA app options; secrets never enter browser HTML or logs."""
import json, os, pathlib
from tls_manager import hostname_for

options_file = pathlib.Path('/data/options.json')
options = json.loads(options_file.read_text()) if options_file.exists() else {}
os.environ.update(HOST='0.0.0.0', PORT='8745', POOL_DATA_DIR='/data', POOL_INGRESS='1', POOL_HA_ENABLED='1')
if options.get('standalone_enabled'):
    password = options.get('standalone_password', '')
    url = options.get('standalone_url', '').rstrip('/')
    if len(password) < 12:
        raise SystemExit('Standalone mode needs a password of at least 12 characters.')
    hostname_for(url)
    os.environ.update(POOL_DIRECT='1', APP_PASSWORD=password, APP_ORIGIN=url, APP_USERNAME=str(options.get('standalone_username') or 'owner'))
import server
server.main(options)
