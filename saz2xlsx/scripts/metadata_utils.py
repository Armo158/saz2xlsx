import json, xml.etree.ElementTree as ET, zipfile
from typing import Optional
from scripts.http_utils import ILLEGAL_XLS_RE, parse_iso_to_kst

COMMENT_VULN_MARKERS = ('취약', '[취약]', 'vuln', 'vulnerable', '漏洞')

def is_marked_vulnerable_by_comment(zf: zipfile.ZipFile, mnum: str) -> bool:
    candidates = [f'raw/{mnum}_m.xml', f'raw/{int(mnum):04d}_m.xml']
    meta_path = next((p for p in candidates if p in zf.namelist()), None)
    if not meta_path:
        return False
    try:
        raw = zf.read(meta_path)
    except KeyError:
        return False
    text = raw.decode('utf-8', 'ignore')
    text = ILLEGAL_XLS_RE.sub('', text)
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return False
    comment = ''
    for e in root.iter('SessionFlag'):
        if e.attrib.get('N') == 'ui-comments':
            comment = e.attrib.get('V', '') or ''
            break
    if not comment:
        return False
    c = comment.lower()
    return any(m.lower() in c for m in COMMENT_VULN_MARKERS)

def extract_request_time_kst(zf: zipfile.ZipFile, mnum: str) -> Optional[str]:
    xml_meta_member = f'raw/{mnum}_m.xml'
    if xml_meta_member in zf.namelist():
        try:
            meta_bytes = zf.read(xml_meta_member)
            root = ET.fromstring(meta_bytes)
            st = root.find('.//SessionTimers')
            if st is not None:
                val = st.attrib.get('ClientBeginRequest') or st.attrib.get('ClientDoneRequest')
                if val:
                    ts = parse_iso_to_kst(val)
                    if ts:
                        return ts
        except (zipfile.BadZipFile, ET.ParseError):
            pass
    json_meta_member = f'raw/{mnum}_m.json'
    if json_meta_member in zf.namelist():
        try:
            meta_data = zf.read(json_meta_member)
            meta = json.loads(meta_data.decode('utf-8'))
            utc_time_str = meta.get('Times', {}).get('ClientConnected')
            if utc_time_str:
                ts = parse_iso_to_kst(utc_time_str)
                if ts:
                    return ts
        except Exception:
            pass
    return None
