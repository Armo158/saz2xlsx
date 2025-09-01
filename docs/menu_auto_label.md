# menu_auto_label: 유지보수 가이드

_Last updated: 2025-08-30 (KST)_

SAZ(HTTP 캡처)에서 HTML을 훑어 **메뉴 후보(라벨·URL)**를 수집하고, 특정 URL에 대해 가장 그럴듯한 메뉴 라벨(필요 시 **`[상위] > [하위]`** 경로 라벨)을 산출하는 모듈입니다.  
외부 의존성 없이 **표준 라이브러리만** 사용합니다.

---

## 목차
- [개요](#개요)
- [폴더 구조](#폴더-구조)
- [빠른 시작](#빠른-시작)
- [공개 API](#공개-api)
- [전체 동작 흐름](#전체-동작-흐름)
- [핵심 구성요소](#핵심-구성요소)
- [후보 추출 파이프라인](#후보-추출-파이프라인)
- [네비게이션 트리 파서(경로 라벨)](#네비게이션-트리-파서경로-라벨)
- [유사도 계산 및 매칭](#유사도-계산-및-매칭)
- [설정/정규식 튜닝 포인트](#설정정규식-튜닝-포인트)
- [에러 처리 & 안정성](#에러-처리--안정성)
- [성능 노트](#성능-노트)
- [saz2xlsx 통합 가이드](#saz2xlsx-통합-가이드)
- [테스트 시나리오](#테스트-시나리오)
- [체크리스트](#체크리스트)
- [로드맵](#로드맵)
- [변경 이력](#변경-이력)

---

## 개요

- **목적**: SAZ에서 HTML 문서를 분석하여 메뉴 후보(라벨·URL)를 수집하고, 특정 URL에 대해 가장 적합한 메뉴 라벨을 산출합니다.  
- **특징**
  - `UL/LI/A` 기반 네비게이션 트리에서 **경로 라벨**(`[부모] > [자식]`) 자동 생성
  - `onclick="foo()"` 형태의 함수 본문에서 **실제 이동 URL** 추출
  - `data-url|route|href`, `form action`, `button formaction`, `select onchange` 등 **비-앵커 요소** 지원
  - 한글/CP949 대응을 위한 **charset 우선 디코딩**

---

## 폴더 구조

```
.
├─ saz2xlsx.py
├─ scripts/
│  ├─ menu_auto_label.py          # ← 본 모듈
│  ├─ saz_parser.py
│  ├─ excel_exporter.py
│  ├─ http_utils.py
│  ├─ filters.py
│  └─ progress_utils.py
├─ docs/
│  └─ menu_auto_label.md          # ← 이 파일
```

상대 링크:
- [saz2xlsx.py](../saz2xlsx.py)
- [scripts/menu_auto_label.py](../scripts/menu_auto_label.py)
- [scripts/excel_exporter.py](../scripts/excel_exporter.py)

> 실제 저장 경로는 프로젝트에 맞춰 조정하세요.

---

## 빠른 시작

```bash
python - <<'PY'
import zipfile, json
from scripts.menu_auto_label import build_candidate_pool_from_saz, best_menu_for_url

saz_path = "examples/sample.saz"
with zipfile.ZipFile(saz_path, "r") as zf:
    pool = build_candidate_pool_from_saz(zf)

url = "https://ex.com/about/program"
label, score, matched = best_menu_for_url(url, pool, referer_url=None)
print(label, score, matched)
PY
```

예상 출력(예시):  
`[게시판] > [하위 게시판] 82.3 https://ex.com/about/program`

---

## 공개 API

### `build_candidate_pool_from_saz(zf, progress=None, progress_every=200) -> dict[str, list[tuple[str,str]]]`

- **입력**: `zipfile.ZipFile` (SAZ opened)  
- **동작**: `raw/*_s.txt` 응답 중 `text/html`/`application/xhtml+xml` 문서를 골라, 부모 페이지 HTML에서 **후보(라벨, 절대URL)** 리스트를 수집하여 **호스트 키**별로 모읍니다.  
- **반환**: `{ host_key: [(label, absolute_url), ...] }`  
  - `host_key`는 기본 포트(80/443) 제거 및 소문자 변환을 거친 값입니다.

### `best_menu_for_url(url, pool, referer_url=None, threshold=58.0) -> (label|None, score: float, matched_url|None)`

- **입력**
  - `url`: 매칭 대상 URL
  - `pool`: 상기 `build_*` 결과
  - `referer_url`: (선택) 참조 호스트 후보까지 확장
  - `threshold`: 점수 커트라인(이상일 때만 라벨 확정)
- **반환**
  - `label`: 최적 라벨(경로 라벨 포함 가능) 또는 `None`
  - `score`: 0~100
  - `matched_url`: 매칭에 사용된 후보 URL

---

## 전체 동작 흐름

1. **SAZ 순회**: `raw/*_s.txt`(응답)에서 HTML 문서만 대상 선택  
2. **요청 매칭**: 대응 `raw/*_c.txt`에서 `Host/Referer/Origin`으로 `current_url` 구성  
3. **바디 디코딩**: `charset` 우선, 실패 시 `utf-8 → cp949 → latin-1`  
4. **후보 추출**: (A) 네비 트리 → (B) `<a href>` → (C) 비-앵커 → (D) `onclick="func()"` 본문  
5. **경로 라벨 치환**: 트리로부터 얻은 `leaf_map`을 B/C 결과에 적용  
6. **중복 제거**: `(label, absolute_url)` 기준 유니크  
7. **호스트 키 정규화**: `example.com:443 → example.com`  
8. **매칭**: `best_menu_for_url`로 최적 라벨 산출

---

## 핵심 구성요소

- **요청/응답 파서**
  - `_parse_request_bytes`, `_parse_response_bytes`
  - `_content_type_is_html`, `_decode_body`
- **URL/호스트 유틸**
  - `_resolve_candidate_url` : `<base href>` 고려, 상대/절대/쿼리-only → 절대 URL
  - `_hostkey` : 기본 포트 및 userinfo 제거, 소문자화
- **클린업**
  - `_clean_text` : 태그 제거, 공백 정규화, HTML 엔티티 해제

---

## 후보 추출 파이프라인

1. **네비 트리 우선**: `UL/LI/A` 구조에서 **경로 라벨** 생성  
2. **일반 앵커**: `<a href="...">라벨</a>`  
3. **비-앵커**:  
   - `onclick="location.href='...'"`, `window.open('...')`, `router.push('...')`
   - `data-url|route|href="..."`
   - `<form action="...">`, `button formaction="..."`
   - `<select onchange="location.href=this.value"><option value="...">...</option>`  
4. **함수 본문 해석**: `onclick="foo()"` → `<script>` 내 `foo`를 찾아 URL 리터럴 추출  
5. **경로 라벨 치환**: (1)에서 얻은 `leaf_map[(leaf_label, url)] → 경로 라벨`을 2·3·4 결과에 적용

> URL 리터럴 캡처는 `_URLVAL`(절대/상대/쿼리-only 포함)로 통일합니다.

---

## 네비게이션 트리 파서(경로 라벨)

- `HTMLParser` 기반 `_MenuTreeParser` 사용
- `nav` 내부 또는 **class/id 힌트**를 가진 `ul/ol`만 추적
  - 힌트: `menu, nav, gnb, lnb, snb, submenu, depth, dropdown, tab, category, side, global, primary, secondary`  
  - breadcrumb 힌트: `breadcrumb, breadcrumbs, bread, path, location, loc`
- `li > a`의 텍스트/링크를 **노드**로 적재하고 DFS로 **경로 라벨** 생성  
  - 예: `[게시판] > [하위 게시판]`

**튜닝 포인트**
- 힌트 키워드는 `_MENU_HINTS`, `_BREAD_HINTS`에서 관리
- 경로 깊이를 제한하고 싶다면 `_flatten_menu_tree` 내 `new_path`를 슬라이싱

---

## 유사도 계산 및 매칭

### 정규화
- 세그먼트에서 숫자/UUID/HEX/날짜는 `{id}` 토큰으로 치환
- 확장자 제거: `jsp|do|php|aspx|html?|cgi|action`

### 점수(0~100)
- **Jaccard(40)**: 정규화된 경로 세그먼트 집합의 교집합/합집합
- **마지막 세그먼트 일치(20)**
- **접미 경로 포함(15)**
- **문자열 유사도(25)**: `SequenceMatcher` ratio

### 가점
- 라벨이 경로 라벨인 경우 마지막 토큰(`[... > [X]]`)이 URL 마지막 세그먼트와 겹치면 **+5**

---

## 설정/정규식 튜닝 포인트

- **`_URLVAL`**: URL 리터럴 포괄 패턴(절대/상대/쿼리-only 포함)  
- **`_MENU_HINTS`, `_BREAD_HINTS`**: 사이트 특성에 맞춰 추가/제거
- **확장자 스트립 목록**: 필요 시 프레임워크별 확장자 추가

> 대부분의 **오탐/누락**은 이 3곳을 조정해 해결됩니다.

---

## 에러 처리 & 안정성

- SAZ 레코드 단위 `try/except`: **부분 실패 무시**
- HTML 파서 예외 무시(깨진 HTML 허용)
- 디코딩 실패 시 관대한 폴백
- `(label, url)` 중복 제거로 노이즈 억제

---

## 성능 노트

- SAZ 문서 수 `N`, 문서당 후보 수 `C`일 때
  - 후보 수집: O(N·C)
  - 매칭: URL 1개당 O(C_host)
- 대용량 대응 팁
  - `progress_every` 조정
  - 후보 풀 후처리로 동일 URL에 대해 **가장 긴 라벨만 유지**

---

## saz2xlsx 통합 가이드

**빌드**

```python
import zipfile
from scripts.menu_auto_label import build_candidate_pool_from_saz

with zipfile.ZipFile(saz_path, "r") as zf:
    pool = build_candidate_pool_from_saz(zf, progress=lambda i,t: print(f"build {i}/{t}"))
```

**매칭**

```python
from scripts.menu_auto_label import best_menu_for_url

label, score, matched = best_menu_for_url(target_url, pool, referer_url=req_headers.get("Referer"))
row["메뉴명(추정)"] = label or ""
row["매칭점수"] = round(score, 2)
row["비고"] = matched or ""
```

**엑셀 컬럼 제안(디버그 모드)**

- `메뉴명(추정)`, `매칭점수`, `matched_candidate_url`(비고)

---

## 테스트 시나리오

1. **경로 라벨 추출**
   - 입력 HTML: `UL/LI/A` 구조 포함
   - 기대: `[부모] > [자식]` 라벨 생성 및 URL 절대화

2. **onclick 함수 본문 URL**
   - `onclick="openFaq()"` + `<script>function openFaq(){{window.open('/help/faq')}}</script>`
   - 기대: `FAQ` 라벨과 `/help/faq` 추출

3. **data-route/form/select**
   - 각 요소에서 URL을 `_URLVAL`로 캡처
   - 기대: 라벨과 절대 URL 수집

4. **charset=cp949 페이지**
   - 기대: 한글 라벨 손실 없이 정상 추출

5. **호스트키 정규화**
   - `example.com` vs `example.com:443`
   - 기대: 동일 키로 풀에 적재/조회

6. **threshold 미달 케이스**
   - 기대: `label=None` 및 `score` 출력 → CSV로 덤프해 룰 개선

---

## 체크리스트

- [ ] `_URLVAL` 수정 시 모든 추출기에 동일 적용했는가?
- [ ] `_MENU_HINTS` 변경으로 푸터/사이드바 오탐이 늘지 않았는가?
- [ ] `_hostkey` 규칙이 빌드/매칭 양쪽에서 동일한가?
- [ ] 확장자 스트립 변경이 정상 라우트를 `{id}`로 오탐하지 않는가?
- [ ] 대용량 SAZ에서 메모리/속도 회귀는 없는가?

---

## 로드맵

- [ ] SPA 라우터 패턴 확대(`history.pushState`, `navigateTo`, 프레임워크별)
- [ ] 외부 스크립트 라우팅 힌트 수집(정적 분석 한정)
- [ ] 경로 라벨 깊이 제한 옵션화(예: `max_depth=3`)
- [ ] 후보 축약 정책 옵션화(동일 URL 시 가장 긴 라벨 유지 등)

---

## 변경 이력

- **2025-08-31**: 초기 문서화.  
  - 경로 라벨(UL/LI/A) 파서 추가
  - `_URLVAL` 포괄 패턴 도입
  - `charset` 우선 디코딩, `xhtml+xml` 대응
  - 호스트키 정규화(기본 포트 제거)
  - 매칭 라벨 가점 룰(마지막 토큰 비교) 추가

---

```
