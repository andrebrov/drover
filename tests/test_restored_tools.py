"""bean-hulr: execute restored behavior without touching the live fleet."""
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    loader = importlib.machinery.SourceFileLoader(name.replace('-', '_'), str(ROOT / 'bin' / name))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    with patch.object(sys, 'argv', [name]):
        loader.exec_module(module)
    return module


class InstalledFixes(unittest.TestCase):

    def test_codex_mcp_flag_is_not_mistaken_for_an_mcp_server(self):
        yolo = load('fleet-yolo')
        def sh(*args, **kwargs):
            out = ('123 tty1 /opt/bin/codex -c mcp_servers.example_hub.url=http://127.0.0.1:7900/example/mcp\n'
                   '124 tty2 /opt/bin/codex mcp-server\n') if args[1] == '-axo' else 'HERDR_PANE_ID=w:p'
            return subprocess.CompletedProcess(args, 0, out, '')
        with patch.object(yolo, 'sh', side_effect=sh):
            self.assertEqual([p[0] for p in yolo.pane_processes()['w:p']], ['123'])


    def test_disabled_take_never_refuses_or_changes_existing_claims(self):
        with tempfile.TemporaryDirectory() as home:
            claims = Path(home) / 'claims.tsv'
            before = 'other\tbean-old\tbackend/file.ts\t2026-10-18T00:00:00Z\n'
            claims.write_text(before)
            env = dict(os.environ, HOME=home, FLEET_CLAIMS_FILE=str(claims))
            result = subprocess.run([str(ROOT/'bin/fleet-claim'), 'take', 'coder', 'bean-hulr', 'backend/file.ts'], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('DISABLED', result.stdout)
            self.assertEqual(claims.read_text(), before)

    def test_pending_recovery_relaunches_in_the_named_seats_worktree(self):
        yolo=load('fleet-yolo')
        with tempfile.TemporaryDirectory() as directory, patch.object(yolo,'PENDING',directory), \
             patch.object(yolo,'DRY',False), patch.object(yolo,'log'), \
             patch.object(yolo,'launch_in_shell',return_value=(None,[('123','claude','--dangerously-skip-permissions')])) as launch, \
             patch.object(yolo,'sh',return_value=subprocess.CompletedProcess([],0,'{"result":{"agents":[{"pane_id":"w:p","name":"judge"}]}}','')):
            (Path(directory)/'judge').write_text('w:p\tclaude\tclaude --dangerously-skip-permissions\n')
            self.assertEqual(yolo.recover_pending({},{}),1)
            self.assertEqual(launch.call_args.kwargs,{'seat':'judge'})



class ObserverRestoration(unittest.TestCase):
    def setUp(self):
        self.obs = load('fleet-observe')
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for name in ('state','reports','tasks','repo/.beans'): (self.root/name).mkdir(parents=True)
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close); self.addCleanup(self.tmp.cleanup)
        for key, value in {'STATE':'state','REPORTS':'reports','TASKS':'tasks','REPO':'repo'}.items():
            self.stack.enter_context(patch.object(self.obs, key, str(self.root/value)))
        self.stack.enter_context(patch.object(self.obs, 'HOME', str(self.root)))
        self.stack.enter_context(patch.dict(os.environ, DROVER_HOME=str(self.root), DROVER_MIGRATIONS_STATUS='fixture-migrate --status'))
        for key, value in {'QUEUE':'lead-queue','POOL':'lead-pool','SENT':'observe-sent','PANE_CACHE':'pane-cache.json'}.items():
            self.stack.enter_context(patch.object(self.obs,key,str(self.root/'state'/value)))

    def test_hidden_files_and_directories_are_not_assignments(self):
        (self.root/'tasks/.lock').write_text('not an assignment')
        (self.root/'tasks/archive').mkdir()
        (self.root/'tasks/coder').write_text('100\tbean-hulr\tnone\t/report.md')
        self.assertEqual([(a,b) for a,b,_ in self.obs.ledger()], [('coder','bean-hulr')])

    def test_unnamed_roster_reuses_remembered_seat(self):
        (self.root/'state/pane-seat.json').write_text('{"w:p":"coder"}')
        def run(args, **kwargs): return subprocess.CompletedProcess(args, 0, '', '')
        with patch.object(self.obs, 'sh', return_value=json.dumps({'result':{'agents':[{'name':None,'pane_id':'w:p','agent':'codex'}]}})), \
             patch.object(self.obs.subprocess, 'run', side_effect=run) as command:
            self.assertIn('coder', self.obs.agents())
            self.assertEqual(command.call_args.args[0], ['herdr','agent','rename','w:p','coder'])

    def test_empty_roster_does_not_run_seat_checks(self):
        with patch.object(self.obs, 'agents', return_value={}), patch.object(self.obs, 'pane_states') as panes, \
             patch.object(self.obs, 'git') as git, patch.object(self.obs, 'sh'):
            self.obs.check()
            panes.assert_not_called(); git.assert_not_called()

    def test_progress_accepts_a_new_branch_for_the_same_seat(self):
        with patch.object(self.obs, 'git', side_effect=['', '200 fleet/coder-bean-other']):
            self.assertEqual(self.obs.progress('coder','bean-hulr',100), (200,'fleet/coder-bean-other'))

    def test_already_owned_queue_item_is_not_dispatched_twice(self):
        (self.root/'state/lead-pool').write_text('coder\n')
        (self.root/'state/lead-queue').write_text('bean-hulr\t*\twork\n')
        (self.root/'tasks/other').write_text('100\tbean-hulr\tnone')
        with patch.object(self.obs, 'QUEUE', str(self.root/'state/lead-queue')), \
             patch.object(self.obs, 'POOL', str(self.root/'state/lead-pool')), \
             patch.object(self.obs, 'DRY', False), patch.object(self.obs.subprocess, 'run') as send:
            self.obs.dispatch({'coder':{'agent':'codex','agent_status':'idle'}}, set())
            send.assert_not_called()
            self.assertEqual((self.root/'state/lead-queue').read_text(), '')

    def test_idle_seats_routed_elsewhere_are_reported(self):
        (self.root/'state/lead-pool').write_text('a\nb\n')
        (self.root/'state/lead-queue').write_text('bean-other\tclaude\twork\n')
        with patch.object(self.obs, 'QUEUE', str(self.root/'state/lead-queue')), \
             patch.object(self.obs, 'POOL', str(self.root/'state/lead-pool')), \
             patch.object(self.obs, 'DRY', True), patch.object(self.obs, 'alert') as alert:
            self.obs.dispatch({s:{'agent':'codex','agent_status':'idle'} for s in ['a','b']}, set())
            self.assertEqual(alert.call_count, 1)

    def test_shell_timeout_kills_the_descendant_holding_the_pipe(self):
        start = time.monotonic()
        with tempfile.TemporaryDirectory() as directory:
            # The descendant retains stdout even after its parent times out.
            marker = Path(directory)/'late'
            result = self.obs.sh(sys.executable, '-c',
                "import subprocess,time; subprocess.Popen(['sh','-c', 'sleep 0.5; touch '+"+repr(str(marker))+"]); time.sleep(5)", timeout=.05)
            self.assertEqual(result, '')
            time.sleep(.6)
            self.assertFalse(marker.exists())
        self.assertLess(time.monotonic()-start, 2)

    def test_owner_reaping_keeps_a_new_bean_visible_only_on_origin(self):
        owners=self.root/'state/owner'; owners.mkdir()
        for bean in ('bean-done','bean-new','bean-gone'): (owners/bean).write_text('coder')
        (self.root/'repo/.beans/bean-done--done.md').write_text('status: completed\n')
        with patch.object(self.obs,'git',return_value='.beans/bean-new--new.md'), patch.object(self.obs,'DRY',False):
            self.obs.reap_owners()
        self.assertEqual(sorted(p.name for p in owners.iterdir()), ['bean-new'])
        self.assertTrue((self.root/'handoffs/owner-reaped/bean-done').exists())

    def test_critical_work_uses_origin_snapshot_and_includes_owner_records(self):
        (self.root/'state/owner').mkdir()
        (self.root/'state/owner/bean-owned').write_text('coder')
        def sh(*args, **kwargs):
            if args[0]=='beans': return json.dumps({'beans':[{'id':'bean-owned'},{'id':'bean-free'}]})
            if args[0]=='bash':
                import shlex
                snapshot=Path(shlex.split(args[-1])[-1])
                (snapshot/'.beans').mkdir(); (snapshot/'.beans.yml').write_text('path: .beans')
            return ''
        with patch.object(self.obs,'sh',side_effect=sh) as command, patch.object(self.obs,'alert') as alert:
            self.obs.critical_work([])
            self.assertEqual(alert.call_args.args[2], 'bean-free')
            archive=next(c for c in command.call_args_list if 'archive' in str(c.args))
            self.assertIn('origin/main',str(archive.args))
            query=next(c for c in command.call_args_list if c.args[0]=='beans')
            self.assertNotEqual(query.kwargs['cwd'], str(self.root/'repo'))

    def test_migration_status_is_read_only_held_filtered_and_hourly(self):
        (self.root/'state/migrations-held').write_text('1001 founder holds\n')
        with patch.object(self.obs,'sh',return_value='PENDING 1001_held.sql\nPENDING 1002_needed.sql\n') as sh, \
             patch.object(self.obs,'alert') as alert, patch.object(self.obs,'DRY',False):
            self.obs.migration_health()
            self.obs.migration_health()
            self.assertEqual(sh.call_count,1)
            self.assertIn('default_transaction_read_only=on',str(sh.call_args.args))
            self.assertIn('--status',str(sh.call_args.args))
            self.assertNotIn('--up',str(sh.call_args.args))
            self.assertEqual(alert.call_args.args[0], 'migrations-pending-1002')

    def test_shared_checkout_reports_failed_fast_forward_and_old_stray_only(self):
        old=self.root/'repo/.beans/bean-old--old.md'; old.write_text('old')
        new=self.root/'repo/.beans/bean-new--new.md'; new.write_text('new')
        os.utime(old,(self.obs.NOW-1000,self.obs.NOW-1000))
        def git(*args):
            if args[:1]==('status',): return '?? .beans/bean-old--old.md\n?? .beans/bean-new--new.md'
            if args[:1]==('rev-parse',): return 'main'
            if args[:1]==('rev-list',): return '2'
            return ''
        with patch.object(self.obs,'git',side_effect=git), patch.object(self.obs,'DRY',False), \
             patch.object(self.obs.subprocess,'run',return_value=subprocess.CompletedProcess([],1,'','blocked by local file')), \
             patch.object(self.obs,'alert') as alert:
            self.obs.shared_checkout_health()
            self.assertTrue(any(c.args[0].startswith('main-ff-blocked') for c in alert.call_args_list))
            stray=next(c for c in alert.call_args_list if c.args[0].startswith('stray-beans'))
            self.assertIn('bean-old',str(stray.args)); self.assertNotIn('bean-new',str(stray.args))

    def test_transient_shared_index_lock_does_not_raise_a_stale_checkout_alert(self):
        def git(*args):
            if args[:1]==('rev-parse',): return 'main'
            if args[:1]==('rev-list',): return '2'
            return ''
        with patch.object(self.obs,'git',side_effect=git), patch.object(self.obs,'DRY',False), \
             patch.object(self.obs.subprocess,'run',return_value=subprocess.CompletedProcess([],1,'','index.lock already exists')), \
             patch.object(self.obs,'alert') as alert:
            self.obs.shared_checkout_health(); alert.assert_not_called()

    def test_migration_query_failure_is_unavailable(self):
        with patch.object(self.obs,'sh',return_value=''), patch.object(self.obs,'DRY',True), patch.object(self.obs,'alert') as alert:
            self.obs.migration_health()
            self.assertEqual(alert.call_args.args[0],'migrations-status-unavailable')

    def test_empty_origin_does_not_reap_unknown_owners(self):
        owners=self.root/'state/owner'; owners.mkdir(); (owners/'bean-new').write_text('coder')
        with patch.object(self.obs,'git',return_value=''), patch.object(self.obs,'DRY',False): self.obs.reap_owners()
        self.assertTrue((owners/'bean-new').exists())

    def test_owner_file_alone_prevents_duplicate_dispatch(self):
        (self.root/'state/owner').mkdir(); (self.root/'state/owner/bean-hulr').write_text('other')
        (self.root/'state/lead-pool').write_text('coder\n')
        (self.root/'state/lead-queue').write_text('bean-hulr\t*\twork\n')
        with patch.object(self.obs,'DRY',False), patch.object(self.obs.subprocess,'run') as send:
            self.obs.dispatch({'coder':{'agent':'codex','agent_status':'idle'}},set()); send.assert_not_called()

    def run_check(self):
        with patch.object(self.obs,'agents',return_value={'coder':{'agent':'codex'}}), \
             patch.object(self.obs,'pane_states',return_value={'coder':{'state':'unknown'}}), \
             patch.object(self.obs,'git',return_value=''), patch.object(self.obs,'sh',return_value=''), \
             patch.object(self.obs,'on_origin',return_value=True), patch.object(self.obs,'dispatch'), \
             patch.object(self.obs,'critical_work',create=True) as critical, patch.object(self.obs,'migration_health',create=True) as migration, \
             patch.object(self.obs,'shared_checkout_health',create=True) as checkout, patch.object(self.obs,'reap_owners',create=True) as owners, \
             patch.object(self.obs,'DRY',False), patch.object(self.obs,'alert'):
            self.obs.check()
            return [critical.call_count,migration.call_count,checkout.call_count,owners.call_count]

    def test_check_reaches_restored_operational_checks(self):
        self.assertEqual(self.run_check(),[1,1,1,1])

    def test_exact_bean_report_fallback_retains_the_origin_proof_requirement(self):
        (self.root/'tasks/coder').write_text('100\tbean-hulr\tnone')
        (self.root/'reports/bean-hulr.md').write_text('DONE '+'a'*40+'\n')
        self.run_check()
        self.assertFalse((self.root/'tasks/coder').exists())

    def test_parked_blocked_assignment_is_not_released(self):
        (self.root/'tasks/coder').write_text('100\tbean-demo-standby\tnone')
        (self.root/'reports/coder-bean-demo-standby.md').write_text('BLOCKED waiting for lead\n')
        self.run_check()
        self.assertTrue((self.root/'tasks/coder').exists())


if __name__ == '__main__': unittest.main()
