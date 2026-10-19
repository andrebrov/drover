"""Validate the actual published entry point and crawler/citation artifacts."""
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from urllib.parse import urlsplit, unquote
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / 'docs'
URL = 'https://andrebrov.github.io/drover/'

class Page(HTMLParser):
    def __init__(self, path):
        super().__init__(); self.tags=[]; self.ids=set(); self.ld=[]; self.capture=False
        self.feed(path.read_text())
    def handle_starttag(self, tag, attrs):
        attrs=dict(attrs); self.tags.append((tag,attrs))
        if 'id' in attrs: self.ids.add(attrs['id'])
        if tag=='script' and attrs.get('type')=='application/ld+json': self.capture=True
    def handle_data(self, data):
        if self.capture: self.ld.append(data)
    def handle_endtag(self, tag):
        if tag=='script': self.capture=False

def homepage():
    assert (SITE/'index.html').is_file(), 'Pages entry point must exist'
    return Page(SITE/'index.html')

def test_page_exposes_canonical_software_metadata():
    page=homepage()
    canonical=[a['href'] for t,a in page.tags if t=='link' and a.get('rel')=='canonical']
    assert canonical==[URL]
    ld=json.loads(''.join(page.ld))
    assert ld['@type']=='SoftwareSourceCode'
    assert ld['url']==URL
    assert ld['codeRepository']=='https://github.com/andrebrov/drover'
    assert ld['license']=='https://spdx.org/licenses/MIT.html'
    assert ld['version']=='0.2.0'
    assert not ({'aggregateRating','review','award'} & ld.keys()), 'Do not invent endorsements'

def test_page_links_and_assets_resolve_inside_published_tree():
    page=homepage()
    refs=[a[k] for t,a in page.tags for k in ('href','src') if k in a]
    assert len(refs)>10
    for ref in refs:
        parsed=urlsplit(ref)
        if parsed.scheme or parsed.netloc: continue
        path=unquote(parsed.path)
        assert not path.startswith('/'), f'Project Pages needs relative paths: {ref}'
        target=(SITE/path).resolve() if path else SITE/'index.html'
        assert target.is_relative_to(SITE.resolve()), ref
        assert target.is_file(), ref
        if parsed.fragment:
            assert target.suffix=='.html' and parsed.fragment in Page(target).ids, ref

def test_crawler_and_citation_urls_match_the_published_project():
    homepage()
    assert (SITE/'.nojekyll').is_file()
    urls=[x.text for x in ET.parse(SITE/'sitemap.xml').iter() if x.tag.endswith('}loc')]
    assert urls==[URL]
    text=(SITE/'llms.txt').read_text()
    assert URL in text and 'https://github.com/andrebrov/drover' in text
    for ref in re.findall(r'\]\((https://andrebrov.github.io/drover/[^)]*)\)',text):
        path=urlsplit(ref).path.removeprefix('/drover/') or 'index.html'
        assert (SITE/path).is_file(),ref
    citation=json.loads((ROOT/'CITATION.cff').read_text())
    assert citation['cff-version']=='1.2.0'
    assert citation['version']=='0.2.0' and citation['license']=='MIT'
    assert citation['url']==URL and citation['repository-code']=='https://github.com/andrebrov/drover'

def test_demo_is_a_readable_asciicast_with_fixture_disclosure():
    path=SITE/'assets/demo.cast'
    assert path.is_file(), 'Record the real fixture commands, not invented terminal output'
    lines=[json.loads(line) for line in path.read_text().splitlines()]
    assert lines[0]['version']==2 and lines[0]['width']>=80
    events=lines[1:]
    assert events and all(e[1]=='o' and isinstance(e[2],str) for e in events)
    assert all(a[0]<=b[0] for a,b in zip(events,events[1:]))
    output=''.join(e[2] for e in events)
    assert 'synthetic' in output.lower()
    assert 'fleet-claims-audit: OK' in output and 'fleet-claims-audit: UNKNOWN' in output
    assert 'exit 2' in output
    assert 'https://andrebrov.github.io/drover/' in (ROOT/'README.md').read_text()

def test_demo_can_be_rerecorded_offline(tmp_path):
    import os, subprocess, sys
    script=ROOT/'scripts/record-demo.py'
    assert script.is_file(), 'Demo must have a reproducible local recorder'
    target=tmp_path/'demo.cast'
    env={k:v for k,v in os.environ.items() if not k.startswith(('DROVER_','FLEET_','GIT_'))}
    env['FLEET_PROOF_SCRATCH']=str(tmp_path)
    result=subprocess.run([sys.executable,str(script),str(target)],env=env,text=True,capture_output=True,timeout=25)
    assert result.returncode==0, result.stdout+result.stderr
    transcript=''.join(json.loads(x)[2] for x in target.read_text().splitlines()[1:])
    assert 'fleet-claims-audit: OK' in transcript and 'DUPLICATE_CURRENT_SEATS' in transcript
    assert 'exit 2' in transcript

def test_source_code_uses_schema_org_target_product_for_the_os():
    # https://schema.org/SoftwareSourceCode, read 2026-10-19:
    # operatingSystem belongs to SoftwareApplication, not SoftwareSourceCode.
    ld=json.loads(''.join(homepage().ld))
    assert 'operatingSystem' not in ld
    assert ld['targetProduct']=={'@type':'SoftwareApplication','name':'macOS'}
