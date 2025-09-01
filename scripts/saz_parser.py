import os, re, json, zipfile
from typing import Dict, List, Tuple, Optional
from urllib.parse import urlparse
from scripts.http_utils import parse_request, extract_params, make_breadcrumb
from scripts.metadata_utils import is_marked_vulnerable_by_comment, extract_request_time_kst
from scripts.filters import is_probable_asset
from scripts.progress_utils import progress_start, progress_update, progress_end

try:
    from scripts.menu_auto_label import build_candidate_pool_from_saz, best_menu_for_url
    _MENU_LABEL_AVAILABLE = True
except Exception:
    _MENU_LABEL_AVAILABLE = False

def parse_saz_data(
    saz_path: str,
    max_values: int = 20,
    enable_menu_label: bool = False,
    menu_threshold: float = 58.0,
    save_menu_candidates: bool = False,
    outdir: Optional[str] = None,
    show_progress: bool = False,
    progress_every: int = 200,
    debug: bool = False
) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    try:
        with zipfile.ZipFile(saz_path, 'r') as zf:
            menu_pool = None
            if enable_menu_label and _MENU_LABEL_AVAILABLE:
                try:
                    if show_progress:
                        t0_pool = progress_start("메뉴 후보 수집")
                    def _pool_cb(i, total):
                        if show_progress:
                            progress_update("메뉴 후보 수집", i, total, t0_pool, every=progress_every)
                    menu_pool = build_candidate_pool_from_saz(zf, progress=_pool_cb, progress_every=progress_every)
                    if show_progress:
                        progress_end("메뉴 후보 수집", t0_pool)
                    if save_menu_candidates and outdir:
                        os.makedirs(outdir, exist_ok=True)
                        stem = os.path.splitext(os.path.basename(saz_path))[0]
                        out_json = os.path.join(outdir, f'{stem}_menu_candidates.json')
                        with open(out_json, 'w', encoding='utf-8') as f:
                            json.dump(menu_pool, f, ensure_ascii=False, indent=2)
                except Exception as e:
                    print(f"[WARN] 메뉴 후보 풀 생성 실패: {e}")
                    menu_pool = None

            req_members = sorted([m for m in zf.namelist() if m.startswith('raw/') and m.endswith('_c.txt')],
                                 key=lambda x: int(re.search(r'raw/(\d+)_c\.txt$', x).group(1)))
            total_req = len(req_members)
            t0_req = progress_start("요청 처리") if show_progress else None

            for idx, reqm in enumerate(req_members, 1):
                mnum_m = re.search(r'raw/(\d+)_c\.txt$', reqm)
                if not mnum_m:
                    continue
                mnum = mnum_m.group(1)
                try:
                    data = zf.read(reqm)
                    method, target, headers, body = parse_request(data)
                    if (method or '').upper() == 'CONNECT' or not target:
                        continue
                    host = headers.get('Host') or headers.get('host')
                    url = target if target.startswith('http') else 'https://' + (host or '') + target
                    referer = headers.get('Referer') or headers.get('referer') or ''

                    if is_probable_asset(url, zf, mnum):
                        pass
                    else:
                        params = extract_params(headers, target, body)
                        parts = []
                        for k, vals in params.items():
                            shown = vals[:max_values]
                            more = f" (외 {len(vals)-max_values}건)" if len(vals) > max_values else ''
                            parts.append(f"{k} = [{', '.join(shown)}]{more}")
                        param_field = '; '.join(parts)

                        is_vulnerable = is_marked_vulnerable_by_comment(zf, mnum)
                        result = '취약' if is_vulnerable else '양호'

                        menu_label, match_score = '', ''
                        if enable_menu_label and _MENU_LABEL_AVAILABLE and menu_pool:
                            try:
                                lbl, score, _ = best_menu_for_url(url, menu_pool, referer_url=referer, threshold=menu_threshold)
                                menu_label = lbl or ''
                                match_score = f"{score:.1f}" if score else ''
                            except Exception as e:
                                print(f"[WARN] 메뉴 매칭 실패(세션 {mnum}): {e}")

                        ts_kst = extract_request_time_kst(zf, mnum) or ''

                        breadcrumb_or_menu = menu_label if menu_label else (make_breadcrumb(url) if debug else '')
                        memo_val = match_score if (match_score and debug) else ''

                        rows.append({
                            '경로': breadcrumb_or_menu,
                            'Method': method,
                            '진단 URL': url,
                            '메뉴명(추정)': menu_label,
                            '매칭점수': match_score,
                            '파라미터': param_field,
                            '진단결과': result,
                            '진단 시각': ts_kst,
                            '비고': memo_val if debug else ''
                        })
                except Exception as e:
                    print(f"세션 {mnum} 처리 중 오류 발생: {e}")
                    continue

                if show_progress and t0_req is not None:
                    progress_update("요청 처리", idx, total_req, t0_req, every=progress_every)

            if show_progress and t0_req is not None:
                progress_end("요청 처리", t0_req)
    except Exception as e:
        print(f"SAZ 파일 읽기 오류: {e}")
        return []
    return rows
