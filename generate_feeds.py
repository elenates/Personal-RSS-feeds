from __future__ import annotations
import hashlib, re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import urljoin
from xml.etree.ElementTree import Element, SubElement, ElementTree
import requests
from bs4 import BeautifulSoup
from dateutil import parser as date_parser

UA='PersonalRSSFeeds/1.0 (+https://github.com/)'

@dataclass
class Item:
    title:str; url:str; description:str=''; date:datetime|None=None


def soup(url):
    r=requests.get(url,headers={'User-Agent':UA,'Accept-Language':'cs,en;q=0.8'},timeout=30)
    r.raise_for_status(); return BeautifulSoup(r.text,'html.parser')

def clean(x): return re.sub(r'\s+',' ',x or '').strip()

def dt(x):
    if not x:return None
    try:
        d=date_parser.parse(x,dayfirst=True,fuzzy=True)
        return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)
    except: return None

def guid(url): return hashlib.sha256(url.encode()).hexdigest()

def add(out,title,url,desc='',date_text=''):
    title=clean(title); url=url.strip()
    if not title or not url or url.startswith('#') or url.lower().startswith('javascript:'):return
    if any(x.url==url for x in out):return
    out.append(Item(title,url,clean(desc),dt(date_text)))

def nearby_text(tag, limit=8):
    node=tag; parts=[]
    for _ in range(limit):
        node=node.find_next()
        if not node:break
        t=clean(node.get_text(' ',strip=True)) if hasattr(node,'get_text') else ''
        if t:parts.append(t)
    return ' '.join(parts)

def mzv():
    base='https://mzv.gov.cz/jnp/cz/informace_pro_cizince/aktuality/index.html'; s=soup(base); out=[]
    # MZV's current HTML is heading/list based. Accept article links under the
    # Aktuality section and use nearby visible date text.
    for a in s.find_all('a',href=True):
        href=urljoin(base,a['href']); title=clean(a.get_text(' ',strip=True))
        if not title or href==base or 'mzv.gov.cz' not in href:continue
        if any(x in title.lower() for x in ['více','menu','kontakt','domů']):continue
        context=nearby_text(a,5)
        m=re.search(r'\d{1,2}\.\s*\d{1,2}\.\s*\d{4}(?:\s*/\s*\d{1,2}:\d{2})?',context)
        if m and len(title)>10:add(out,title,href,context[:1500],m.group())
    return 'MZV - Aktuality pro cizince',base,out[:50]

def tmbk():
    base='https://www.seznamzpravy.cz/autor/tmbk-1312'; s=soup(base); out=[]
    for a in s.find_all('a',href=True):
        title=clean(a.get_text(' ',strip=True)); href=urljoin(base,a['href'])
        if not title.startswith('TMBK:') or 'seznamzpravy.cz' not in href:continue
        context=nearby_text(a,5); m=re.search(r'\d{1,2}\.\s*\d{1,2}\.\s*\d{4}\s+\d{1,2}:\d{2}',context)
        add(out,title,href,'',m.group() if m else '')
    return 'TMBK - Seznam Zprávy',base,out[:50]

def skalni():
    base='https://www.skalnimlyn.cz/akce-a-novinky'; s=soup(base); out=[]
    marker=next((h for h in s.find_all(['h2','h3']) if 'Nadcházející akce' in clean(h.get_text())),None)
    scope=marker.parent if marker else s
    for h in scope.find_all(['h3','h4']):
        title=clean(h.get_text(' ',strip=True))
        if not title or title.lower() in {'nadcházející akce','novinky'}:continue
        context=nearby_text(h,12); link=None
        for a in h.find_all_next('a',href=True,limit=8):
            href=urljoin(base,a['href'])
            if href!=base and 'akce-a-novinky' not in href: link=href;break
        if not link:continue
        m=re.search(r'\d{1,2}\.\s*(?:\d{1,2}\.|\w+)',context)
        add(out,title,link,context[:2000],m.group() if m else '')
    return 'Skalní mlýn - Nadcházející akce',base,out[:50]

def energie():
    base='https://www.svetenergie.cz/cs/kalendar-akci'; s=soup(base); out=[]
    for a in s.find_all('a',href=True):
        if clean(a.get_text(' ',strip=True)).upper()!='ZJISTIT VÍCE':continue
        href=urljoin(base,a['href']); card=a
        for _ in range(6):
            card=card.parent
            if not card:break
            if len(clean(card.get_text(' ',strip=True)))>100:break
        if not card:continue
        title=''
        for h in card.find_all(['h2','h3','h4']):
            title=clean(h.get_text(' ',strip=True))
            if title:break
        text=clean(card.get_text(' ',strip=True)).replace('ZJISTIT VÍCE','').strip()
        if not title:title=text[:160]
        m=re.search(r'\d{1,2}\.\s*\d{1,2}\.\s*[-–]\s*\d{1,2}\.\s*\d{1,2}\.\s*\d{4}|\d{1,2}\.\s*[-–]\s*\d{1,2}\.\s*\d{1,2}\.\s*\d{4}|\d{1,2}\.\s*\d{1,2}\.\s*\d{4}',text)
        add(out,title,href,text[:2500],m.group() if m else '')
    return 'Svět energie - Kalendář akcí',base,out[:50]

def write(name,title,source,items):
    items=sorted(items,key=lambda x:x.date or datetime.min.replace(tzinfo=timezone.utc),reverse=True)
    root=Element('rss',{'version':'2.0'}); ch=SubElement(root,'channel')
    for tag,val in [('title',title),('link',source),('description','Generated personal RSS feed'),('generator','Personal RSS Feeds')]:SubElement(ch,tag).text=val
    for x in items:
        e=SubElement(ch,'item');SubElement(e,'title').text=x.title;SubElement(e,'link').text=x.url;SubElement(e,'guid',{'isPermaLink':'false'}).text=guid(x.url)
        if x.description:SubElement(e,'description').text=x.description
        if x.date:SubElement(e,'pubDate').text=format_datetime(x.date)
    p=Path('docs/feeds')/f'{name}.xml';p.parent.mkdir(parents=True,exist_ok=True);ElementTree(root).write(p,encoding='utf-8',xml_declaration=True)

def main():
    for name,fn in [('mzv',mzv),('tmbk',tmbk),('skalni-mlyn',skalni),('svet-energie',energie)]:
        title,src,items=fn();print(name,len(items));write(name,title,src,items)
if __name__=='__main__':main()
