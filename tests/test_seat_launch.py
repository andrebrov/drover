"""Exercise the real fleet launcher with a recorded native transport."""
import json, os
from pathlib import Path
import subprocess, tempfile, unittest
ROOT=Path(__file__).resolve().parents[1]

class SeatLaunch(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  stub=self.root/'herdr';stub.write_text('''#!/usr/bin/env python3
import json,os,sys
args=sys.argv[1:]
with open(os.environ['CALLS'],'a') as f:f.write(json.dumps(args)+'\\n')
if args[:2]==['agent','list']: doc={'result':{'agents':[]}}
elif args[:2]==['workspace','list']:doc={'result':{'workspaces':[]}}
elif args[:2]==['worktree','open']:doc={'result':{'workspace':{'workspace_id':'ws'}}}
elif args[:2]==['pane','list']:doc={'result':{'panes':[{'pane_id':'ws:p','cwd':'/nonexistent'}]}}
else:doc={'result':{}}
print(json.dumps(doc))
''');stub.chmod(0o755)
  self.env={k:v for k,v in os.environ.items() if not k.startswith(('DROVER_','FLEET_','GIT_'))}
  self.env.update(HOME=str(self.root),DROVER_CONFIG='/nonexistent',FLEET_HERDR=str(stub),FLEET='architect:codex',DROVER_REPO=str(self.root),FLEET_SEED_WIP='0',CALLS=str(self.root/'calls'),DROVER_CALLSIGNS=str(self.root/'names.tsv'),DROVER_SEAT_MODELS='architect=example-model')
  (self.root/'names.tsv').write_text('architect\tNavigator\n')
 def test_callsign_labels_worktree_without_renaming_seat_and_pin_reaches_spawn(self):
  r=subprocess.run(['bash',str(ROOT/'bin/fleet'),'up'],env=self.env,text=True,capture_output=True)
  self.assertEqual(r.returncode,0,r.stdout+r.stderr)
  calls=[json.loads(s) for s in (self.root/'calls').read_text().splitlines()]
  opened=next(c for c in calls if c[:2]==['worktree','open'])
  self.assertEqual(opened[opened.index('--label')+1],'Navigator · architect')
  start=next(c for c in calls if c[:2]==['agent','start'])
  self.assertIn('architect',start)
  self.assertIn('--model example-model',' '.join(start))
