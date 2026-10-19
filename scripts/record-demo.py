#!/usr/bin/env python3
"""Record real offline ownership checks; add reading time for a 15-second asciicast.

Usage: python3 scripts/record-demo.py [output.cast]
No Herdr session, credentials or network call is used. Sources are synthetic fixtures.
Command output is unmodified. Playback timing is edited, not a wall-time benchmark.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]

def main():
    output=Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'docs/assets/demo.cast'
    env={k:v for k,v in os.environ.items() if not k.startswith(('DROVER_','FLEET_','GIT_'))}
    events=[[0,'o','drover / synthetic fixtures / real CLI output\r\nPlayback dwell time edited for reading; no live agents.\r\n\r\n']]
    with tempfile.TemporaryDirectory(dir=os.environ.get('FLEET_PROOF_SCRATCH')) as scratch:
        home=Path(scratch);env.update(HOME=str(home),DROVER_CONFIG=str(home/'no-config'))
        for at,name,expected in [(1,'clean',0),(7,'duplicate',2)]:
            fixture=home/name
            shutil.copytree(ROOT/'tests/fixtures/claims-audit'/name,fixture)
            (fixture/'seat-state').mkdir();repo=fixture/'repo';repo.mkdir()
            def git(*args):
                return subprocess.run(['git','-C',str(repo),*args],env=env,check=True,capture_output=True,text=True)
            git('init','-q');git('config','user.name','Fixture');git('config','user.email','fixture@example.invalid')
            (repo/'.beans').mkdir()
            for bean,status in json.loads((fixture/'beans.json').read_text()).items():
                (repo/'.beans'/f'{bean}--fixture.md').write_text(f'---\nstatus: {status}\n---\n')
            git('add','.beans');git('commit','-qm','synthetic board');git('update-ref','refs/remotes/origin/main','HEAD')
            result=subprocess.run([sys.executable,str(ROOT/'bin/fleet-claims-audit'),'--fixture',name],cwd=home,env=env,text=True,capture_output=True,timeout=15)
            if result.returncode!=expected:
                raise RuntimeError(f'{name} audit exited {result.returncode}: {result.stdout}{result.stderr}')
            events.append([at,'o',f'$ fleet-claims-audit --fixture {name}\r\n'])
            events.append([at+1,'o',(result.stdout+result.stderr).replace('\n','\r\n')+f'exit {result.returncode}\r\n\r\n'])
        events.append([15,'o','Check evidence before dispatch. Resolve UNKNOWN; do not release a seat by status alone.\r\n'])
    header={'version':2,'width':100,'height':18,'title':'drover — synthetic ownership audit; real CLI output, edited timing','env':{'TERM':'xterm-256color'}}
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text('\n'.join(json.dumps(row,ensure_ascii=False) for row in [header,*events])+'\n')
    print(f'Recorded {len(events)} output events (synthetic fixtures, 15-second edited playback).')

if __name__=='__main__':main()
