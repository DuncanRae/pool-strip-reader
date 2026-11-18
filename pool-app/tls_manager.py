"""Optional direct HTTPS and Cloudflare DNS-01 certificates, inside this app.

Certbot is an Alpine package. Only its fixed DNS plugin is invoked; secrets are
passed in a private credentials file, never in argv, URLs or app status.
"""
import datetime as dt
import os
import pathlib
import re
import ssl
import subprocess
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from urllib.parse import urlsplit

STATUS = {'enabled': False, 'state': 'disabled'}

def status():
    return dict(STATUS)

def hostname_for(url):
    parsed = urlsplit(url)
    host = parsed.hostname or ''
    if (parsed.scheme != 'https' or parsed.username or parsed.password or
        parsed.path not in ('', '/') or parsed.query or parsed.fragment or
        not re.fullmatch(r'(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}', host)):
        raise ValueError('Use an HTTPS origin with a DNS hostname, for example https://pool.example.com.')
    if parsed.port is not None and not 1 <= parsed.port <= 65535:
        raise ValueError('Invalid HTTPS port.')
    return host

def certificate_context(cert, key, hostname):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(str(cert), str(key))
    info = ssl._ssl._test_decode_cert(str(cert))
    if hostname not in [value.lower() for kind, value in info.get('subjectAltName', ()) if kind == 'DNS']:
        raise ValueError('Certificate does not cover the configured hostname.')
    expires = ssl.cert_time_to_seconds(info['notAfter'])
    if expires <= time.time() or ssl.cert_time_to_seconds(info['notBefore']) > time.time():
        raise ValueError('Certificate is not currently valid.')
    return context, dt.datetime.fromtimestamp(expires, dt.timezone.utc).isoformat()

class TLSHTTPServer(ThreadingHTTPServer):
    """Swap complete validated contexts; a failed renewal cannot replace one."""
    daemon_threads = True
    def __init__(self, address, handler, context):
        self.context = context
        super().__init__(address, handler)
    def get_request(self):
        raw, address = super().get_request()
        raw.settimeout(15)
        try:
            # Handshake occurs in the request thread, not in the accept loop.
            return self.context.wrap_socket(raw, server_side=True, do_handshake_on_connect=False), address
        except Exception:
            raw.close()
            raise

class CertificateManager:
    def __init__(self, options, data, activate):
        self.options = options
        self.hostname = hostname_for(options['standalone_url'])
        self.root = pathlib.Path(data) / 'tls' / self.hostname
        self.activate = activate
        self.mode = options.get('certificate_mode', 'files')
        self.direct = None
        self.fingerprint = None
        self.stop = threading.Event()
        if self.mode not in ('files', 'cloudflare'):
            raise ValueError('Unknown certificate mode.')
        if self.mode == 'cloudflare':
            token = options.get('cloudflare_api_token', '').strip()
            if not re.fullmatch(r'[A-Za-z0-9_-]{20,256}', token):
                raise ValueError('Enter a Cloudflare DNS API token in the app configuration.')
            if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', options.get('letsencrypt_email', '')):
                raise ValueError('Enter a contact email for Let’s Encrypt.')
            if options.get('letsencrypt_terms_accepted') is not True:
                raise ValueError('Review and accept the Let’s Encrypt subscriber agreement before issuance.')
            self.cert = self.root / 'live' / 'pool' / 'fullchain.pem'
            self.key = self.root / 'live' / 'pool' / 'privkey.pem'
        else:
            self.cert = self.ssl_file(options.get('certfile', 'fullchain.pem'))
            self.key = self.ssl_file(options.get('keyfile', 'privkey.pem'))
        STATUS.clear()
        STATUS.update(enabled=True, state='waiting', mode=self.mode, hostname=self.hostname)
    @staticmethod
    def ssl_file(name):
        path = (pathlib.Path('/ssl') / name).resolve()
        if not path.is_relative_to('/ssl'):
            raise ValueError('Certificate files must be inside /ssl.')
        return path
    def load(self):
        try:
            context, expires = certificate_context(self.cert, self.key, self.hostname)
            # Validate first, then start or replace only the direct listener.
            fingerprint = (self.cert.read_bytes(), self.key.read_bytes())
            if fingerprint != self.fingerprint:
                self.activate(context)
                self.fingerprint = fingerprint
            STATUS.update(state='ready', expiresAt=expires)
            return True
        except (OSError, ValueError, ssl.SSLError):
            STATUS.update(state='certificate_unavailable')
            return False
    def issue(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        # Certbot renewal settings retain this path; recreate it for each run.
        credentials = self.root / 'cloudflare.ini'
        fd = os.open(credentials, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w') as stream:
            stream.write('dns_cloudflare_api_token = ' + self.options['cloudflare_api_token'].strip() + '\n')
        try:
            with tempfile.TemporaryDirectory(prefix='pool-acme-') as temp:
                command = ['certbot', 'certonly', '--non-interactive', '--agree-tos',
                    '--email', self.options['letsencrypt_email'], '--dns-cloudflare',
                    '--dns-cloudflare-credentials', str(credentials),
                    '--dns-cloudflare-propagation-seconds', '60', '--cert-name', 'pool',
                    '--domain', self.hostname, '--keep-until-expiring',
                    '--config-dir', str(self.root), '--work-dir', temp + '/work',
                    '--logs-dir', temp + '/logs', '--quiet']
                # Do not expose DNS responses or credentials in HA logs.
                result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=600, check=False, env={'PATH': '/usr/bin:/bin:/usr/local/bin', 'HOME': temp})
                return result.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False
        finally:
            credentials.unlink(missing_ok=True)
    def cycle(self):
        had_certificate = self.load()
        if self.mode == 'files':
            return 300
        STATUS.update(lastAttempt=dt.datetime.now(dt.timezone.utc).isoformat())
        if self.issue() and self.load():
            STATUS.update(renewal='ok')
            print('Pool HTTPS: certificate ready; automatic renewal checks enabled.', flush=True)
            return 12 * 3600
        STATUS.update(renewal='failed')
        if had_certificate:
            STATUS.update(state='ready')
        print('Pool HTTPS: certificate request failed; retry in one hour. Check Cloudflare token, email and network. HA sidebar remains available.', flush=True)
        return 3600
    def run(self):
        while not self.stop.is_set():
            try:
                delay = self.cycle()
            except Exception:
                # Never print raw exceptions which may contain provider secrets.
                STATUS.update(renewal='failed')
                print('Pool HTTPS: certificate setup failed; retry in one hour.', flush=True)
                delay = 3600
            self.stop.wait(delay)
    def start(self):
        threading.Thread(target=self.run, name='pool-certificates', daemon=True).start()
