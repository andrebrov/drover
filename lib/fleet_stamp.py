"""Stamped measurements (fleet regression, after harness2 core/runmark.py): a result counts only if its writer finished and says so.

A file that is merely present proves nothing: fleet-measure republished a frozen 10-13 count for five days; the main tripwire printed
UNAVAILABLE for an hour under load; a seat label outlived the state it described. stamp() writes the payload with
{tool, tool_sha256, inputs_fingerprint, computed_at, complete: true} in ONE atomic rename, the marker being part of the last write.
read_fresh() hands the payload back only when the marker is there, the age is within max_age, and the tool file is the one that wrote
it; otherwise it returns Unavailable(reason), which a consumer prints as UNAVAILABLE(reason), never as the previous value or 0.

Reasons: missing_marker (no file, unreadable, or not a stamped file), incomplete (marker present but not complete),
stale (older than max_age, or timestamped in the future), other_tool_version (the tool file changed since).
"""
import hashlib
import json
import os
import tempfile
import time
from pathlib import Path


class Unavailable:
    def __init__(self, reason):
        self.reason = reason

    def __bool__(self):
        return False

    def __str__(self):
        return f'UNAVAILABLE({self.reason})'

    def __repr__(self):
        return f'Unavailable({self.reason!r})'


def tool_sha256(tool_path):
    return hashlib.sha256(Path(tool_path).read_bytes()).hexdigest()


def fingerprint(**inputs):
    """Stable digest of what the measurement was computed from (git tip, window, state-file mtimes...)."""
    return hashlib.sha256(json.dumps(inputs, sort_keys=True, default=str).encode()).hexdigest()[:16]


def stamp(path, tool_path, payload, inputs=None, now=None):
    """Write payload + marker atomically. Call it as the LAST step of the writer: a writer killed earlier leaves no marker."""
    path = Path(path)
    doc = {'stamp': {'tool': Path(tool_path).name, 'tool_sha256': tool_sha256(tool_path),
                     'inputs_fingerprint': fingerprint(**(inputs or {})),
                     'computed_at': time.time() if now is None else now, 'complete': True},
           'payload': payload}
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', dir=path.parent, prefix=f'.{path.name}.', delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(json.dumps(doc))
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return doc['stamp']


def read_fresh(path, max_age, tool_path, now=None, inputs=None):
    """The payload, or Unavailable(reason). With `inputs`, a result computed from different inputs is other_inputs."""
    try:
        doc = json.loads(Path(path).read_text())
        mark = doc['stamp']
        payload = doc['payload']
    except (OSError, ValueError, KeyError, TypeError):
        return Unavailable('missing_marker')
    if not isinstance(mark, dict) or 'computed_at' not in mark:
        return Unavailable('missing_marker')
    if mark.get('complete') is not True:
        return Unavailable('incomplete')
    try:
        age = (time.time() if now is None else now) - float(mark['computed_at'])
    except (TypeError, ValueError):
        return Unavailable('missing_marker')
    if age > max_age or age < 0:
        return Unavailable('stale')
    if mark.get('tool_sha256') != tool_sha256(tool_path):
        return Unavailable('other_tool_version')
    if inputs is not None and mark.get('inputs_fingerprint') != fingerprint(**inputs):
        return Unavailable('other_inputs')
    return payload


def _main(argv):
    """`fleet_stamp.py read PATH MAX_AGE_SECONDS TOOL_PATH [FIELD...]`: print `field: value` lines, or UNAVAILABLE(reason) and exit 1."""
    if len(argv) < 5 or argv[1] != 'read':
        print('usage: fleet_stamp.py read PATH MAX_AGE_SECONDS TOOL_PATH [FIELD...]')
        return 2
    try:
        result = read_fresh(argv[2], float(argv[3]), argv[4])
    except OSError:                       # the tool file itself is unreadable: nothing can be certified
        result = Unavailable('other_tool_version')
    if not result:
        print(result)
        return 1
    fields = argv[5:] or (sorted(result) if isinstance(result, dict) else [])
    for field in fields:
        print(f'{field}: {result.get(field, "UNAVAILABLE(missing_field)") if isinstance(result, dict) else result}')
    return 0


if __name__ == '__main__':
    import sys
    sys.exit(_main(sys.argv))
