"""Push admission is FIFO: exercised through the real fleet-slot CLI and its flock pool."""
import fcntl
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

SLOT = Path(__file__).resolve().parents[1] / 'bin/fleet-slot'


class PushQueue(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        home = Path(self.tmp.name)
        self.root = home / 'watch/state/heavy-slots'
        self.root.mkdir(parents=True)
        self.env = dict(os.environ, DROVER_HOME=str(home), DROVER_CONFIG='/nonexistent',
                        FLEET_STATE=str(home / 'watch/state'), FLEET_PUSH_SLOTS='1', FLEET_HEAVY_SLOTS='2')
        self.env.pop('FLEET_SLOT_HELD', None)
        # Only the sleep boundary is instrumented; admission and execution are the real CLI.
        self.driver = home / 'driver.py'
        self.driver.write_text('''import os, pathlib, runpy, sys, time
sleep = time.sleep
def waiting(seconds):
    pathlib.Path(os.environ['READY']).touch()
    sleep(seconds)
time.sleep = waiting
sys.argv = [sys.argv[1]] + sys.argv[2:]
runpy.run_path(sys.argv[0], run_name='__main__')
''')

    def wait_for(self, predicate, message):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(.02)
        self.fail(message)

    def occupy(self, name='p1'):
        fd = os.open(self.root / f'slot-{name}.lock', os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        self.addCleanup(lambda: os.close(fd))
        return fd

    def start(self, name, push=True, **overrides):
        admitted, release, ready = (self.root.parent / f'{name}.{s}' for s in ('in', 'out', 'ready'))
        child = self.root.parent / f'{name}.py'
        child.write_text('import pathlib, time\n'
                         f'pathlib.Path({str(admitted)!r}).touch()\n'
                         f'while not pathlib.Path({str(release)!r}).exists(): time.sleep(.02)\n')
        proc = subprocess.Popen([sys.executable, str(self.driver), str(SLOT), 'run',
                                 *(['--push'] if push else []), '--', sys.executable, str(child)],
                                env=dict(self.env, READY=str(ready), **overrides),
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)

        def cleanup():
            release.touch()
            try:
                os.killpg(proc.pid, signal.SIGCONT)
                proc.wait(timeout=4)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=4)
            except ProcessLookupError:
                pass
        self.addCleanup(cleanup)
        return proc, admitted, release, ready

    def test_earlier_live_waiter_cannot_be_overtaken(self):
        held = self.occupy()
        early, first, release, ready = self.start('early')
        self.wait_for(ready.exists, 'earlier waiter did not reach the polling boundary')
        os.kill(early.pid, signal.SIGSTOP)          # stopped is still live: it keeps its turn
        fcntl.flock(held, fcntl.LOCK_UN)
        late, second, later_release, later_ready = self.start('late')
        self.wait_for(lambda: later_ready.exists() or second.exists(), 'later waiter did not run')
        self.assertFalse(second.exists(), 'later arrival stole the free slot from a live earlier waiter')
        os.kill(early.pid, signal.SIGCONT)
        self.wait_for(first.exists, 'earlier waiter was not admitted')
        release.touch()
        self.assertEqual(early.wait(timeout=5), 0)
        self.wait_for(second.exists, 'later waiter was not admitted after the first released')
        later_release.touch()
        self.assertEqual(late.wait(timeout=5), 0)

    def test_dead_head_is_skipped(self):
        held = self.occupy()
        dead, _, _, ready = self.start('dead')
        self.wait_for(ready.exists, 'head did not wait')
        dead.kill()
        dead.wait(timeout=5)
        fcntl.flock(held, fcntl.LOCK_UN)
        live, admitted, release, _ = self.start('live')
        self.wait_for(admitted.exists, 'dead head blocked admission')
        release.touch()
        self.assertEqual(live.wait(timeout=5), 0)
        self.assertEqual(list((self.root / 'push-queue').glob('*.ticket')), [])

    def test_push_pool_keeps_its_parallel_capacity(self):
        one, first, release1, _ = self.start('one', FLEET_PUSH_SLOTS='2')
        self.wait_for(first.exists, 'first slot not admitted')
        two, second, release2, _ = self.start('two', FLEET_PUSH_SLOTS='2')
        self.wait_for(second.exists, 'second slot serialized behind the first')
        three, third, release3, ready3 = self.start('three', FLEET_PUSH_SLOTS='2')
        self.wait_for(ready3.exists, 'third waiter did not wait')
        self.assertFalse(third.exists(), 'pool exceeded two slots')
        release1.touch()
        self.wait_for(third.exists, 'third waiter did not fill the released slot')
        release2.touch()
        release3.touch()
        for proc in (one, two, three):
            self.assertEqual(proc.wait(timeout=5), 0)

    def test_general_pool_does_not_queue(self):
        self.occupy()
        _, _, _, ready = self.start('push')
        self.wait_for(ready.exists, 'push did not wait')
        general, admitted, release, _ = self.start('general', push=False)
        self.wait_for(admitted.exists, 'push queue blocked the independent general pool')
        release.touch()
        self.assertEqual(general.wait(timeout=5), 0)

    def test_interrupted_waiter_removes_its_ticket(self):
        self.occupy()
        proc, _, _, ready = self.start('interrupt')
        self.wait_for(ready.exists, 'waiter did not queue')
        self.assertEqual(len(list((self.root / 'push-queue').glob('*.ticket'))), 1)
        proc.send_signal(signal.SIGINT)
        self.assertNotEqual(proc.wait(timeout=5), 0)
        self.assertEqual(list((self.root / 'push-queue').glob('*.ticket')), [])


if __name__ == '__main__':
    unittest.main()
