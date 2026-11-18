"""Build the Home Assistant app folder and archive from the canonical source.

Output: ../dist/pool_strip_reader and ../dist/pool-strip-reader-ha-app.tar.gz.
Copy the folder to the HA `addons` share; never copy anything into `/data`.
"""
import pathlib, shutil, tarfile

root = pathlib.Path(__file__).resolve().parent.parent
out = root.parent / 'dist' / 'pool_strip_reader'
if out.exists(): shutil.rmtree(out)
out.mkdir(parents=True)
for name in ('server.py', 'chemistry.py', 'ha_pool.py', 'tls_manager.py', 'chart.json'):
    shutil.copy2(root / name, out / name)
for name in ('config.yaml', 'Dockerfile', 'ha_run.py', 'DOCS.md', 'CHANGELOG.md'):
    shutil.copy2(root / 'ha-app' / name, out / name)
shutil.copy2(root / 'ha-app' / 'DOCS.md', out / 'README.md')
shutil.copytree(root / 'public', out / 'public', ignore=shutil.ignore_patterns('.DS_Store', '_*'))
shutil.copy2(root / 'public' / 'icon-192.png', out / 'icon.png')
shutil.copy2(root / 'public' / 'icon-512.png', out / 'logo.png')
(out / '.dockerignore').write_text('data\n.env\n__pycache__\n.DS_Store\n')
archive = out.parent / 'pool-strip-reader-ha-app.tar.gz'
with tarfile.open(archive, 'w:gz') as tar: tar.add(out, arcname='pool_strip_reader')
print(f'HA app folder: {out}\nInstallation archive: {archive}')
