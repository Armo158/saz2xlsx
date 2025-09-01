import re, json
from typing import Dict, List, Tuple, Optional
from urllib.parse import urlparse, parse_qsl
from datetime import datetime
from zoneinfo import ZoneInfo

ILLEGAL_XLS_RE = re.compile(r'[\x00-\x08\x0B\x0C\x0E-\x1F]')
KST = ZoneInfo("Asia/Seoul")

def sanitize_excel_str(x):
    if x is None:
        return None
    if not isinstance(x, str):
        try:
            x = str(x)
        except Exception:
            return ''
    return ILLEGAL_XLS_RE.sub('', x)

def parse_request(raw: bytes) -> Tuple[str, str, Dict[str, str], bytes]:
    try:
        head, body = raw.split(b"\r\n\r\n", 1)
    except ValueError:
        head, body = raw, b""
    lines = head.split(b"\r\n")
    if not lines:
        return "", "", {}, b""
    reqline = lines[0].decode('latin-1', errors='ignore')
    parts = reqline.split()
    method, target = (parts[0], parts[1]) if len(parts) >= 2 else ("", "")
    headers: Dict[str, str] = {}
    for ln in lines[1:]:
        try:
            s = ln.decode('latin-1', errors='ignore')
            if not s or ':' not in s:
                continue
            k, v = s.split(':', 1)
            headers[k.strip()] = v.strip()
        except Exception:
            continue
    return method, target, headers, body

def flatten_json(obj, parent_key: str = '', sep: str = '.') -> Dict[str, object]:
    items: Dict[str, object] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            new_key = f"{parent_key}{sep}{k}" if parent_key else str(k)
            items.update(flatten_json(v, new_key, sep))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            new_key = f"{parent_key}[{i}]" if parent_key else f"[{i}]"
            items.update(flatten_json(v, new_key, sep))
    else:
        items[parent_key] = obj
    return items

def extract_params(headers: Dict[str, str], target: str, body: bytes) -> Dict[str, List[str]]:
    from collections import defaultdict
    params: Dict[str, List[str]] = defaultdict(list)
    if '?' in target:
        qs = target.split('?', 1)[1]
        for k, v in parse_qsl(qs, keep_blank_values=True):
            if v not in params[k]:
                params[k].append(v)
    ctype = (headers.get('content-type') or headers.get('Content-Type') or '').lower()
    if ctype.startswith('application/x-www-form-urlencoded'):
        try:
            s = body.decode('utf-8', errors='ignore')
            for k, v in parse_qsl(s, keep_blank_values=True):
                if v not in params[k]:
                    params[k].append(v)
        except Exception:
            pass
    elif 'json' in ctype:
        try:
            text = body.decode('utf-8', errors='ignore')
            obj = json.loads(text)
            flat = flatten_json(obj)
            for k, v in flat.items():
                sval = str(v)
                if sval not in params[k]:
                    params[k].append(sval)
        except Exception:
            pass
    return params

def make_breadcrumb(url: str) -> str:
    p = urlparse(url)
    segs = [s for s in (p.path or '/').split('/') if s]
    if not segs:
        return "[메인 페이지]"
    return ' > '.join(f"[{s}]" for s in segs[:5])

def parse_iso_to_kst(s: str) -> Optional[str]:
    s = s.strip()
    try:
        if '.' in s and len(s.split('.')[1].split('+')[0].split('-')[0].strip()) == 7:
            s = s[:-1]
        dt = None
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            if s.endswith('Z'):
                dt = datetime.fromisoformat(s[:-1] + '+00:00')
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("UTC"))
        kst = dt.astimezone(KST)
        return kst.strftime('%Y-%m-%d %H:%M:%S')
    except Exception:
        return None
