#!/usr/bin/env python3
"""bean-ne0q — fleet-stale-jobs maps production stale jobs to open beans and fails on an unowned one.

Run: python3 bundle/tests/test_fleet_stale_jobs.py   (pytest also collects it)
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.normpath(os.path.join(HERE, '..', 'bin', 'fleet-stale-jobs'))
NOW = '2026-10-18T20:00:00Z'


def line(job, started):
    return ('[Error Tracking] {"message":"job \\"%s\\" has not completed a run started since %s — its next window was due '
            'later; staleness exceeds its every_24h schedule plus grace. Its output is presumed stopped.","stack":"Error"}' % (job, started))


def run(lines, beans, *extra):
    with tempfile.TemporaryDirectory() as tmp:
        log, bj = os.path.join(tmp, 'log.txt'), os.path.join(tmp, 'beans.json')
        open(log, 'w').write('\n'.join(lines) + '\n')
        json.dump(beans, open(bj, 'w'))
        p = subprocess.run([sys.executable, SCRIPT, '--log-file', log, '--beans-json', bj, '--now', NOW, *extra],
                           capture_output=True, text=True, env=dict(os.environ,DROVER_JOB_SWEEP_BEANS='bean-ne0q'))
    return p.returncode, p.stdout


def bean(id, title, status='in-progress', body=''):
    return {'id': id, 'title': title, 'status': status, 'body': body}


def test_owned_stale_job_passes():
    rc, out = run([line('sample-daily-job', '2026-10-11T13:58:07.152Z')], [bean('bean-y7kw', 'SAMPLE sample-daily-job misses its window')])
    assert rc == 0 and 'sample-daily-job: stale 7.3 d — bean-y7kw' in out, (rc, out)


def test_unowned_stale_job_fails():
    rc, out = run([line('esp-campaign-sync-daily', '2026-10-15T00:00:00.000Z')], [bean('bean-aaaa', 'something else entirely')])
    assert rc == 1 and 'esp-campaign-sync-daily: stale 3.8 d — UNOWNED' in out, (rc, out)


def test_no_stale_jobs_passes():
    assert run([], [bean('bean-aaaa', 'x')]) == (0, '- none\n')
    # a job whose last run is under 24 h old is not stale
    assert run([line('sample-daily-job', '2026-10-18T10:00:00.000Z')], [])[0] == 0


def test_closed_bean_and_the_standup_bean_do_not_own():
    lines = [line('email-creative-capture', '2026-10-10T00:00:00.000Z')]
    rc, out = run(lines, [bean('bean-done', 'email-creative-capture fixed', status='completed'),
                          bean('bean-ne0q', 'sweep', body='lists email-creative-capture')])
    assert rc == 1 and 'UNOWNED' in out, (rc, out)


def test_owner_match_is_a_whole_token_and_reads_the_body():
    lines = [line('sample-daily-job', '2026-10-11T00:00:00.000Z')]
    assert run(lines, [bean('bean-x', 'sample-daily-job-v2 rewrite')])[0] == 1   # a different job id is not the job
    assert run(lines, [bean('bean-y', 'SAMPLE drafts', body='stale: `sample-daily-job`')])[0] == 0


def test_newest_report_per_job_wins():
    # the same job reported twice: the later "started since" is its age
    rc, out = run([line('sample-daily-job', '2026-10-11T00:00:00Z'), line('sample-daily-job', '2026-10-17T00:00:00Z')],
                  [bean('bean-y', 'sample-daily-job')])
    assert 'stale 1.8 d' in out, out


def _bean_file(dirpath, id, title, status='in-progress'):
    os.makedirs(dirpath, exist_ok=True)
    open(os.path.join(dirpath, f'{id}--x.md'), 'w').write(
        f"---\n# {id}\ntitle: '{title}'\nstatus: {status}\ntype: bug\ncreated_at: 2026-10-18T00:00:00Z\nupdated_at: 2026-10-18T00:00:00Z\n---\n\nbody\n")


def test_owners_come_from_origin_main_not_the_cwd():
    # bean-ne0q follow-up: the lead runs the standup from a worktree whose .beans are days old. The bean that names the job exists
    # only on origin/main; the cwd holds a stale tree without it. The job must be reported owned.
    def git(cwd, *a):
        return subprocess.run(['git', '-C', cwd, *a], capture_output=True, text=True, check=True).stdout
    with tempfile.TemporaryDirectory() as tmp:
        origin, work, stale = (os.path.join(tmp, n) for n in ('origin.git', 'work', 'stale'))
        subprocess.run(['git', 'init', '-q', '--bare', '-b', 'main', origin], check=True)
        subprocess.run(['git', 'clone', '-q', origin, work], check=True, capture_output=True)
        git(work, 'config', 'user.email', 't@t'); git(work, 'config', 'user.name', 't')
        open(os.path.join(work, '.beans.yml'), 'w').write('beans:\n    path: .beans\n    prefix: bean-\n    id_length: 4\n    default_status: todo\n    default_type: task\n')
        _bean_file(os.path.join(work, '.beans'), 'bean-tsv1', 'esp-campaign-sync-daily still red')
        git(work, 'add', '-A'); git(work, 'commit', '-q', '-m', 'beans'); git(work, 'push', '-q', 'origin', 'HEAD:main')
        # the stale cwd: same config, an unrelated bean only
        os.makedirs(stale)
        open(os.path.join(stale, '.beans.yml'), 'w').write(open(os.path.join(work, '.beans.yml')).read())
        _bean_file(os.path.join(stale, '.beans'), 'bean-old1', 'unrelated old bean')
        log = os.path.join(tmp, 'log.txt')
        open(log, 'w').write(line('esp-campaign-sync-daily', '2026-10-15T00:00:00.000Z') + '\n')
        p = subprocess.run([sys.executable, SCRIPT, '--log-file', log, '--repo', work, '--now', NOW], cwd=stale, capture_output=True, text=True)
    assert p.returncode == 0 and 'bean-tsv1' in p.stdout and 'UNOWNED' not in p.stdout, (p.returncode, p.stdout, p.stderr)


def test_unmeasured_is_not_a_pass():
    p = subprocess.run([sys.executable, SCRIPT, '--log-file', '/nonexistent', '--beans-json', '/nonexistent'], capture_output=True, text=True)
    assert p.returncode == 2 and 'UNMEASURED' in p.stdout, (p.returncode, p.stdout)


if __name__ == '__main__':
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith('test_'):
            try:
                fn(); print('ok  ', name)
            except AssertionError as e:
                failed += 1; print('FAIL', name, e)
    sys.exit(1 if failed else 0)
