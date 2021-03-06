import time
import signal
import sys
import os

from circus.tests.support import TestCircus
from circus.stream import QueueStream
from circus.watcher import Watcher
from circus.process import UNEXISTING


def run_process(test_file):
    try:
        i = 0
        while True:
            sys.stdout.write('%d-%s\\n' % (os.getpid(), i))
            sys.stdout.flush()
            time.sleep(1)
    except:
        return 1


class TestWatcher(TestCircus):

    def setUp(self):
        super(TestWatcher, self).setUp()
        self.stream = QueueStream()
        dummy_process = 'circus.tests.test_watcher.run_process'
        self.test_file = self._run_circus(dummy_process,
                stdout_stream={'stream': self.stream})
        self.arbiter = self.arbiters[-1]

    def status(self, cmd, **props):
        resp = self.call(cmd, **props)
        return resp.get('status')

    def numprocesses(self, cmd, **props):
        resp = self.call(cmd, **props)
        return resp.get('numprocesses')

    def pids(self):
        return self.call('list', name='test').get('pids')

    def test_signal(self):
        self.assertEquals(self.numprocesses('incr', name='test'), 2)

        pids = self.pids()
        self.assertEquals(len(pids), 2)
        to_kill = pids[0]
        self.assertEquals(self.status('signal', name='test', pid=to_kill,
                                      signum=signal.SIGKILL), 'ok')

        time.sleep(1)  # wait for the process to die

        # we still should have two processes, but not the same pids for them
        pids = self.pids()
        self.assertEquals(len(pids), 2)
        self.assertTrue(to_kill not in pids)

    def test_unexisting(self):
        watcher = self.arbiter.get_watcher("test")

        self.assertEquals(len(watcher.processes), 1)
        process = watcher.processes.values()[0]
        to_kill = process.pid
        # the process is killed in an unsual way
        os.kill(to_kill, signal.SIGSEGV)
        # and wait for it to die
        try:
            pid, status = os.waitpid(to_kill, 0)
        except OSError:
            pass

        # ansure the old process is considered "unexisting"
        self.assertEquals(process.status, UNEXISTING)

        # this should clean up and create a new process
        watcher.reap_and_manage_processes()

        # we should have a new process here now
        self.assertEquals(len(watcher.processes), 1)
        process = watcher.processes.values()[0]
        # and that one needs to have a new pid.
        self.assertNotEqual(process.pid, to_kill)
        # and should not be unexisting...
        self.assertNotEqual(process.status, UNEXISTING)

    def test_stats(self):
        resp = self.call("stats").get('infos')
        self.assertTrue("test" in resp)
        watchers = resp['test']

        self.assertEqual(watchers[watchers.keys()[0]]['cmdline'],
                         sys.executable.split(os.sep)[-1])

    def test_streams(self):
        time.sleep(1.)
        # let's see what we got
        self.assertTrue(self.stream.qsize() > 1)

    def test_max_age(self):
        result = self.call('set', name='test',
                           options={'max_age': 1, 'max_age_variance': 0})
        self.assertEquals(result.get('status'), 'ok')
        initial_pids = self.pids()
        time.sleep(3.0)  # allow process to reach max_age and restart
        current_pids = self.pids()
        self.assertEqual(len(current_pids), 1)
        self.assertNotEqual(initial_pids, current_pids)

    def test_arbiter_reference(self):
        self.assertEqual(self.arbiters[0].watchers[0].arbiter,
                         self.arbiters[0])


class TestWatcherFromConfiguration(TestCircus):

    def test_env_from_string(self):
        config = {'name': 'foobar',
                  'cmd': 'foobar',
                  'env': 'coconuts=migrate'}
        watcher = Watcher.load_from_config(config)
        self.assertEquals(watcher.env, {'coconuts': 'migrate'})


class TestWatcherInitialization(TestCircus):

    def test_copy_env(self):
        old_environ = os.environ
        try:
            os.environ = {'COCONUTS': 'MIGRATE'}
            watcher = Watcher("foo", "foobar", copy_env=True)
            self.assertEquals(watcher.env, os.environ)

            watcher = Watcher("foo", "foobar", copy_env=True,
                              env={"AWESOMENESS": "YES"})
            self.assertEquals(watcher.env,
                              {'COCONUTS': 'MIGRATE', 'AWESOMENESS': 'YES'})
        finally:
            os.environ = old_environ

    def test_copy_path(self):
        stream = QueueStream()
        qstream = {'stream': stream}
        old_environ = os.environ
        old_paths = sys.path[:]
        try:
            sys.path = ['XYZ']
            os.environ = {'COCONUTS': 'MIGRATE'}
            cmd = ('%s -c "import sys; '
                   'sys.stdout.write(\':\'.join(sys.path)); '
                   ' sys.stdout.flush()"') % sys.executable
            watcher = Watcher('xx', cmd, copy_env=True, copy_path=True,
                              stdout_stream=qstream)
            watcher.start()
            time.sleep(3.)
            watcher.stop()
            data = [v for k, v in stream.get().items()][1]
            data = ''.join(data)
            self.assertTrue('XYZ' in data, data)
        finally:
            os.environ = old_environ
            sys.path[:] = old_paths


SUCCESS = 1
FAILURE = 2
ERROR = 3


class TestWatcherHooks(TestCircus):

    def run_with_hooks(self, hooks):
        self.stream = QueueStream()
        dummy_process = 'circus.tests.test_watcher.run_process'
        self._run_circus(dummy_process,
                stdout_stream={'stream': self.stream},
                hooks=hooks)

    def _stop(self):
        self.call("stop", name="test")

    def get_status(self):
        return self.call("status", name="test")['status']

    def test_missing_hook(self):
        hooks = {'before_start': ('fake.hook.path', False)}
        self.assertRaises(ImportError, self.run_with_hooks, hooks)

    def _test_hooks(self, hook_name='before_start', status='active',
                    behavior=SUCCESS, call=None):
        self.before_start_called = False

        def hook(watcher, arbiter, hook_name):
            self.before_start_called = True
            self.arbiter_in_hook = arbiter
            if behavior == SUCCESS:
                return True
            elif behavior == FAILURE:
                return False
            raise TypeError('beeeuuua')

        hooks = {hook_name: (hook, False)}
        self.run_with_hooks(hooks)
        if call:
            call()
        self.assertTrue(self.before_start_called)
        self.assertEqual(self.arbiter_in_hook, self.arbiters[0])
        self.assertEqual(self.get_status(), status)

    def test_before_start(self):
        self._test_hooks()

    def test_before_start_fails(self):
        self._test_hooks(behavior=ERROR, status='stopped')

    def test_before_start_false(self):
        self._test_hooks(behavior=FAILURE, status='stopped',
                         hook_name='after_start')

    def test_after_start(self):
        self._test_hooks(hook_name='after_start')

    def test_after_start_fails(self):
        self._test_hooks(behavior=ERROR, status='stopped',
                         hook_name='after_start')

    def test_after_start_false(self):
        self._test_hooks(behavior=FAILURE, status='stopped',
                         hook_name='after_start')

    def test_before_stop(self):
        self._test_hooks(hook_name='before_stop', status='stopped',
                         call=self._stop)

    def test_before_stop_fails(self):
        self._test_hooks(behavior=ERROR, status='stopped',
                         hook_name='before_stop',
                         call=self._stop)

    def test_before_stop_false(self):
        self._test_hooks(behavior=FAILURE, status='stopped',
                         hook_name='before_stop', call=self._stop)

    def test_after_stop(self):
        self._test_hooks(hook_name='after_stop', status='stopped',
                         call=self._stop)

    def test_after_stop_fails(self):
        self._test_hooks(behavior=ERROR, status='stopped',
                         hook_name='after_stop',
                         call=self._stop)

    def test_after_stop_false(self):
        self._test_hooks(behavior=FAILURE, status='stopped',
                         hook_name='after_stop', call=self._stop)
