#!/usr/bin/env bash
# Run the whole public suite. No live agents, database, network service or provider key is needed.
set -euo pipefail
cd "$(dirname "$0")/.."
for test in tests/*.test.sh; do
  bash "$test"
done
node bin/fleet-check-inline-jest-fanout --self-test
python3 -m pytest -q tests
python3 - <<'PY'
import ast
from pathlib import Path
import subprocess
count = 0
for path in [*Path('bin').iterdir(), *Path('lib').iterdir(), Path('install.sh')]:
    if not path.is_file(): continue
    text = path.read_text()
    if text.startswith('#!/usr/bin/env bash') or path.suffix == '.sh':
        subprocess.run(['bash','-n',str(path)],check=True)
        count += 1
    elif text.startswith('#!/usr/bin/env python') or path.suffix == '.py':
        ast.parse(text,filename=str(path))
        count += 1
print(f'BUILD: {count} shell/Python syntax checks passed')
PY
