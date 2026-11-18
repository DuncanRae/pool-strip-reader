"""Build the Home Assistant app folder (with its Dockerfile) from the canonical source.

Output: ../dist/pool_strip_reader and ../dist/pool-strip-reader-ha-app.tar.gz.

The store metadata (config.yaml, DOCS.md, CHANGELOG.md, icon, logo) lives in the repository's
top-level pool_strip_reader/ folder, which is what a Home Assistant app store reads. Its
config.yaml names the prebuilt image; the copy here drops that line so this folder builds from
source. Two uses:
- a local install: copy the folder to the HA `addons` share (never copy anything into `/data`);
- the release workflow builds the published images from this folder.

Stops if the version differs between server.py, config.yaml, the Dockerfile and package.json.
"""
import json, pathlib, re, shutil, sys, tarfile

root = pathlib.Path(__file__).resolve().parent.parent      # pool-app/
store = root.parent / 'pool_strip_reader'                   # the store's app folder
out = root.parent / 'dist' / 'pool_strip_reader'

config = (store / 'config.yaml').read_text()
versions = {
    'pool_strip_reader/config.yaml': re.search(r'^version:\s*"([^"]+)"', config, re.M),
    'pool-app/server.py': re.search(r"^APP_VERSION = '([^']+)'", (root / 'server.py').read_text(), re.M),
    'pool-app/ha-app/Dockerfile': re.search(r'^ARG BUILD_VERSION=(\S+)', (root / 'ha-app' / 'Dockerfile').read_text(), re.M),
}
versions = {k: v.group(1) if v else None for k, v in versions.items()}
versions['pool-app/package.json'] = json.loads((root / 'package.json').read_text()).get('version')
if len(set(versions.values())) != 1 or None in versions.values():
    sys.exit('Versions differ: ' + ', '.join(f'{k} {v}' for k, v in versions.items()))
version = versions['pool-app/server.py']

if out.exists(): shutil.rmtree(out)
out.mkdir(parents=True)
for name in ('server.py', 'chemistry.py', 'ha_pool.py', 'tls_manager.py', 'chart.json'):
    shutil.copy2(root / name, out / name)
for name in ('Dockerfile', 'ha_run.py'):
    shutil.copy2(root / 'ha-app' / name, out / name)
for name in ('DOCS.md', 'CHANGELOG.md', 'icon.png', 'logo.png'):
    shutil.copy2(store / name, out / name)
shutil.copy2(store / 'DOCS.md', out / 'README.md')
(out / 'config.yaml').write_text(re.sub(r'^image:.*\n', '', config, flags=re.M))
shutil.copytree(root / 'public', out / 'public', ignore=shutil.ignore_patterns('.DS_Store', '_*'))
(out / '.dockerignore').write_text('data\n.env\n__pycache__\n.DS_Store\n')
archive = out.parent / 'pool-strip-reader-ha-app.tar.gz'
with tarfile.open(archive, 'w:gz') as tar: tar.add(out, arcname='pool_strip_reader')
print(f'Version {version}\nHA app folder: {out}\nInstallation archive: {archive}')
