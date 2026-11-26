"""Immutable legacy HTML/robots reference data; never reads external pages."""
import json,sys,html,hashlib
from pathlib import Path
from urllib.robotparser import RobotFileParser
from bs4 import BeautifulSoup
from bs4.dammit import EntitySubstitution,UnicodeDammit
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'apps/api'))
from intelligence.ir_metadata import publication_metadata
from intelligence.util import canonical
def write(path,blob):
    if path.exists():assert path.read_bytes()==blob,'Frozen IR reference differs: '+str(path)
    else:path.write_bytes(blob)
write(ROOT/'apps/backend-rust/assets/legacy-html-entities.json',canonical({'attribute':html.entities.html5,'text':EntitySubstitution.HTML_ENTITY_TO_CHARACTER,'numeric_text':{str(i):UnicodeDammit.numeric_character_reference(i)[0] for i in range(160)},'numeric_attribute':{str(i):html.unescape('&#'+str(i)+';') for i in range(160)}}).encode())
htmls=[
 '<h1> Report <b>earnings</b> &amp; growth </h1><p>Raises <em>guidance</em>.</p>',
 '<h1>First</h1><meta property="og:title" content="Later &amp; title"><p>Nested <p>lowers outlook</p>end</p>',
 '<H1 CLASS=x>A<!--comment-->B<br>C<script>fake <h1>ignored</h1></script><style>ignored</style></H1>',
 '<h1>New\r\nline <span> title </span></h1><p>'+(' '*4)+'\n  </p>',
 '<h1>&notit; &madeup; &not &amp; &#0; &#1; &#13; &#128; &#xD800; &#xFDD0;</h1>',
 '<meta property="wrong" PROPERTY="og:title" content="first" CONTENT="last"><h1>fallback</h1>',
 '<h1>A<template>ignored <b>nested</b></template>B<rt>ruby</rt><rp>paren</rp></h1>',
 '<h1>A<![CDATA[cdata &amp;]]>B</h1><p>no changes to guidance</p>',
 '<h1/>outside<h1>Second</h1><p>raised guidance</p>',
 '<h1>A<noscript><b>inside</b></noscript><textarea><b>text</b></textarea></h1>',
 '<meta name="press_release_publish_date" content="2026-11-18T12:00:00Z">',
 '<meta name="wrong" property="article:published_time" content="2026-11-18T12:00:00Z"><meta property="article:published_time" content="2026-11-18T13:00:00Z">',
 '<meta NAME="PUBDATE" content="invalid"><meta name="pubdate" content="2026-11-18T12:00:00-08:00">',
 '<meta name="datepublished" content="2026-11-18"><meta name="datepublished" content="2026-11-18T13:00:00.123400Z">',
 '<a href="/2026-11-18-Story"> A &amp; <b>B</b> </a><a HREF="x" href="y">overwrite</a>',
 '<h1><script>const a="<a href=\'x";</script>real</h1><p>maintains outlook</p>',
 '<h1>one</h1> <h1>two</h1><p>\u00a0\u001c</p>',
]
cases=[]
for source in htmls:
    soup=BeautifulSoup(source,'html.parser')
    tags=[]
    for tag in soup.find_all(['a','meta','h1','p']):
        tags.append({'name':tag.name,'attrs':{k:v for k,v in tag.attrs.items() if k in ['href','name','property','content']},'text':tag.get_text(' ',strip=True),'raw_text':tag.get_text()})
    original,published=publication_metadata(source)
    cases.append({'html':source,'tags':tags,'publication':[original,published]})
rules=['','User-agent: *\nDisallow: /private\nAllow: /private/public', 'User-agent: *\nAllow: /private/public\nDisallow: /private',
 'User-agent: Other\nDisallow: /\n\nUser-agent: *\nDisallow:', 'User-agent: *\nDisallow: /\n\nUser-agent: *\nAllow: /',
 'User-agent: *\nDisallow: /a%20b?x=1&y=2','User-agent: *\nDisallow: /*secret$','User-agent: *\nDisallow: *',
 'User-agent:\nDisallow: /\n\nUser-agent: *\nAllow: /','User-agent: *\n\nDisallow: /',
 'User-agent: *\n   \nDisallow: /a','User-agent: *\nCrawl-delay: 1\nUser-agent: Other\nDisallow: /',
 'User-agent: * #comment\nDisallow: /\u00e9','User-agent: *\nDisallow: /%FF','User-agent: *\nDisallow: /x?']
urls=['https://investor.axon.com/','https://investor.axon.com/private','https://investor.axon.com/private/public/x','https://investor.axon.com/a%20b?x=1&y=2','https://investor.axon.com/a b?x=1&y=2','https://investor.axon.com/*secret$','https://investor.axon.com/secret','https://investor.axon.com/\u00e9','https://investor.axon.com/%FF','https://investor.axon.com/x?','https://investor.axon.com/x','https://investor.axon.com/a#part']
robots=[]
for text in rules:
    p=RobotFileParser();p.parse(text.splitlines());robots.append({'rules':text,'urls':[{'url':url,'allowed':p.can_fetch('*',url)} for url in urls]})
write(ROOT/'apps/backend-rust/tests/fixtures/ir-html-robots.json',canonical({'html':cases,'robots':robots}).encode())
print(f'Saved {len(cases)} legacy HTML/publication cases and {len(robots)*len(urls)} original robots decisions')
from urllib.parse import urljoin
edges=[]
for value in ['&notit; &amp= &amp; &#13; &#1; &#123=','\r\n  x &amp; y','&unknown; &not; &notit;','&#xFDD0; &#x10FFFF; &#x110000;']:
    source='<meta property="og:title" content="'+value+'"><h1>'+value+'</h1>'
    soup=BeautifulSoup(source,'html.parser');tags=[]
    for tag in soup.find_all(['meta','h1']):tags.append({'name':tag.name,'attrs':{k:v for k,v in tag.attrs.items() if k in ['href','name','property','content']},'text':tag.get_text(' ',strip=True),'raw_text':tag.get_text()})
    edges.append({'html':source,'tags':tags})
joins=[]
for base in ['https://investor.axon.com:443/press-releases/?a=1#old','https://www.motorolasolutions.com/a/b.html']:
    for target in ['','?','#','../c','/x//y','//investor.axon.com:443/a','https://investor.axon.com:443/a b','/\u00e9/2026-11-18-title','./','../../..','../x?y=#f']:
        joins.append({'base':base,'target':target,'result':urljoin(base,target)})
rule='User-agent: *\r\nDisallow: /a\r\nAllow: /\r\n';p=RobotFileParser();p.parse(rule.splitlines())
write(ROOT/'apps/backend-rust/tests/fixtures/ir-edges.json',canonical({'html':edges,'joins':joins,'robots':{'rules':rule,'urls':[{'url':url,'allowed':p.can_fetch('*',url)} for url in urls]}}).encode())
print('Saved attribute, raw-newline, URL-identity and CRLF robots edge cases')
boundaries=[]
for source in ["<h1>&#x1fffffe;</h1><meta property='og:title' content='&#x1fffffe;'>", "<h1>before<script><!--<script>x</script>after</h1>"]:
    soup=BeautifulSoup(source,'html.parser');tags=[]
    for tag in soup.find_all(['meta','h1']):tags.append({'name':tag.name,'attrs':{k:v for k,v in tag.attrs.items() if k in ['href','name','property','content']},'text':tag.get_text(' ',strip=True),'raw_text':tag.get_text()})
    boundaries.append({'html':source,'tags':tags})
write(ROOT/'apps/backend-rust/tests/fixtures/ir-boundaries.json',canonical({'html':boundaries}).encode())
print('Saved script escape and out-of-range numeric entity boundaries')
