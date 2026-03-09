"""
나라장터(G2B) 입찰 공고 수집 스크립트 v2

수집 전략:
1. [지역] 충남/아산: 모든 공고 수집 (지역제한 놓치지 않기 위함)
2. [전국] 키워드 검색 후 '내 면허' 보유 건만 필터링 (기계가스, 실내건축 등)

보유 면허(업종):
- 기계가스설비공사업 (주력: 기계설비)
- 가스난방시공업 (주력: 가스시설시공업 2종, 난방시공업 2종)
- 실내건축공사업
- 물품 (보일러, 냉난방기 등)

관련도 자동 계산:
- 3 ⭐⭐⭐ 핵심: 보일러, GHP, 가스히트펌프, 지열, 공기열, 히트펌프
- 2 ⭐⭐  관련: 냉난방, EHP, 냉방기, 난방기
- 1 ⭐    업종: 기계설비, 실내건축, 가스, 설비공사
"""

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from urllib.parse import urlencode

import requests

# Windows 콘솔 UTF-8 출력
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ── 설정 ──────────────────────────────────────────────
API_BASE = "https://apis.data.go.kr/1230000/ad/BidPublicInfoService"
# GitHub Actions Secrets에서 읽거나, 로컬용 키 사용 (환경변수 우선)
API_KEY = os.environ.get("OPEN_API_KEY", "47b01b658dc081be550f3bb16d80ed544c629993f10ed2b317f6dc04a95f8722")

# 내 면허 키워드 (공고의 업종명에 이 단어가 포함되어야 수집)
# 대분류(통합업종)와 세부분류(주력분야) 모두 커버하도록 설정
# 1. 기계설비 (기계가스설비공사업, 기계설비공사)
# 2. 가스/난방 2종 (가스난방시공업, 가스시설시공업, 난방시공업)
MY_LICENSES = ["기계가스설비", "기계설비", "가스난방", "가스시설", "난방시공", "실내건축"]

# 내 물품 키워드 (물품 공고일 경우 품목명에 포함되어야 수집)
MY_GOODS = ["보일러", "냉난방", "냉방", "난방", "히트펌프", "GHP", "EHP", "관급자재"]

ENDPOINTS = {
    "공사": "/getBidPblancListInfoCnstwk",
    "물품": "/getBidPblancListInfoThng",
}

# 수집 대상 설정
# type: "local" (지역 - 무조건 수집), "national" (전국 - 면허 필터링 적용)
REGIONS = [
    # 1. 지역 우선 (충남/아산 발주처는 업종 상관없이 일단 확인)
    {"type": "local", "code": "28",   "name": "충청남도", "params": {"ntceInsttNm": "충청남도"}},
    {"type": "local", "code": "2817", "name": "아산시",   "params": {"ntceInsttNm": "아산시"}},
    
    # 2. 전국 (키워드로 검색하되, 나중에 면허로 필터링함)
    {"type": "national", "code": "00", "name": "전국_보일러",  "params": {"bidNtceNm": "보일러"}},
    {"type": "national", "code": "00", "name": "전국_냉난방",  "params": {"bidNtceNm": "냉난방"}},
    {"type": "national", "code": "00", "name": "전국_GHP",    "params": {"bidNtceNm": "GHP"}},
    {"type": "national", "code": "00", "name": "전국_EHP",    "params": {"bidNtceNm": "EHP"}},
    {"type": "national", "code": "00", "name": "전국_히트펌프", "params": {"bidNtceNm": "히트펌프"}},
    {"type": "national", "code": "00", "name": "전국_기계설비", "params": {"bidNtceNm": "기계설비"}},
    {"type": "national", "code": "00", "name": "전국_실내건축", "params": {"bidNtceNm": "실내건축"}},
    {"type": "national", "code": "00", "name": "전국_가스",    "params": {"bidNtceNm": "가스"}},
]

# 키워드 관련도 (3: 핵심, 2: 관련, 1: 업종)
KEYWORDS = {
    3: ["보일러", "GHP", "가스히트펌프", "지열", "공기열", "히트펌프"],
    2: ["냉난방", "EHP", "냉방기", "난방기"],
    1: ["기계가스설비", "기계설비", "가스난방", "난방시공", "실내건축", "가스시설"],
}

OUTPUT_DIR = "data"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "notices.json")

# 조회 기간: 오늘 기준 최근 7일
FETCH_DAYS = 3
DT_FMT = "%Y%m%d%H%M"  # API 필수 형식: YYYYMMDDHHMM (12자리)
PAGE_SIZE = 100
# ──────────────────────────────────────────────────────


def get_date_range() -> tuple[str, str]:
    """오늘 기준 7일 조회 범위 반환"""
    now = datetime.now()
    start = now - timedelta(days=FETCH_DAYS)
    return start.strftime(DT_FMT), now.strftime(DT_FMT)


def fetch_page(
    endpoint: str,
    page: int,
    bgn_dt: str,
    end_dt: str,
    extra_params: dict,
) -> dict:
    """나라장터 API 단일 페이지 호출.

    serviceKey를 URL에 직접 삽입해 이중 인코딩 방지.
    날짜 형식: YYYYMMDDHHMM (12자리 필수)
    """
    url_base = API_BASE + endpoint
    params = {
        "pageNo": page,
        "numOfRows": PAGE_SIZE,
        "type": "json",
        "inqryDiv": "1",       # 1: 공고일자 기준
        "inqryBgnDt": bgn_dt,
        "inqryEndDt": end_dt,
        **extra_params,
    }
    url = f"{url_base}?serviceKey={API_KEY}&{urlencode(params)}"
    resp = requests.get(url, timeout=30)
    if not resp.ok:
        print(f"  [HTTP {resp.status_code}] {resp.text[:300]}", file=sys.stderr)
        resp.raise_for_status()
    data = resp.json()
    hdr = data.get("response", {}).get("header", {})
    code = hdr.get("resultCode", "00")
    if code not in ("00", ""):
        raise ValueError(f"API 오류 code={code} msg={hdr.get('resultMsg')}")
    return data


def extract_items(data: dict) -> list[dict]:
    """API 응답에서 공고 목록 추출 (다양한 응답 구조 대응)"""
    try:
        body = data["response"]["body"]
        items = body.get("items", [])
        if not items:
            return []
        if isinstance(items, dict):
            inner = items.get("item", [])
            if isinstance(inner, list):
                return inner
            if isinstance(inner, dict):
                return [inner]
            return []
        if isinstance(items, list):
            return items
    except (KeyError, AttributeError, TypeError):
        pass
    return []


def is_target_notice(item: dict, region_type: str, category: str) -> bool:
    """
    수집 대상인지 판별하는 필터 함수
    - 지역(local) 공고면: 무조건 True (일단 수집)
    - 전국(national) 공고면: 내 면허(업종)나 물품이 포함되어야 True
    """
    if region_type == "local":
        return True

    # 전국 공고일 경우 필터링
    # 1. 공사: 업종명(indstrytyNm) 확인
    industry = item.get("indstrytyNm") or item.get("cnstwkEtcIndstrytyNm") or ""
    if category == "공사":
        if any(lic in industry for lic in MY_LICENSES):
            return True

    # 2. 물품: 품목명(prdctClsfcNoNm) 확인
    product = item.get("prdctClsfcNoNm") or ""
    if category == "물품":
        if any(good in product for good in MY_GOODS):
            return True
            
    return False


def calc_relevance(notice: dict) -> int:
    """공고명·분류명에서 키워드 매칭으로 관련도 계산.

    반환값: 3(핵심) / 2(관련) / 1(업종) / 0(해당없음)
    """
    text = " ".join([
        notice.get("bidNtceNm", ""),             # 공고명
        notice.get("prdctClsfcNoNm", ""),        # 물품분류명
        notice.get("cnstwkEtcIndstrytyNm", ""),  # 공사업종명
        notice.get("indstrytyNm", ""),           # 용역업종명
        notice.get("dminsttNm", ""),             # 수요기관명
        notice.get("ntceInsttNm", ""),           # 공고기관명
    ]).replace(" ", "")

    for score in (3, 2, 1):  # 높은 점수부터 확인
        if any(kw in text for kw in KEYWORDS[score]):
            return score
    return 0


def collect_region_endpoint(
    endpoint: str,
    label: str,
    region: dict,
    bgn_dt: str,
    end_dt: str,
) -> list[dict]:
    """특정 카테고리+지역 조합의 전체 페이지 수집"""
    all_items: list[dict] = []
    page = 1
    tag = f"{label}/{region['name']}"

    while True:
        try:
            data = fetch_page(endpoint, page, bgn_dt, end_dt, region["params"])
        except (requests.RequestException, ValueError) as e:
            print(f"  [경고] {tag} p{page} 실패: {e}")
            break

        items = extract_items(data)
        if not items:
            break

        all_items.extend(items)

        try:
            total = int(data["response"]["body"].get("totalCount", 0))
        except (KeyError, ValueError, TypeError):
            total = len(all_items)

        print(f"    {tag} p{page}: {len(items)}건 (누적 {len(all_items)}/{total})")

        if len(all_items) >= total:
            break

        page += 1
        time.sleep(0.3)

    return all_items


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    now = datetime.now()
    bgn_dt, end_dt = get_date_range()

    print(f"수집 기간: {bgn_dt} ~ {end_dt}")
    print(f"대상: {len(ENDPOINTS)}개 카테고리 × {len(REGIONS)}개 항목\n")

    raw_pool: list[dict] = []
    fetch_count = 0

    for label, endpoint in ENDPOINTS.items():
        for region in REGIONS:
            print(f"[{label} / {region['name']}({region['code']})] 수집 시작")
            items = collect_region_endpoint(endpoint, label, region, bgn_dt, end_dt)
            for item in items:
                item["_category"] = label
                item["_region_code"] = region["code"]
                item["_region_name"] = region["name"]
                
                # 여기서 1차 필터링 (전국 공고 중 내 면허 아닌 것 제외)
                if is_target_notice(item, region.get("type", "national"), label):
                    raw_pool.append(item)
                    fetch_count += 1
            
            print(f"  → 수집 {len(items)}건 / 유효 {fetch_count}건 (누적)\n")
            time.sleep(0.2) # API 부하 조절

    # 중복 제거 (공고번호+차수 기준, 먼저 수집된 것 우선)
    seen: set[str] = set()
    unique: list[dict] = []
    for item in raw_pool:
        key = f"{item.get('bidNtceNo')}-{item.get('bidNtceOrd', '00')}"
        if key not in seen:
            seen.add(key)
            unique.append(item)

    print(f"전체 수집: {len(raw_pool)}건 → 중복제거 후: {len(unique)}건")

    # 관련도 계산
    for item in unique:
        item["_relevance"] = calc_relevance(item)

    # 관련도 1 이상만 포함
    filtered = [item for item in unique if item["_relevance"] > 0]
    print(f"키워드 매칭: {len(filtered)}/{len(unique)}건")

    # 관련도 내림차순, 공고일자 내림차순 정렬
    filtered.sort(
        key=lambda x: (
            -x["_relevance"],
            x.get("bidNtceDt") or x.get("bidNtceRegstrDt") or "",
        ),
        reverse=False,
    )
    # 실제 정렬: 관련도 높은 순 → 날짜 최신 순
    filtered.sort(
        key=lambda x: (
            x["_relevance"],
            x.get("bidNtceDt") or x.get("bidNtceRegstrDt") or "",
        ),
        reverse=True,
    )

    # 저장
    output = {
        "fetched_at": now.isoformat(),
        "period": {"from": bgn_dt, "to": end_dt},
        "total": len(filtered),
        "notices": filtered,
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n저장 완료: {OUTPUT_FILE} (총 {len(filtered)}건)")
    print("관련도별 통계:")
    for score, label in [(3, "⭐⭐⭐ 핵심"), (2, "⭐⭐  관련"), (1, "⭐    업종")]:
        count = sum(1 for x in filtered if x["_relevance"] == score)
        print(f"  {label}: {count}건")

    # ── Git 자동 업데이트 (로컬 실행 시에만 작동) ─────────────────────────────
    # GitHub Actions(서버)가 아닐 때만 실행. 서버에서는 yml 파일이 별도로 처리함.
    if "GITHUB_ACTIONS" not in os.environ:
        print("\n[Git] GitHub 업데이트 시작...")
        try:
            # 데이터 파일뿐만 아니라 index.html 등 모든 변경사항을 함께 업로드
            subprocess.run(["git", "add", "."], check=True)
            msg = f"Auto-update: {now.strftime('%Y-%m-%d %H:%M')}"
            subprocess.run(["git", "commit", "-m", msg], check=False)
            subprocess.run(["git", "push"], check=True)
            print("  → [성공] GitHub Push 완료! 잠시 후 대시보드에서 확인하세요.")
        except Exception as e:
            print(f"  → [오류] Git 명령 실패: {e}")


if __name__ == "__main__":
    main()
