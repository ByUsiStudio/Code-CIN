import os
import sys
import tempfile

sys.path.insert(0, '.')
from codecin.cin import _resolve_import

root = tempfile.mkdtemp()
pkg = os.path.join(root, 'rich')
os.makedirs(os.path.join(pkg, 'src', 'internal'))
open(os.path.join(pkg, 'pic.toml'), 'w').write(
    '[package]\nname="rich"\nversion="1.1.1"\nentry="src/rich.cin"\n')
open(os.path.join(pkg, 'src', 'rich.cin'), 'w').write(
    'import "rich:table"\nfunction rich_main() -> int { return 1 }\n')
open(os.path.join(pkg, 'src', 'extra.cin'), 'w').write(
    'function extra() -> int { return 2 }\n')
open(os.path.join(pkg, 'src', 'internal', 'table.cin'), 'w').write(
    'function table() -> int { return 3 }\n')
open(os.path.join(pkg, 'src', 'main.cin'), 'w').write(
    'function main() -> int { return 0 }\n')
print(_resolve_import('rich:*', root))
print(_resolve_import('rich:table', root))
print(_resolve_import('rich::1.1.1:table', root))
try:
    _resolve_import('rich::2.0.0:*', root)
except Exception as e:
    print(type(e).__name__, e)
