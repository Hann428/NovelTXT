"""Fetch Microsoft's pinned SDK to the workspace; verify NuGet's SHA512."""
import base64
import hashlib
import gzip
import json
import urllib.request
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = '1.0.3912.50'
BASE = f'https://api.nuget.org/v3-flatcontainer/microsoft.web.webview2/{VERSION}/microsoft.web.webview2.{VERSION}.nupkg'
dest = ROOT / 'vendor' / 'WebView2SDK'
dest.mkdir(parents=True, exist_ok=True)
with urllib.request.urlopen(BASE, timeout=30) as response:
    body = response.read(30000000)
metadata_url = f"https://www.nuget.org/api/v2/Packages(Id=%27Microsoft.Web.WebView2%27,Version=%27{VERSION}%27)"
with urllib.request.urlopen(metadata_url, timeout=15) as response:
    metadata_body = response.read()
    if metadata_body.startswith(b'\x1f\x8b'):
        metadata_body = gzip.decompress(metadata_body)
    metadata = ET.fromstring(metadata_body)
expected = next(node.text for node in metadata.iter() if node.tag.endswith('}PackageHash'))
algorithm = next(node.text for node in metadata.iter() if node.tag.endswith('}PackageHashAlgorithm'))
if algorithm != 'SHA512':
    raise RuntimeError('Unexpected package hash algorithm')
actual = base64.b64encode(hashlib.sha512(body).digest()).decode()
if actual != expected:
    raise RuntimeError('NuGet SHA512 mismatch')
archive = dest / f'microsoft.web.webview2.{VERSION}.nupkg'
archive.write_bytes(body)
with zipfile.ZipFile(archive) as package:
    wanted = [name for name in package.namelist() if not name.endswith('/') and (
              name.startswith('lib/net462/') or name.startswith('runtimes/win-x64/native/') or
              name.lower().endswith(('license.txt', '.nuspec')))]
    for name in wanted:
        target = dest / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(package.read(name))
(dest / 'provenance.json').write_text(json.dumps({'version': VERSION, 'source': BASE, 'sha512': actual,
                                               'sha256': hashlib.sha256(body).hexdigest(), 'files': wanted}, indent=2), encoding='utf-8')
print(json.dumps({'version': VERSION, 'bytes': len(body), 'files': wanted}))
