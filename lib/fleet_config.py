"""Share drover-env with Python tools without changing an importing process's environment."""
import os
from pathlib import Path
import subprocess


class Config(dict):
    """Explicit runtime overrides win; import-time defaults remain local to this tool."""
    def __getitem__(self, key):
        if key in os.environ:
            return os.environ[key]
        return super().__getitem__(key)

    def get(self, key, default=None):
        return os.environ.get(key, super().get(key, default))


def configure(export=False):
    source = Path(__file__).resolve().parents[1] / 'bin/drover-env'
    result = subprocess.run(['bash', '-c', 'set -a; . "$1"; env -0', 'drover-config', str(source)],
                            check=True, capture_output=True)
    config = Config()
    for entry in result.stdout.split(b'\0'):
        key, sep, value = entry.partition(b'=')
        if sep and (key.startswith(b'DROVER_') or key.startswith(b'FLEET_')):
            config[os.fsdecode(key)] = os.fsdecode(value)
    # A real CLI exports its config to children. A probe importing its helpers
    # has no authority to reconfigure the caller or its later test fixtures.
    if export:
        for key,value in config.items():
            os.environ.setdefault(key,value)
    return config
