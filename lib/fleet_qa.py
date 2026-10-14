#!/usr/bin/env python3
"""Seat questions and lead decisions as files, not TUI keystrokes.

  fleet-ask <seat> <bean> "<question>" [option ...]   write questions/<id>-a.json, print <id>, the seat ends its turn
  fleet-ask --open                                    one line per unanswered question with its age (fleet status calls this)
  fleet-answer <id> <choice|text> [--rationale R] [--by WHO]
                                                      write decisions/<id>-a.json ONCE, append it to the bean, wake the seat
  fleet-answer --wake <id>                            re-send the wake for an already recorded decision (never rewrites it)

A decision is single-fire: it is created with O_EXCL, so a second answer is refused whatever its content. Nothing here types into a pane;
the seat is woken with `herdr agent prompt` (HERDR_BIN overrides the binary, for tests).
"""
import glob
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone

ROOT = os.environ.get('FLEET_QA_DIR') or os.environ.get('DROVER_HOME') or os.path.expanduser('~/.local/share/drover')
QDIR = os.path.join(ROOT, 'questions')
DDIR = os.path.join(ROOT, 'decisions')
BEANS = os.environ.get('FLEET_BEANS_DIR') or os.path.join(os.environ.get('DROVER_REPO', '.'), '.beans')
HERDR = os.environ.get('HERDR_BIN', 'herdr')
BEAN_RE = re.compile('^' + (os.environ.get('DROVER_BEAN_RE') or r'[a-z0-9]+-[a-z0-9]{4}') + '$')


def now() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def die(code: int, msg: str):
    print(f'fleet-qa: {msg}', file=sys.stderr)
    sys.exit(code)


def write_new(path: str, doc: dict) -> bool:
    """Create `path` atomically and only if absent. False means somebody already did."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        return False
    with os.fdopen(fd, 'w') as f:
        json.dump(doc, f, indent=2)
        f.write('\n')
    return True


def ask(args):
    if len(args) < 3:
        die(2, 'usage: fleet-ask <seat> <bean> "<question>" [option ...]')
    seat, bean, question, *options = args
    if not BEAN_RE.match(bean):
        die(2, f'not a bean id: {bean}')
    if not re.match(r'^[A-Za-z0-9_.-]+$', seat):
        die(2, f'not a seat name: {seat}')
    if not question.strip():
        die(2, 'empty question')
    qid = f"{bean}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-{seat}"
    doc = {'id': qid, 'bean': bean, 'seat': seat, 'question': question, 'options': options, 'asked_at': now()}
    if not write_new(os.path.join(QDIR, f'{qid}-a.json'), doc):
        die(3, f'question {qid} already exists (one per seat per second)')
    print(qid)


def load(path: str):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def age(asked_at: str) -> str:
    try:
        secs = int(time.time() - datetime.strptime(asked_at, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc).timestamp())
    except ValueError:
        return '?'
    return f'{secs // 3600}h{secs % 3600 // 60:02d}m' if secs >= 3600 else f'{secs // 60}m{secs % 60:02d}s'


def open_questions():
    out = []
    for path in sorted(glob.glob(os.path.join(QDIR, '*-a.json'))):
        q = load(path)
        if not q or not q.get('id'):
            continue
        if os.path.exists(os.path.join(DDIR, f"{q['id']}-a.json")):
            continue
        out.append(q)
    return out


def list_open():
    for q in open_questions():
        opts = ' | '.join(f'{i + 1}) {o}' for i, o in enumerate(q.get('options') or []))
        print(f"open question {q['id']}  seat={q['seat']} bean={q['bean']} age={age(q.get('asked_at', ''))}  {q['question'][:120]}{'  [' + opts + ']' if opts else ''}")


def wake(q: dict, d: dict) -> int:
    msg = (f"Decision for your question {q['id']} ({q['bean']}): {d['decision']}"
           + (f" — {d['rationale']}" if d.get('rationale') else '')
           + f" (by {d['by']}). Continue the bean; do not ask again.")
    try:
        r = subprocess.run([HERDR, 'agent', 'prompt', q['seat'], msg], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return 1
    return r.returncode


def append_to_bean(q: dict, d: dict) -> bool:
    files = glob.glob(os.path.join(BEANS, f"{q['bean']}--*.md"))
    if not files:
        return False
    with open(files[0], 'a') as f:
        f.write(f"\n## Decision {d['at']} ({q['id']})\n\nQ ({q['seat']}): {q['question']}\n\nA ({d['by']}): {d['decision']}"
                + (f"\n\nWhy: {d['rationale']}" if d.get('rationale') else '') + '\n')
    return True


def answer(args):
    rewake = False
    if args and args[0] == '--wake':
        rewake, args = True, args[1:]
    rationale, by, rest = '', os.environ.get('FLEET_ANSWER_BY', 'lead'), []
    it = iter(args)
    for a in it:
        if a == '--rationale':
            rationale = next(it, '')
        elif a == '--by':
            by = next(it, by)
        else:
            rest.append(a)
    if not rest or (not rewake and len(rest) < 2):
        die(2, 'usage: fleet-answer <id> <choice|text> [--rationale R] [--by WHO] | fleet-answer --wake <id>')
    qid = rest[0]
    q = load(os.path.join(QDIR, f'{qid}-a.json'))
    if not q:
        die(2, f'no such question: {qid}')
    dpath = os.path.join(DDIR, f'{qid}-a.json')
    if rewake:
        d = load(dpath)
        if not d:
            die(2, f'{qid} has no decision to re-send')
        sys.exit(0 if wake(q, d) == 0 else 4)
    text = ' '.join(rest[1:]).strip()
    opts = q.get('options') or []
    if text.isdigit() and 1 <= int(text) <= len(opts):
        text = opts[int(text) - 1]
    if not text:
        die(2, 'empty decision')
    if opts and text not in opts:
        print(f'fleet-qa: note: "{text}" is not one of the offered options; recorded as free text', file=sys.stderr)
    d = {'id': qid, 'decision': text, 'rationale': rationale, 'by': by, 'at': now()}
    if not write_new(dpath, d):
        die(3, f'{qid} is already answered; a second answer is refused')
    if not append_to_bean(q, d):
        print(f"fleet-qa: warning: no bean file for {q['bean']} under {BEANS}; decision recorded only in {dpath}", file=sys.stderr)
    if wake(q, d) != 0:
        die(4, f"decision recorded ({dpath}) but waking {q['seat']} failed; run: fleet-answer --wake {qid}")
    print(f"answered {qid}: {text}")


def main():
    prog = os.environ.get('FLEET_QA_PROG', '')
    args = sys.argv[1:]
    if prog.endswith('fleet-ask'):
        list_open() if args == ['--open'] else ask(args)
    elif prog.endswith('fleet-answer'):
        answer(args)
    else:
        die(2, 'run as fleet-ask or fleet-answer')


if __name__ == '__main__':
    main()
