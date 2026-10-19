"""Installer preflight exercises real Git history and isolated destinations."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

class InstallDrift(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root/'source'
        shutil.copytree(ROOT, self.source, ignore=shutil.ignore_patterns('.git','__pycache__','.pytest_cache'))
        self.env = {k:v for k,v in os.environ.items() if not k.startswith(('GIT_', 'DROVER_', 'FLEET_'))}
        self.env.update(HOME=str(self.root/'home'), DROVER_BIN_DIR=str(self.root/'bin'), DROVER_HOME=str(self.root/'state'),
                        DROVER_CONFIG_DIR=str(self.root/'config'), LAUNCH_AGENTS=str(self.root/'launch'))
        self.git('init','-qb','main')
        self.git('config','user.name','Fixture')
        self.git('config','user.email','fixture@example.invalid')
        self.commit()
    def git(self,*args):
        return subprocess.check_output(['git','-C',str(self.source),*args],env=self.env)
    def commit(self):
        self.git('add','.'); self.git('commit','-qm','fixture')
    def install(self,*args):
        return subprocess.run(['bash',str(self.source/'install.sh'),*args],env=self.env,capture_output=True,text=True)
    def test_clean_installs_and_known_old_version_upgrades(self):
        self.assertEqual(self.install().returncode,0)
        target=self.root/'bin/fleet'
        previous=target.read_bytes(); target.unlink(); target.write_bytes(previous)
        (self.source/'bin/fleet').write_bytes(previous+b'\n# committed update\n'); self.commit()
        result=self.install('--force')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(target.read_bytes(),(self.source/'bin/fleet').read_bytes())
    def test_unknown_drift_refuses_before_any_install_even_force(self):
        (self.root/'bin').mkdir()
        target=self.root/'bin/fleet'; target.write_text('installed-only fix\n')
        result=self.install('--force')
        self.assertNotEqual(result.returncode,0,result.stdout)
        self.assertIn('installed-only',result.stderr)
        self.assertEqual(list((self.root/'bin').iterdir()),[target])
        self.assertEqual(target.read_text(),'installed-only fix\n')
    def test_uncommitted_source_refuses_even_when_destination_matches(self):
        source=self.source/'bin/fleet'; source.write_text(source.read_text()+'\n# uncommitted\n')
        (self.root/'bin').mkdir(); (self.root/'bin/fleet').write_bytes(source.read_bytes())
        result=self.install('--force')
        self.assertNotEqual(result.returncode,0,result.stdout)
        self.assertIn('committed',result.stderr)
    def test_uninstall_and_dry_run_do_not_write(self):
        result=self.install('--dry-run')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertFalse((self.root/'bin').exists())

    def test_untracked_executable_cannot_bypass_source_provenance(self):
        extra=self.source/'bin/untracked-helper';extra.write_text('#!/bin/sh\necho untracked\n');extra.chmod(0o755)
        result=self.install('--force')
        self.assertNotEqual(result.returncode,0,result.stdout)
        self.assertFalse((self.root/'bin/untracked-helper').exists())

    def test_uninstall_removes_only_own_links_even_after_local_source_edit(self):
        self.assertEqual(self.install().returncode,0)
        source=self.source/'bin/fleet';source.write_text(source.read_text()+'\n# local edit\n')
        result=self.install('--uninstall')
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertFalse((self.root/'bin/fleet').exists())
