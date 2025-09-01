# 📘 SAZ2XLSX 도구

## 소개
이 도구는 **Fiddler SAZ 파일을 분석하여 Excel 리포트로 변환**해주는 Python 기반 툴입니다.  
웹 보안 점검·취약점 진단 보고서 작성 시 반복적인 패킷 분석 작업을 자동화하도록 설계되었습니다.

## 해당 코드 실행 시 SAZ 파일 생성
- 진단 시, 패킷을 선택 후 마우스 우클릭 > Comment(단축키 m)을 입력하여 '취약', 'vuln', 또는 'vulnerable'이라고 입력합니다.
- 위와 같이 comment를 입력 하게 되면 엑셀 생성 시 진단 결과가 '취약'으로 자동으로 입력됩니다. 그러하지 않은 경우는 모두 양호로 입력됩니다.
- Fiddler > File > Save > All Sessions 를 이용하여 파일을 저장합니다.
- 해당 .saz 파일을 saz2xlsx.py와 같은 디렉토리에 저장합니다.

## 주요 기능

### 1. SAZ 파일 파싱
- 요청(Request)와 응답(Response)을 자동으로 파싱
- URL, HTTP 메서드, 파라미터, 요청 시간, 코멘트 기반 취약/양호 여부 추출
- `CONNECT` 메서드, 정적 자산(js/css/png 등)은 자동으로 제외 처리

### 2. 메뉴명 자동 라벨링
- `--menu-label` 옵션 사용 시(디폴트) HTML 내 메뉴 구조를 파싱해 후보를 수집
- 요청 URL과 후보 메뉴를 유사도 기반으로 매칭
- 임계값(`--menu-threshold`) 이하일 경우 메뉴명은 공란으로 처리
- 디버그 모드(`--debug`)에서만 매칭 점수와 추정 메뉴명이 노출됨

### 3. 엑셀 리포트 생성
- 표준 서식 적용된 `.xlsx` 파일 출력
- 옵션:
  - `--include-time`: 요청 시각 컬럼 포함
  - `--separate-by-url`: 도메인별 시트 분리
  - `--base-url`: 대표 URL을 첫 행에 표시
- 컬럼 구성
  - 기본: 경로, Method, 진단 URL, 파라미터, 진단결과, 비고
  - 디버그 모드: `메뉴명(추정)`, `매칭점수`, `비고(매칭점수)` 추가

### 4. URL 제외 기능
- `--banned-file` 옵션으로 제외할 URL을 관리 가능
- 지원 형식:
  - prefix (예: `/common`)
  - 정규식 (예: `re:^/test/.*`)

### 5. 진행 표시
- 대량의 세션 처리 시 진행률, ETA 표시
- `-p` 또는 `--progress` 옵션 사용
- `--progress-every`로 업데이트 주기 설정 가능 (기본 200개)

---

## 설치 및 실행

### 1) 의존성
```bash
pip install pandas openpyxl
```

### 2) 실행 예시
```bash
# 기본 실행
python saz2xlsx.py input.saz -o report.xlsx

# 도메인별 시트 분리 + 시각 표시
python saz2xlsx.py input.saz --include-time --separate-by-url -o report_split.xlsx
```

---

## 프로젝트 구조

```
project/
 ├─ saz2xlsx.py           # 메인 CLI 엔트리포인트
 └─ scripts/              # 모듈 모음
     ├─ saz_parser.py     # SAZ 파일 파서 (행 데이터 생성)
     ├─ excel_exporter.py # Excel 출력/서식
     ├─ http_utils.py     # HTTP 요청 파싱, 파라미터 추출, 문자열 정화
     ├─ metadata_utils.py # Fiddler 메타데이터 분석 (코멘트, 시간)
     ├─ filters.py        # 정적 자산 필터링, banned 리스트 관리
     ├─ progress_utils.py # 진행 표시/ETA 계산
     └─ menu_auto_label.py (선택) # 메뉴 후보 수집 및 매칭 로직
```

---

## 유지보수/개발자용 정리

### 1. `saz_parser.py`
- 책임: SAZ(zip) 열고 `_c.txt` 요청 파싱 → 행 딕셔너리 생성
- 주요 함수:
  - `parse_saz_data(...)`
- 내부에서 사용하는 모듈:
  - `http_utils` (요청/파라미터 처리)
  - `metadata_utils` (취약/시간 추출)
  - `filters` (정적 자산 필터)
  - `menu_auto_label` (옵션)

### 2. `excel_exporter.py`
- 책임: DataFrame → Excel(.xlsx) 변환
- 기능:
  - 시트별 분리/서식
  - 컬럼 width 자동 조정
- 디버그 모드 여부에 따라 컬럼 구성이 달라짐

### 3. `http_utils.py`
- 책임: HTTP 요청/응답 관련 유틸
- 기능:
  - `parse_request`: method, target, headers, body 추출
  - `extract_params`: URL/폼/JSON 파라미터 추출
  - `make_breadcrumb`: URL → [path] > [path] 변환
  - `sanitize_excel_str`: Excel 금지 문자 제거
  - `parse_iso_to_kst`: ISO → KST 시각 변환

### 4. `metadata_utils.py`
- 책임: `_m.xml`, `_m.json`에서 메타데이터 분석
- 기능:
  - `is_marked_vulnerable_by_comment`: 코멘트 키워드 기반 취약 여부 판정
  - `extract_request_time_kst`: 요청 시각 추출

### 5. `filters.py`
- 책임: 요청 제외 로직
- 기능:
  - `is_probable_asset`: js/css/png 등 정적 자산 자동 제외
  - `parse_banned_file`, `compile_ignores`, `filter_sessions`

### 6. `progress_utils.py`
- 책임: CLI 진행 표시
- 함수:
  - `progress_start`
  - `progress_update`
  - `progress_end`

### 7. `menu_auto_label.py` 
- 책임: HTML 내 메뉴 구조 추출 & 요청 URL과 매칭
- 함수:
  - `build_candidate_pool_from_saz`
  - `best_menu_for_url`
