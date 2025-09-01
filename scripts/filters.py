import io, os, re, zipfile
from typing import List, Tuple, Optional
from urllib.parse import urlparse

ASSET_EXTS = {'.js','.css','.map','.png','.jpg','.jpeg','.gif','.svg','.ico','.webp',
              '.woff','.woff2','.ttf','.eot','.otf','.pdf','.zip','.rar','.7z','.mp4','.mp3','.wav'}
ASSET_PATH_KWS = ['/js/', '/scripts/', '/static/', '/assets/', '/common/js/', '/fonts/']

def response_ctype(zf: zipfile.ZipFile, mnum: str) -> str:
    cand = [f'raw/{mnum}_s.txt', f'raw/{int(mnum):04d}_s.txt']
    for name in cand:
        if name in zf.namelist():
            try:
                head = zf.read(name).split(b'\r\n\r\n', 1)[0]
                txt = head.decode('latin-1', 'ignore').lower()
                for ln in txt.splitlines():
                    if ln.startswith('content-type:'):
                        return ln.split(':',1)[1].strip()
            except Exception:
                pass
    return ''

def is_probable_asset(url: str, zf: Optional[zipfile.ZipFile], mnum: Optional[str]) -> bool:
    p = urlparse(url)
    path = (p.path or '').lower()
    _, ext = os.path.splitext(path)
    if ext in ASSET_EXTS:
        return True
    if any(kw in path for kw in ASSET_PATH_KWS):
        return True
    if zf and mnum:
        ctype = response_ctype(zf, mnum)
        if ctype and (
            'javascript' in ctype or 'text/css' in ctype or 'image/' in ctype or
            'font/' in ctype or 'octet-stream' in ctype
        ):
            return True
    return False

def parse_banned_file(path: Optional[str]):
    prefixes = []
    regexes = []
    if not path:
        return prefixes, regexes
    try:
        with io.open(path, 'r', encoding='utf-8') as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith('#'):
                    continue
                if s.lower().startswith('re:'):
                    regexes.append(s[3:].strip())
                else:
                    prefixes.append(s)
    except Exception:
        pass
    return prefixes, regexes

def compile_ignores(prefixes: List[str], regexes: List[str]):
    norm_prefix = []
    for pfx in prefixes or []:
        p = pfx.strip()
        if not p or p.startswith('http') or not p.startswith('/'):
            continue
        norm_prefix.append(p)
    comp_regex = []
    for rx in regexes or []:
        try:
            comp_regex.append(re.compile(rx))
        except Exception:
            continue
    return norm_prefix, comp_regex

def should_ignore(url: str, prefixes, regexes) -> bool:
    p = urlparse(url)
    path = p.path or '/'
    for pf in prefixes:
        if path.startswith(pf):
            return True
    for rg in regexes:
        if rg.search(url):
            return True
    return False

def filter_sessions(rows, prefixes, regexes):
    out = []
    for item in rows:
        url = item['진단 URL']
        if should_ignore(url, prefixes, regexes):
            continue
        out.append(item)
    return out
