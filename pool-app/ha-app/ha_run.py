"""Start the app: Home Assistant mode under the Supervisor, plain Docker mode anywhere else.

Under Home Assistant this translates the app options; secrets never enter browser HTML or logs.
"""
import json, os, pathlib
from tls_manager import hostname_for

if not os.getenv('SUPERVISOR_TOKEN'):
    # Plain Docker (no Supervisor). Ingress mode is never switched on here: it trusts the ingress
    # proxy's address as the owner without a password. server.main() refuses to start without
    # APP_PASSWORD (12+ characters) and APP_ORIGIN.
    os.environ.pop('POOL_INGRESS', None); os.environ.pop('POOL_DIRECT', None); os.environ.pop('POOL_HA_ENABLED', None)
    import server
    server.main()
    raise SystemExit(0)

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
