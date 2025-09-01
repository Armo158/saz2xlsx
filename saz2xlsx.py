import argparse, os
from scripts.saz_parser import parse_saz_data
from scripts.filters import parse_banned_file, compile_ignores, filter_sessions
from scripts.excel_exporter import export_excel

def main():
    ap = argparse.ArgumentParser(description='Fiddler SAZ → 취약점 진단 엑셀 자동화 (패킷 순서 + KST 시각)')
    ap.add_argument('saz', help='입력 SAZ 파일 경로')
    ap.add_argument('-o','--output', default='saz_parsed.xlsx', help='출력 엑셀 파일 경로(.xlsx). 기본값: saz_parsed.xlsx')
    ap.add_argument('--banned-file', default=None, help='제외할 URL 목록 파일(한 줄당 prefix 또는 re:정규식)')
    ap.add_argument('--max-values', type=int, default=20, help='파라미터 값 최대 표기 개수')
    ap.add_argument('--base-url', type=str, default=None, help='엑셀 첫 행에 입력할 진단 대상 대표 URL')
    ap.add_argument('--include-time', action='store_true', help='엑셀에 "진단 시각" 컬럼을 포함할지 여부')
    ap.add_argument('--separate-by-url', action='store_true', help='URL별로 엑셀 시트를 분리하여 생성')

    # 메뉴 라벨링
    ap.add_argument('--menu-label', action='store_true', default=True, help='HTML에서 메뉴 후보를 수집하고 요청 URL에 메뉴명을 자동 매칭합니다.')
    ap.add_argument('--menu-threshold', type=float, default=58.0, help='메뉴 매칭 임계값(기본 58.0). 점수 미만이면 공란.')
    ap.add_argument('--menu-save-candidates', action='store_true', help='메뉴 후보 풀(JSON)을 결과 폴더에 저장합니다.')

    # 진행 표시
    ap.add_argument('-p','--progress', action='store_true', default=True, help='진행 상황(Progress) 표시')
    ap.add_argument('--progress-every', type=int, default=200, help='몇 건마다 업데이트할지 (기본 200)')

    # debug
    ap.add_argument('--debug', action='store_true', help='비고 컬럼명을 비고(매칭점수)로 바꾸고 매칭 점수를 기록합니다.')

    # python saz2xlsx.py Test.saz --menu-label -p --base-url https://www.cyberone.kr --banned-file ./banned.txt -o ./output/금일_일일보고.xlsx

    args = ap.parse_args()

    out_dir = os.path.dirname(os.path.abspath(args.output)) or '.'
    os.makedirs(out_dir, exist_ok=True)

    rows = parse_saz_data(
        args.saz,
        max_values=args.max_values,
        enable_menu_label=args.menu_label,
        menu_threshold=args.menu_threshold,
        save_menu_candidates=args.menu_save_candidates,
        outdir=out_dir,
        show_progress=args.progress,
        progress_every=args.progress_every,
        debug=args.debug
    )

    if args.banned_file:
        ban_pfx_raw, ban_rx_raw = parse_banned_file(args.banned_file)
        ban_pfx, ban_rx = compile_ignores(ban_pfx_raw, ban_rx_raw)
        rows = filter_sessions(rows, ban_pfx, ban_rx)

    export_excel(rows, args.output, args.base_url, args.include_time, args.separate_by_url, debug=args.debug)
    print(f"[+] 엑셀 파일 생성 완료: {args.output}")

if __name__ == '__main__':
    main()
