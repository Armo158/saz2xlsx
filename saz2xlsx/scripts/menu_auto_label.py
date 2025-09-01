#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
menu_auto_label.py

SAZ에서 HTML을 훑어 '메뉴 후보(라벨-URL)'를 수집하고,
요청 URL과 유사도 매칭하여 가장 근접한 메뉴명을 찾는 모듈.

공개 API:
    - build_candidate_pool_from_saz(zf, progress=None, progress_every=200) -> dict[str, list[tuple[str, str]]]
    - best_menu_for_url(url, pool, referer_url=None, threshold=58.0)
        -> (label|None, score: float, matched_candidate_url|None)

의존성: 표준 라이브러리만 사용
"""
from __future__ import annotations

import re
import json
import zipfile
from typing import Dict, List, Tuple, Optional, Callable
from urllib.parse import urlparse, urljoin
from difflib import SequenceMatcher
from html import unescape
from html.parser import HTMLParser

# ---------------------------------------------------------------------------
# 디코딩/공통 유틸
# ---------------------------------------------------------------------------

_TAG_STRIP = re.compile(r'<[^>]+>')
_WS = re.compile(r'\s+')

def _decode_bytes(b: bytes) -> str:
    """바이트를 관대하게 디코딩"""
    if b is None:
        return ""
    try:
        return b.decode('utf-8', 'ignore')
    except Exception:
        try:
            return b.decode('cp949', 'ignore')
        except Exception:
            return b.decode('latin-1', 'ignore')

def _decode_body(body: bytes, headers: Dict[str, str]) -> str:
    """Content-Type 헤더의 charset을 우선 적용"""
    ctype = (headers.get('Content-Type') or headers.get('content-type') or '')
    m = re.search(r'charset\s*=\s*([A-Za-z0-9._-]+)', ctype, re.I)
    if m:
        enc = m.group(1).lower()
        try:
            return body.decode(enc, 'ignore')
        except Exception:
            pass
    return _decode_bytes(body)

def _clean_text(html: str) -> str:
    """태그 제거 + 공백 정리 + HTML 엔티티 해제"""
    return unescape(_WS.sub(' ', _TAG_STRIP.sub('', html or '')).strip())

def _attr_get(attr_html: str, name: str) -> str:
    m = re.search(rf'{name}\s*=\s*(".*?"|\'.*?\'|[^\s>]+)', attr_html or '', re.I | re.S)
    if not m:
        return ''
    v = m.group(1)
    if v and (v[0] in ('"', "'") and v[-1] == v[0]):
        v = v[1:-1]
    return (v or '').strip()

def _resolve_candidate_url(candidate: str, current_url: str, html: str) -> str:
    """상대경로 -> 절대 URL. javascript:, # 은 제외. <base href> 고려."""
    if not candidate:
        return ''
    if candidate.startswith('javascript:') or candidate.startswith('#'):
        return ''
    m = re.search(r'<base[^>]+href=["\']([^"\']+)["\']', html or '', re.I)
    base = m.group(1).strip() if m else current_url
    return urljoin(base, candidate)

def _hostkey(host_or_url: str) -> str:
    """host 키 정규화: 기본 포트 제거, userinfo 제거"""
    netloc = urlparse(host_or_url).netloc or host_or_url
    netloc = netloc.lower()
    if '@' in netloc:
        netloc = netloc.split('@', 1)[-1]
    h, sep, p = netloc.partition(':')
    if p in ('80', '443'):
        return h
    return netloc or h

# '/path', 'http(s)://..', '//host/path', './rel', '../rel', '?q=1'까지 허용
_URLVAL = r'(?:(?:https?:)?//[^\'"\s]+|/[^\s\'"]+|\./[^\'"\s]+|\.\./[^\'"\s]+|\?[^\'"\s]+)'

# ---------------------------------------------------------------------------
# 요청/응답 라이트 파서
# ---------------------------------------------------------------------------

ParsedRequest = Tuple[str, str, Dict[str, str], bytes]
ParsedResponse = Tuple[Dict[str, str], bytes]

def _parse_request_bytes(data: bytes) -> ParsedRequest:
    head, sep, body = data.partition(b'\r\n\r\n')
    if not sep:
        head, sep, body = data.partition(b'\n\n')
    text = _decode_bytes(head)
    lines = text.splitlines()
    method, target = '', ''
    headers: Dict[str, str] = {}
    if lines:
        parts = lines[0].split()
        if len(parts) >= 2:
            method, target = parts[0], parts[1]
    for ln in lines[1:]:
        if not ln.strip():
            break
        if ':' in ln:
            k, v = ln.split(':', 1)
            headers[k.strip()] = v.strip()
    return (method or '', target or '', headers, body)  # type: ignore

def _parse_response_bytes(data: bytes) -> ParsedResponse:
    head, sep, body = data.partition(b'\r\n\r\n')
    if not sep:
        head, sep, body = data.partition(b'\n\n')
    text = _decode_bytes(head)
    headers: Dict[str, str] = {}
    for ln in text.splitlines():
        if ':' in ln:
            k, v = ln.split(':', 1)
            headers[k.strip()] = (v or '').strip()
    return (headers, body)  # type: ignore

def _content_type_is_html(headers: Dict[str, str]) -> bool:
    ctype = (headers.get('Content-Type') or headers.get('content-type') or '').lower()
    return ('text/html' in ctype) or ('application/xhtml+xml' in ctype)

# ---------------------------------------------------------------------------
# HTML에서 메뉴 후보(라벨-URL) 추출 - 기존 규칙
# ---------------------------------------------------------------------------

def _extract_anchor_candidates(html: str, current_url: str) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    for m in re.finditer(r'<a\b([^>]+)>(.*?)</a>', html or '', re.I | re.S):
        attrs = m.group(1)
        inner = m.group(2)
        href = _attr_get(attrs, 'href')
        label = _clean_text(inner) or _attr_get(attrs, 'title') or _attr_get(attrs, 'aria-label')
        if href and label:
            url = _resolve_candidate_url(href, current_url, html)
            if url:
                out.append((label.strip(), url))
    return out

# ---------------------------------------------------------------------------
# HTML에서 onclick에 입력된 함수 본문에서 URL 추출
# ---------------------------------------------------------------------------

# onclick="foo(...)" 형태의 함수 호출 탐지
_ONCLICK_FUNC_CALL = re.compile(
    r'onclick\s*=\s*["\']\s*([A-Za-z_$][\w$]*)\s*\([^"\']*?\)\s*["\']', re.I
)

# HTML 내 <script> 블록 추출
_SCRIPT_BLOCKS = re.compile(r'<script\b[^>]*>(.*?)</script>', re.I | re.S)

def _find_function_body(html: str, name: str) -> str:
    """inline <script>에서 function name(...) { ... } 등 본문을 찾아 반환"""
    if not html or not name:
        return ''
    src = html or ''
    body = ''
    # 전통 function 선언
    pat1 = re.compile(rf'function\s+{re.escape(name)}\s*\([^)]*\)\s*\{{(.*?)\}}', re.I | re.S)
    # 할당식: name = function(...) { ... }
    pat2 = re.compile(rf'{re.escape(name)}\s*=\s*function\s*\([^)]*\)\s*\{{(.*?)\}}', re.I | re.S)
    # 화살표: const/let/var name = (...) => { ... }
    pat3 = re.compile(rf'(?:const|let|var)\s+{re.escape(name)}\s*=\s*\([^)]*\)\s*=>\s*\{{(.*?)\}}', re.I | re.S)

    # script 블록 내에서만 탐색(오탐 줄이기)
    for m in _SCRIPT_BLOCKS.finditer(src):
        blk = m.group(1) or ''
        for pat in (pat1, pat2, pat3):
            mm = pat.search(blk)
            if mm:
                body = mm.group(1)
                return body
    return ''

def _extract_url_from_js_body(body: str) -> str:
    """함수 본문에서 네비게이션 URL 리터럴 추출"""
    if not body:
        return ''
    # location.href / assign / replace
    m = re.search(r'location\.(?:href|assign|replace)\s*=\s*["\']([^"\']+)["\']', body, re.I)
    if m: return m.group(1)
    # document.location
    m = re.search(r'document\.location\s*=\s*["\']([^"\']+)["\']', body, re.I)
    if m: return m.group(1)
    # window.open(url, ...)
    m = re.search(r'window\.open\s*\(\s*["\']([^"\']+)["\']', body, re.I)
    if m: return m.group(1)
    # form.action='...'; form.submit()
    m = re.search(r'\.action\s*=\s*["\']([^"\']+)["\']\s*;[^;]*?\.submit\s*\(', body, re.I | re.S)
    if m: return m.group(1)
    return ''

def _extract_onclick_function_candidates(html: str, current_url: str) -> List[Tuple[str, str]]:
    """onclick="func()" 앵커의 라벨과, func 본문에서 찾은 URL을 결합"""
    out: List[Tuple[str, str]] = []
    if not html:
        return out
    func_names = set(_ONCLICK_FUNC_CALL.findall(html))
    if not func_names:
        return out

    # 함수명 → URL 매핑(본문에서 한 번만 해석)
    fn_url: Dict[str, str] = {}
    for name in func_names:
        body = _find_function_body(html, name)
        url  = _extract_url_from_js_body(body)
        if url:
            fn_url[name] = _resolve_candidate_url(url, current_url, html)

    if not fn_url:
        return out

    # 해당 함수를 호출하는 앵커의 라벨을 붙여 최종 후보 생성
    for m in re.finditer(r'<a\b([^>]+)>(.*?)</a>', html or '', re.I | re.S):
        attrs = m.group(1) or ''
        inner = m.group(2) or ''
        onclick = _attr_get(attrs, 'onclick')
        if not onclick:
            continue
        for name, absurl in fn_url.items():
            if not absurl:
                continue
            if re.search(rf'\b{name}\s*\(', onclick):
                label = _clean_text(inner) or _attr_get(attrs, 'title') or _attr_get(attrs, 'aria-label')
                if label:
                    out.append((label.strip(), absurl))
    return out

def _extract_non_a_candidates(html: str, current_url: str) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []

    # onclick: location.href/assign/replace, window.open, router.push/navigate/go
    ONCLICK_PATS = [
        rf"onclick\s*=\s*['\"][^'\"]*?\blocation\.(?:href|assign|replace)\s*=\s*['\"]({_URLVAL})['\"][^'\"]*?['\"]",
        rf"onclick\s*=\s*['\"][^'\"]*?\bwindow\.open\s*\(\s*['\"]({_URLVAL})['\"][^'\"]*?['\"]",
        rf"onclick\s*=\s*['\"][^'\"]*?\b(?:router\.push|navigate|go)\s*\(\s*['\"]({_URLVAL})['\"][^'\"]*?['\"]",
    ]
    for pat in ONCLICK_PATS:
        for m in re.finditer(pat, html or '', re.I | re.S):
            snippet = (html or '')[max(0, m.start()-200): m.end()+200]
            tag = re.search(r'<([a-z0-9]+)[^>]*>(.*?)</\1>', snippet, re.I | re.S)
            label = _clean_text(tag.group(2))[:80] if tag else ''
            url = _resolve_candidate_url(m.group(1), current_url, html)
            if url and label:
                out.append((label, url))

    # data-url/route/href
    for m in re.finditer(rf'data-(?:url|route|href)\s*=\s*["\']({_URLVAL})["\']', html or '', re.I):
        snippet = (html or '')[max(0, m.start()-150): m.end()+150]
        tag = re.search(r'<([a-z0-9]+)[^>]*>(.*?)</\1>', snippet, re.I | re.S)
        label = _clean_text(tag.group(2))[:80] if tag else ''
        url = _resolve_candidate_url(m.group(1), current_url, html)
        if url and label:
            out.append((label, url))

    # form action / 버튼 formaction
    for m in re.finditer(rf'<form[^>]+action=["\']({_URLVAL})["\'][^>]*>(.*?)</form>', html or '', re.I | re.S):
        label = _clean_text(m.group(2))[:80]
        url = _resolve_candidate_url(m.group(1), current_url, html)
        if url and label:
            out.append((label, url))
    for m in re.finditer(rf'formaction=["\']({_URLVAL})["\']', html or '', re.I):
        sn = (html or '')[max(0, m.start()-150): m.end()+150]
        btn = re.search(r'<button[^>]*>(.*?)</button>', sn, re.I | re.S)
        label = _clean_text(btn.group(1))[:80] if btn else ''
        url = _resolve_candidate_url(m.group(1), current_url, html)
        if url and label:
            out.append((label, url))

    # select onchange=location.href=this.value
    for m in re.finditer(r'<select[^>]+onchange=["\'][^"\']*location\.href\s*=\s*this\.value[^"\']*["\']', html or '', re.I):
        block = re.search(r'<select[^>]*>(.*?)</select>', (html or '')[m.start():], re.I | re.S)
        if block:
            for opt in re.finditer(rf'<option[^>]+value=["\']({_URLVAL})["\'][^>]*>(.*?)</option>', block.group(1), re.I | re.S):
                label = _clean_text(opt.group(2))[:80]
                url = _resolve_candidate_url(opt.group(1), current_url, html)
                if url and label:
                    out.append((label, url))

    out.extend(_extract_onclick_function_candidates(html, current_url))
    return out

# ---------------------------------------------------------------------------
# 네비게이션 트리(상단메뉴 > 소메뉴) 파서
# ---------------------------------------------------------------------------

_MENU_HINTS = ('menu','nav','gnb','lnb','snb','submenu','depth','dropdown','tab','category','side','global','primary','secondary')
_BREAD_HINTS = ('breadcrumb','breadcrumbs','bread','path','location','loc')

def _attrs_to_dict(attrs) -> Dict[str, str]:
    return {k: (v or '') for k, v in (attrs or [])}

class _MenuNode:
    __slots__ = ('label','href','children')
    def __init__(self, label: Optional[str]=None, href: Optional[str]=None):
        self.label: Optional[str] = label
        self.href: Optional[str] = href
        self.children: List['_MenuNode'] = []

class _MenuTreeParser(HTMLParser):
    """UL/LI/A 구조에서 메뉴 트리 감지 (네비/서브메뉴/드롭다운 등)"""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _MenuNode()
        self.stack: List[_MenuNode] = [self.root]
        self.nav_depth = 0       # <nav> 안
        self.menu_ul_depth = 0   # class/id 힌트가 있는 <ul>/<ol> 안
        self.in_a = False
        self.a_text_buf: List[str] = []
        self.a_href: str = ''
        self.current_li_active = False

    def _is_menu_ul(self, attrs: Dict[str,str]) -> bool:
        klass = attrs.get('class','').lower()
        idv   = attrs.get('id','').lower()
        hay = f' {klass} {idv} '
        return any(k in hay for k in _MENU_HINTS) or any(b in hay for b in _BREAD_HINTS)

    def handle_starttag(self, tag, attrs):
        d = _attrs_to_dict(attrs)
        if tag == 'nav':
            self.nav_depth += 1
        elif tag in ('ul','ol'):
            if self._is_menu_ul(d):
                self.menu_ul_depth += 1
        elif tag == 'li':
            if self.nav_depth > 0 or self.menu_ul_depth > 0:
                node = _MenuNode()
                self.stack[-1].children.append(node)
                self.stack.append(node)
        elif tag == 'a':
            if self.nav_depth > 0 or self.menu_ul_depth > 0:
                self.in_a = True
                self.a_text_buf = []
                self.a_href = d.get('href','') or ''
        # 다른 태그는 무시

    def handle_data(self, data):
        if self.in_a and (self.nav_depth > 0 or self.menu_ul_depth > 0):
            self.a_text_buf.append(data)

    def handle_endtag(self, tag):
        if tag == 'a':
            if self.in_a and (self.nav_depth > 0 or self.menu_ul_depth > 0):
                label = _clean_text(''.join(self.a_text_buf))
                if label:
                    cur = self.stack[-1]
                    # 현재 li의 대표 라벨/링크로 세팅(비어 있을 때만)
                    if not cur.label:
                        cur.label = label
                    if not cur.href and self.a_href:
                        cur.href = self.a_href
            self.in_a = False
            self.a_text_buf = []
            self.a_href = ''
        elif tag == 'li':
            if len(self.stack) > 1 and (self.nav_depth > 0 or self.menu_ul_depth > 0):
                self.stack.pop()
        elif tag in ('ul','ol'):
            if self.menu_ul_depth > 0:
                self.menu_ul_depth -= 1
        elif tag == 'nav':
            if self.nav_depth > 0:
                self.nav_depth -= 1

def _flatten_menu_tree(root: _MenuNode, current_url: str, html: str) -> Tuple[List[Tuple[str,str]], Dict[Tuple[str,str], str]]:
    """
    트리를 DFS하여 (경로라벨, 절대URL) 리스트 생성
    leaf_map[(leaf_label, abs_url)] = 경로라벨 로 반환하여
    다른 extractor에서 동일 URL/라벨이 나오면 경로라벨로 치환할 수 있게 함.
    """
    results: List[Tuple[str,str]] = []
    leaf_map: Dict[Tuple[str,str], str] = []

    # leaf_map을 dict로 (타입 힌트 교정)
    leaf_map = {}

    def walk(node: _MenuNode, path_labels: List[str]):
        my_label = (node.label or '').strip()
        my_href  = (node.href or '').strip()
        new_path = path_labels + ([my_label] if my_label else [])

        if my_label and my_href:
            absurl = _resolve_candidate_url(my_href, current_url, html)
            if absurl:
                path_label = ' > '.join(f'[{x}]' for x in new_path)
                results.append((path_label, absurl))
                leaf_map[(my_label, absurl)] = path_label

        for ch in node.children:
            walk(ch, new_path)

    for ch in root.children:
        walk(ch, [])
    return results, leaf_map

def extract_menu_tree_candidates(html: str, current_url: str) -> Tuple[List[Tuple[str,str]], Dict[Tuple[str,str], str]]:
    """
    상단메뉴/소메뉴 등의 UL/LI/A 구조에서 계층 라벨 경로를 추출.
    예) [명의 헬스케어란?] > [프로그램 소개]
    """
    parser = _MenuTreeParser()
    try:
        parser.feed(html or '')
    except Exception:
        # HTML 깨진 경우 등, 실패해도 무시
        pass
    return _flatten_menu_tree(parser.root, current_url, html)

# ---------------------------------------------------------------------------
# 통합 후보 추출
# ---------------------------------------------------------------------------

def extract_menu_candidates_from_html(html: str, current_url: str) -> List[Tuple[str, str]]:
    """
    (label, absolute_url) 리스트
    - 고전 <a href> 후보
    - 비-<a> 클릭 후보(onclick/data-url/form/option 등)
    - onclick="func()" → <script>의 본문에서 URL 추출
    - 네비 트리(UL/LI/A)에서 [상위] > [하위] 경로라벨 후보
    """
    c: List[Tuple[str, str]] = []

    # 1) 네비 트리 우선 구축 (경로 라벨)
    tree_cands, leaf_map = extract_menu_tree_candidates(html, current_url)
    c.extend(tree_cands)

    # 2) 일반 앵커/논앵커 후보
    anchors = _extract_anchor_candidates(html, current_url)
    non_a   = _extract_non_a_candidates(html, current_url)

    # 3) 트리에서 파생된 leaf_map[(원래라벨, url)]이 있으면 경로라벨로 치환
    def _apply_pathlabel(items: List[Tuple[str,str]]) -> List[Tuple[str,str]]:
        out: List[Tuple[str,str]] = []
        for label, u in items:
            key = (label, u)
            if key in leaf_map:
                out.append((leaf_map[key], u))
            else:
                out.append((label, u))
        return out

    anchors = _apply_pathlabel(anchors)
    non_a   = _apply_pathlabel(non_a)

    c.extend(anchors)
    c.extend(non_a)

    # 4) 중복 제거
    seen = set()
    out: List[Tuple[str, str]] = []
    for label, u in c:
        k = (label, u)
        if k not in seen:
            seen.add(k)
            out.append((label, u))
    return out

# ---------------------------------------------------------------------------
# 경로 유사도
# ---------------------------------------------------------------------------

_NUM = re.compile(r'^\d+$')
_UUID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.I)
_HEX  = re.compile(r'^[0-9a-f]{8,}$', re.I)
_DATE = re.compile(r'^\d{4}[-/]?\d{2}([-/]?\d{2})?$')

def _norm_seg(seg: str) -> str:
    s = (seg or '').strip()
    if not s:
        return ''
    if _NUM.match(s) or _UUID.match(s) or _HEX.match(s) or _DATE.match(s):
        return '{id}'
    s = re.sub(r'\.(?:jsp|do|php|aspx|html?|cgi|action)$', '', s, flags=re.I)
    return s

def _path_segs(u: str) -> List[str]:
    p = urlparse(u or '')
    segs = [_norm_seg(x) for x in (p.path or '/').split('/') if _norm_seg(x)]
    return segs

def path_similarity(a_url: str, b_url: str) -> float:
    """0~100 점수. 세그먼트 겹침 + 접미 일치 + 문자열 유사도 종합."""
    if not a_url or not b_url:
        return 0.0
    if a_url == b_url:
        return 100.0

    a_segs, b_segs = _path_segs(a_url), _path_segs(b_url)
    if not a_segs and not b_segs:
        return 0.0

    # 세그먼트 Jaccard
    sa, sb = set(a_segs), set(b_segs)
    inter = len(sa & sb)
    union = max(1, len(sa | sb))
    jacc = inter / union  # 0~1

    # 마지막 세그먼트 일치
    last_match = 1.0 if (a_segs and b_segs and a_segs[-1] == b_segs[-1]) else 0.0

    # 접미 경로 포함
    a_path = '/'.join(a_segs); b_path = '/'.join(b_segs)
    suffix = 1.0 if (a_path.endswith(b_path) or b_path.endswith(a_path)) else 0.0

    # 문자열 유사도
    sm = SequenceMatcher(None, a_path, b_path).ratio()

    score = (jacc*40) + (last_match*20) + (suffix*15) + (sm*25)
    return float(min(100.0, score))

# ---------------------------------------------------------------------------
# 후보 풀 생성 & 매칭
# ---------------------------------------------------------------------------

def build_candidate_pool_from_saz(
    zf: zipfile.ZipFile,
    progress: Optional[Callable[[int, int], None]] = None,
    progress_every: int = 200
) -> Dict[str, List[Tuple[str, str]]]:
    """
    호스트별 후보 풀: { host: [(label, absolute_url), ...] }
    HTML(document) 응답을 대상으로 부모 페이지의 HTML에서 메뉴 후보를 추출
    progress(i, total): 진행 콜백 (선택)
    """
    pool: Dict[str, List[Tuple[str, str]]] = {}
    names = [n for n in zf.namelist() if n.startswith('raw/') and n.endswith('_s.txt')]
    total = len(names)
    for i, sname in enumerate(names, 1):
        try:
            # 응답 헤더 파싱
            resp_headers, resp_body = _parse_response_bytes(zf.read(sname))
            if not _content_type_is_html(resp_headers):
                continue

            # 대응하는 요청 읽기
            m = re.search(r'raw/(\d+)_s\.txt$', sname)
            if not m:
                continue
            mnum = m.group(1)
            reqn = f'raw/{mnum}_c.txt'
            if reqn not in zf.namelist():
                reqn2 = f'raw/{int(mnum):04d}_c.txt'
                if reqn2 in zf.namelist():
                    reqn = reqn2
                else:
                    continue

            method, target, headers, _ = _parse_request_bytes(zf.read(reqn))
            host = headers.get('Host') or headers.get('host') or ''
            if not host:
                continue

            # 스킴 추정: referer/origin 스킴 우선, 없으면 https 기본
            ref = headers.get('Referer') or headers.get('referer') or headers.get('Origin') or headers.get('origin') or ''
            scheme = urlparse(ref).scheme if ref.startswith('http') else ('https' if host.endswith(':443') else 'https')
            current_url = target if target.startswith('http') else f'{scheme}://{host}{target}'

            html = _decode_body(resp_body, resp_headers)
            cands = extract_menu_candidates_from_html(html, current_url)
            if not cands:
                continue

            key = _hostkey(current_url)
            pool.setdefault(key, [])
            pool[key].extend(cands)
        except Exception:
            # 개별 실패는 무시
            pass
        finally:
            if progress and (i == 1 or i % progress_every == 0 or i == total):
                progress(i, total)

    # 중복 제거
    for h in list(pool.keys()):
        seen = set()
        uniq: List[Tuple[str, str]] = []
        for label, u in pool[h]:
            k = (label, u)
            if k not in seen:
                seen.add(k); uniq.append((label, u))
        pool[h] = uniq
    return pool

def best_menu_for_url(url: str, pool: Dict[str, List[Tuple[str, str]]],
                      referer_url: Optional[str] = None,
                      threshold: float = 58.0) -> Tuple[Optional[str], float, Optional[str]]:
    """
    url과 가장 비슷한 후보를 반환: (label, score, matched_candidate_url)
    - 1순위: 동일 host 후보
    - 2순위: referer host 후보
    - threshold 미만이면 label=None
    """
    if not url:
        return None, 0.0, None
    hostk = _hostkey(url)
    candidates: List[Tuple[str, str]] = list(pool.get(hostk, []))

    if referer_url:
        rhostk = _hostkey(referer_url)
        if rhostk and rhostk != hostk and rhostk in pool:
            candidates.extend(pool[rhostk])

    if not candidates:
        return None, 0.0, None

    best_label, best_score, best_cand = None, 0.0, None
    tgt_segs = _path_segs(url)
    tgt_last = (tgt_segs[-1] if tgt_segs else '').lower()

    for label, cand_url in candidates:
        s = path_similarity(url, cand_url)
        # 라벨 보정: 경로라벨인 경우 마지막 토큰만 추출해서 비교
        lbl_last = ''
        if label:
            # [... ] > [... ] 형태에서 마지막 []만 추출
            m = re.findall(r'\[([^\]]+)\]', label)
            lbl_last = (m[-1] if m else label).lower()
        if tgt_last and lbl_last and tgt_last in lbl_last:
            s += 5  # 소폭 가점
        if s > best_score:
            best_label, best_score, best_cand = label, s, cand_url

    if best_score >= threshold:
        return best_label, best_score, best_cand
    return None, best_score, best_cand

# ---------------------------------------------------------------------------
# (선택) 스탠드얼론 사용: SAZ → 후보 풀 JSON 덤프
# ---------------------------------------------------------------------------

def _dump_candidates_json(saz_path: str, out_json: str) -> None:
    with zipfile.ZipFile(saz_path, 'r') as zf:
        pool = build_candidate_pool_from_saz(zf)
    with open(out_json, 'w', encoding='utf-8') as f:
        json.dump(pool, f, ensure_ascii=False, indent=2)

if __name__ == '__main__':
    import argparse, os
    ap = argparse.ArgumentParser(description='SAZ에서 메뉴 후보(라벨-URL) 풀 생성')
    ap.add_argument('saz', help='입력 SAZ 파일 경로')
    ap.add_argument('-o', '--out', default=None, help='저장할 JSON 경로(미지정 시 saz와 같은 폴더에 생성)')
    args = ap.parse_args()
    out = args.out
    if not out:
        base = os.path.dirname(os.path.abspath(args.saz))
        stem = os.path.splitext(os.path.basename(args.saz))[0]
        out = os.path.join(base, f'{stem}_menu_candidates.json')
    _dump_candidates_json(args.saz, out)
    print(f'[OK] 후보 풀 저장: {out}')
