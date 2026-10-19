"""bean-sff1: run the real prep/link/landing commands against isolated Git + npm fixtures."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / 'bin'

class SharedDependencies(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='fleet-dependencies-')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.home = self.base/'home'
        self.main = self.home/'IdeaProjects/project'
        self.seat = self.home/'.herdr/worktrees/project/fleet-test'
        self.cache = self.base/'cache'
        self.fake = self.base/'bin'
        self.fake.mkdir()
        npm = self.fake/'npm'
        npm.write_text("#!/bin/bash\n[ \"${FAIL_NPM:-0}\" != 1 ] || exit 1\necho ci >> \"$NPM_LOG\"\nmkdir -p node_modules/pkg\ncp package-lock.json node_modules/pkg/lock\nif [ \"${HOIST_ONLY:-0}\" != 1 ]; then mkdir -p backend/node_modules/pkg frontend/node_modules/pkg; fi\n")
        npm.chmod(0o755)
        self.env = {**os.environ, 'HOME':str(self.home), 'DROVER_REPO':str(self.main), 'DROVER_SHARED_NM':'1', 'PATH':str(self.fake)+os.pathsep+os.environ['PATH'],
                    'FLEET_NM_CACHE':str(self.cache), 'FLEET_NM_BACKUPS':str(self.base/'backups'),
                    'NPM_LOG':str(self.base/'npm.log'), 'FLEET_EPHEMERAL':'0', 'FAIL_NPM':'0', 'HOIST_ONLY':'0'}
        self.origin = self.base/'origin.git'
        self.run_cmd(['git','init','-q','--bare','-b','main',str(self.origin)])
        self.main.parent.mkdir(parents=True)
        self.run_cmd(['git','clone','-q',str(self.origin),str(self.main)])
        for name,value in [('user.name','Fixture'),('user.email','fixture@example.invalid')]: self.git(self.main,'config',name,value)
        for d in ['.beans','backend','frontend','scripts']: (self.main/d).mkdir()
        (self.main/'.gitignore').write_text('node_modules\n')
        (self.main/'.beans/bean-test--fixture.md').write_text('# fixture')
        for f in ['package.json','backend/package.json','frontend/package.json']: (self.main/f).write_text('{}')
        (self.main/'package-lock.json').write_text('{"fixture":"founder-A"}')
        self.git(self.main,'add','.')
        self.git(self.main,'commit','-qm','fixture')
        self.git(self.main,'push','-q','origin','HEAD:main')
        self.seat.parent.mkdir(parents=True)
        self.git(self.main,'worktree','add','-q','-b','fleet/test-bean-test',str(self.seat),'origin/main')
        (self.seat/'package-lock.json').write_text('{"fixture":"seat-B"}')
        self.git(self.seat,'commit','-qam','own dependency change')

    def run_cmd(self,args,cwd=None,check=True,**kwargs):
        result=subprocess.run([str(a) for a in args],cwd=cwd or self.base,env=self.env,text=True,capture_output=True,**kwargs)
        if check: self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        return result

    def git(self,cwd,*args): return self.run_cmd(['git','-C',cwd,*args]).stdout.strip()
    def nm(self,*args,**kwargs): return self.run_cmd([BIN/'fleet-shared-nm',*args],**kwargs)
    def prep(self,check=True): return self.run_cmd([BIN/'fleet-prep','test','bean-test',self.seat],check=check)
    def keydir(self,repo=None): return self.cache/self.nm('key',repo or self.seat).stdout.strip()
    def assert_links(self):
        expected=self.keydir()
        for d in ['node_modules','backend/node_modules','frontend/node_modules']:
            target=expected/d if (expected/d).is_dir() else expected/'node_modules'
            self.assertTrue((self.seat/d).is_symlink(),str(self.seat/d))
            self.assertEqual((self.seat/d).resolve(),target.resolve())

    def test_persistent_founder_links_move_to_own_lock_key(self):
        for d in ['node_modules','backend/node_modules','frontend/node_modules']:
            (self.main/d).mkdir()
            (self.main/d/'untouched').write_text('founder')
            (self.seat/d).symlink_to(self.main/d,target_is_directory=True)
        result=self.prep()
        self.assertIn('ready on fleet/test-bean-test',result.stdout)
        self.assert_links()
        self.assertNotEqual(self.keydir(),self.keydir(self.main))
        self.assertEqual((self.seat/'node_modules/pkg/lock').read_text(),'{"fixture":"seat-B"}')
        self.assertEqual((self.main/'frontend/node_modules/untouched').read_text(),'founder')
        self.assertEqual(self.git(self.seat,'status','--porcelain'),'')

    def test_a_live_persistent_build_prevents_relink_before_switching(self):
        self.git(self.seat,'switch','--detach','origin/main')
        process=subprocess.Popen(['node','-e',"setTimeout(()=>{},20000)"],cwd=self.seat,env=self.env)
        try:
            time.sleep(0.3)
            result=self.prep(check=False)
            self.assertEqual(result.returncode,7,result.stdout+result.stderr)
            self.assertEqual(self.git(self.seat,'branch','--show-current'),'')
            self.assertFalse((self.seat/'node_modules').exists())
        finally:
            process.terminate()
            process.wait(timeout=5)

    def test_real_directories_are_preserved_before_relink_including_agy_shape(self):
        for d in ['node_modules','backend/node_modules']: (self.seat/d).symlink_to(self.main/d,target_is_directory=True)
        folder=self.seat/'frontend/node_modules'
        folder.mkdir()
        (folder/'retained-package').write_text('do not delete')
        self.prep()
        self.assert_links()
        copies=list((self.base/'backups').glob('*/frontend/node_modules/retained-package'))
        self.assertEqual(len(copies),1)
        self.assertEqual(copies[0].read_text(),'do not delete')

    def test_link_refusal_is_preflight_not_partial_relink(self):
        (self.seat/'node_modules').symlink_to(self.main/'node_modules',target_is_directory=True)
        (self.seat/'frontend/node_modules').mkdir()
        result=self.nm('link',self.seat,self.seat,check=False)
        self.assertEqual(result.returncode,4,result.stdout+result.stderr)
        self.assertEqual(os.readlink(self.seat/'node_modules'),str(self.main/'node_modules'))

    def test_hoisted_workspace_dependencies_do_not_leave_a_stale_nested_link(self):
        self.env['HOIST_ONLY']='1'
        for d in ['backend/node_modules','frontend/node_modules']: (self.seat/d).symlink_to(self.main/d,target_is_directory=True)
        self.prep()
        self.assert_links()
        self.assertEqual((self.seat/'frontend/node_modules').resolve(),(self.keydir()/'node_modules').resolve())

    def test_check_pins_head_key_and_refuses_stale_or_dirty_lockfiles(self):
        self.nm('link',self.seat,self.seat)
        self.nm('check',self.seat,'HEAD')
        foreign=Path(self.nm('ensure',self.main).stdout.strip())
        for d in ['node_modules','backend/node_modules','frontend/node_modules']:
            if (self.seat/d).is_symlink(): (self.seat/d).unlink()
            (self.seat/d).symlink_to(foreign/d,target_is_directory=True)
        stale=self.nm('check',self.seat,'HEAD',check=False)
        self.assertNotEqual(stale.returncode,0)
        self.assertIn('node_modules lock key mismatch',stale.stderr)
        self.nm('link',self.seat,self.seat)
        (self.seat/'package-lock.json').write_text('{"fixture":"dirty"}')
        dirty=self.nm('check',self.seat,'HEAD',check=False)
        self.assertNotEqual(dirty.returncode,0)
        self.assertIn('lockfiles differ from HEAD',dirty.stderr)

    def test_foreign_source_locks_cannot_be_installed_into_a_seat(self):
        result=self.nm('link',self.main,self.seat,check=False)
        self.assertEqual(result.returncode,4,result.stdout+result.stderr)
        self.assertIn("source lock key differs from the worktree",result.stderr)
        self.assertFalse((self.seat/'node_modules').exists())

    def test_failed_dependency_install_never_reports_persistent_seat_ready(self):
        self.env['FAIL_NPM']='1'
        result=self.prep(check=False)
        self.assertEqual(result.returncode,6,result.stdout+result.stderr)
        self.assertNotIn('ready on',result.stdout)

if __name__=='__main__': unittest.main()
