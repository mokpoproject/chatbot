"""
어업·농업인을 위한 날씨 챗봇 (기본 버전)

기상청 단기예보 조회서비스(VilageFcstInfoService_2.0 / getVilageFcst)를 사용해
"내일 새벽 5시 출항해도 돼?", "모레 농약 쳐도 돼?" 같은 질문에
'작업을 해도 되는지, 무엇을 조심해야 하는지'로 답합니다.

실행 전: .env 파일에 KMA_SERVICE_KEY=발급받은키 를 넣어주세요.

※ 판단 기준은 참고용 규칙입니다. 실제 출항 여부는 기상특보와 해경 통제를 따라야 합니다.
"""

import math
import os
import re
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

load_dotenv()
SERVICE_KEY = os.getenv("KMA_SERVICE_KEY")
ENDPOINT = "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst"

# (이름, 질문에서 알아들을 단어들, 위도, 경도) - 필요하면 여기에 장소를 추가하세요
PLACES = [
    ("무안", ["무안"], 34.9904, 126.4817),
    ("목포항", ["목포"], 34.7852, 126.3834),
    ("해남", ["해남"], 34.5733, 126.5989),
    ("제주시", ["제주시", "제주"], 33.4996, 126.5312),
    ("한림항", ["한림"], 33.4145, 126.2656),
    ("성산포항", ["성산"], 33.4731, 126.9306),
    ("서귀포항", ["서귀포"], 33.2393, 126.5626),
]
DEFAULT_PLACE = PLACES[0]

# 출항 판단용 '앞바다' 지점 (위도, 경도)
# 항구 좌표는 육지 격자에 걸려 파고가 0으로 나오기 때문에, 바다 위 지점의 예보로 파고·풍속을 봄
SEA_POINTS = {
    "무안": (34.90, 125.90),     # 신안 자은도 서쪽 바다
    "목포항": (34.90, 125.90),
    "해남": (34.25, 126.40),     # 땅끝 서쪽 바다
    "제주시": (33.60, 126.53),   # 제주도 북쪽 앞바다
    "한림항": (33.42, 126.15),   # 제주도 서쪽 앞바다
    "성산포항": (33.46, 127.00), # 제주도 동쪽 앞바다
    "서귀포항": (33.17, 126.56), # 제주도 남쪽 앞바다
}

# 기상특보 조회서비스 (공공데이터포털에서 따로 활용신청 필요)
WARN_ENDPOINT = "https://apis.data.go.kr/1360000/WthrWrnInfoService/getPwnStatus"

# 장소별로 확인할 특보구역 이름 (특보 문장에 이 단어가 들어 있으면 해당 지역 특보로 봄)
# 구역 이름이 실제와 다르면 '특보 전체'를 입력해 원문을 보고 여기를 고치세요.
WARN_AREAS = {
    "무안": ["무안", "전남북부서해앞바다"],
    "목포항": ["목포", "전남북부서해앞바다", "서해남부북쪽안쪽먼바다"],
    "해남": ["해남", "전남남부서해앞바다", "서해남부남쪽안쪽먼바다"],
    "제주시": ["제주도북부", "제주도북부앞바다"],
    "한림항": ["제주도서부", "제주도서부앞바다"],
    "성산포항": ["제주도동부", "제주도동부앞바다"],
    "서귀포항": ["제주도남부", "제주도남부앞바다", "제주도남쪽안쪽먼바다"],
}
WARN_WORDS = ["특보", "주의보", "경보"]

FISHING_WORDS = ["출항", "조업", "배 띄", "바다", "파도", "물때", "낚시", "어업", "고기잡"]
FARMING_WORDS = ["농약", "방제", "살포", "밭", "논", "수확", "하우스", "서리", "농사", "농업", "파종", "건조", "말리"]

SKY = {"1": "맑음", "3": "구름많음", "4": "흐림"}
PTY = {"0": "없음", "1": "비", "2": "비/눈", "3": "눈", "4": "소나기"}
BASE_HOURS = [2, 5, 8, 11, 14, 17, 20, 23]  # 단기예보 발표 시각
WORK_HOURS = 6  # 시각을 말하면 그 시각부터 몇 시간 동안의 날씨를 볼지

# ---------------------------------------------------------------------------
# 1. 위도/경도 -> 기상청 격자(nx, ny) 변환 (기상청 제공 LCC 변환식)
# ---------------------------------------------------------------------------
def latlon_to_grid(lat, lon):
    RE, GRID = 6371.00877, 5.0
    SLAT1, SLAT2, OLON, OLAT = 30.0, 60.0, 126.0, 38.0
    XO, YO = 43, 136
    DEGRAD = math.pi / 180.0

    re_ = RE / GRID
    slat1, slat2 = SLAT1 * DEGRAD, SLAT2 * DEGRAD
    olon, olat = OLON * DEGRAD, OLAT * DEGRAD

    sn = math.tan(math.pi * 0.25 + slat2 * 0.5) / math.tan(math.pi * 0.25 + slat1 * 0.5)
    sn = math.log(math.cos(slat1) / math.cos(slat2)) / math.log(sn)
    sf = math.tan(math.pi * 0.25 + slat1 * 0.5)
    sf = (sf ** sn) * math.cos(slat1) / sn
    ro = math.tan(math.pi * 0.25 + olat * 0.5)
    ro = re_ * sf / (ro ** sn)

    ra = math.tan(math.pi * 0.25 + lat * DEGRAD * 0.5)
    ra = re_ * sf / (ra ** sn)
    theta = lon * DEGRAD - olon
    if theta > math.pi:
        theta -= 2.0 * math.pi
    if theta < -math.pi:
        theta += 2.0 * math.pi
    theta *= sn

    x = math.floor(ra * math.sin(theta) + XO + 0.5)
    y = math.floor(ro - ra * math.cos(theta) + YO + 0.5)
    return x, y


# ---------------------------------------------------------------------------
# 2. 기상청 API 호출
# ---------------------------------------------------------------------------
def latest_base(now):
    """지금 조회 가능한 가장 최근 발표 시각 (발표 후 약 10분 뒤부터 제공)"""
    t = now - timedelta(minutes=10)
    for h in reversed(BASE_HOURS):
        if t.hour >= h:
            return t.strftime("%Y%m%d"), f"{h:02d}00"
    prev = t - timedelta(days=1)
    return prev.strftime("%Y%m%d"), "2300"


_cache = {}


def fetch_forecast(nx, ny, now):
    """{(날짜, 시각): {카테고리: 값}} 형태로 예보를 돌려줍니다."""
    base_date, base_time = latest_base(now)
    cache_key = (nx, ny, base_date, base_time)
    if cache_key in _cache:
        return _cache[cache_key]

    params = {
        "serviceKey": SERVICE_KEY,
        "pageNo": 1,
        "numOfRows": 1500,
        "dataType": "JSON",
        "base_date": base_date,
        "base_time": base_time,
        "nx": nx,
        "ny": ny,
    }
    res = requests.get(ENDPOINT, params=params, timeout=10)
    res.raise_for_status()

    try:
        data = res.json()
    except ValueError:
        # 키가 아직 등록 전이거나 잘못되면 XML 오류 메시지가 옵니다
        raise RuntimeError(
            "API가 예상과 다른 응답을 보냈어요. 키 발급 직후라면 1시간쯤 뒤에 다시 해보세요.\n"
            + res.text[:300]
        )

    header = data["response"]["header"]
    if header["resultCode"] != "00":
        raise RuntimeError(f"API 오류 {header['resultCode']}: {header['resultMsg']}")

    forecast = {}
    for item in data["response"]["body"]["items"]["item"]:
        key = (item["fcstDate"], item["fcstTime"])
        forecast.setdefault(key, {})[item["category"]] = item["fcstValue"]

    _cache[cache_key] = forecast
    return forecast



# ---------------------------------------------------------------------------
# 2-1. 기상특보 현황 (지금 발효 중인 특보 + 예비특보)
# ---------------------------------------------------------------------------
_warn_cache = {"time": None, "data": None}


def fetch_warnings(now):
    """최신 특보현황 원문을 {'t6': 발효 중 특보, 't7': 예비특보, 'tmFc': 발표시각}으로 돌려줍니다."""
    if _warn_cache["time"] and now - _warn_cache["time"] < timedelta(minutes=10):
        return _warn_cache["data"]

    params = {"serviceKey": SERVICE_KEY, "pageNo": 1, "numOfRows": 10, "dataType": "JSON"}
    res = requests.get(WARN_ENDPOINT, params=params, timeout=10)
    res.raise_for_status()
    try:
        data = res.json()
    except ValueError:
        raise RuntimeError(
            "특보 API가 예상과 다른 응답을 보냈어요. 공공데이터포털에서 "
            "'기상청_기상특보 조회서비스'도 활용신청했는지 확인해주세요.\n" + res.text[:300]
        )

    header = data["response"]["header"]
    if header["resultCode"] != "00":
        raise RuntimeError(f"특보 API 오류 {header['resultCode']}: {header['resultMsg']}")

    items = data["response"]["body"]["items"]["item"]
    latest = max(items, key=lambda it: int(it.get("tmFc", 0)))  # 순서가 아니라 발표시각으로 최신 선택
    result = {"t6": latest.get("t6", ""), "t7": latest.get("t7", ""), "tmFc": str(latest.get("tmFc", ""))}
    _warn_cache.update(time=now, data=result)
    return result


def parse_active(t6):
    """'o 풍랑주의보 : 구역1, 구역2' 줄들 -> [(특보이름, 구역문자열), ...]"""
    result = []
    for line in re.split(r"[\r\n]+", t6 or ""):
        line = line.strip().lstrip("o").strip()
        if ":" not in line:
            continue
        name, areas = line.split(":", 1)
        result.append((name.strip(), areas.strip()))
    return result


def parse_preliminary(t7):
    """'(1) 풍랑 예비특보' 제목 아래 'o 시각 : 구역' 줄들 -> [(제목, 시각, 구역), ...]"""
    result, title = [], ""
    for line in re.split(r"[\r\n]+", t7 or ""):
        line = line.strip()
        if re.match(r"\(\d+\)", line):
            title = re.sub(r"^\(\d+\)\s*", "", line)
        elif ":" in line:
            when, areas = line.lstrip("o").strip().split(":", 1)
            result.append((title, when.strip(), areas.strip()))
    return result


def warnings_for(place_name, warn):
    """해당 장소에 걸린 특보만 골라냅니다."""
    keys = WARN_AREAS.get(place_name, [place_name])
    active = [(n, a) for n, a in parse_active(warn["t6"]) if any(k in a for k in keys)]
    prelim = [(t, w, a) for t, w, a in parse_preliminary(warn["t7"]) if any(k in a for k in keys)]
    return active, prelim


def warning_lines(place_name, warn):
    active, prelim = warnings_for(place_name, warn)
    tm = warn["tmFc"]
    stamp = f"{tm[4:6]}/{tm[6:8]} {tm[8:10]}:{tm[10:12]} 발표" if len(tm) >= 12 else "최신 발표"
    lines = [f"📢 기상특보 ({stamp} 기준)"]
    if active:
        lines += [f"🚨 {name} 발효 중" for name, _ in active]
    else:
        lines.append("발효 중인 특보 없음")
    for title, when, _ in prelim:
        lines.append(f"🔔 {title}: {when} 발효 예정")
    return lines, active, prelim


# ---------------------------------------------------------------------------
# 3. 질문 이해하기 (규칙 기반)
# ---------------------------------------------------------------------------
def parse_question(text, now):
    # 날짜
    day_offset = 0
    if "글피" in text:
        day_offset = 3
    elif "모레" in text:
        day_offset = 2
    elif "내일" in text:
        day_offset = 1
    target_date = (now + timedelta(days=day_offset)).date()

    # 시각
    hour = None
    m = re.search(r"(\d{1,2})\s*시", text)
    if m:
        hour = int(m.group(1)) % 24
        if any(w in text for w in ["오후", "저녁", "밤"]) and hour < 12:
            hour += 12
    elif "새벽" in text:
        hour = 4
    elif "아침" in text:
        hour = 7
    elif "점심" in text:
        hour = 12
    elif "오후" in text:
        hour = 14
    elif "저녁" in text:
        hour = 18

    # 장소 (가장 길게 일치하는 단어 기준)
    place = DEFAULT_PLACE
    best_len = 0
    for p in PLACES:
        for word in p[1]:
            if word in text and len(word) > best_len:
                place, best_len = p, len(word)

    # 어업 / 농업 / 둘 다
    fishing = any(w in text for w in FISHING_WORDS)
    farming = any(w in text for w in FARMING_WORDS)
    if fishing and not farming:
        mode = "어업"
    elif farming and not fishing:
        mode = "농업"
    else:
        mode = "전체"

    return place, target_date, hour, mode


# ---------------------------------------------------------------------------
# 4. 예보 정리
# ---------------------------------------------------------------------------
def to_float(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def rain_mm(pcp):
    """PCP 값('강수없음', '1mm 미만', '2.0mm', '30.0~50.0mm') -> 숫자(mm)"""
    if not pcp or "없음" in pcp:
        return 0.0
    if "미만" in pcp:
        return 0.5
    m = re.search(r"\d+(\.\d+)?", pcp)
    return float(m.group()) if m else 0.0


def summarize(hours):
    """[(시, 예보dict), ...] -> 판단에 필요한 값들"""
    temps = [to_float(v["TMP"]) for _, v in hours if "TMP" in v]
    waves = [to_float(v["WAV"]) for _, v in hours if "WAV" in v]
    rain_hours = [h for h, v in hours if v.get("PTY", "0") != "0" or to_float(v.get("POP")) >= 60]
    # 육지 격자는 파고가 0으로 나옴 -> 0은 '잔잔함'이 아니라 '정보 없음'으로 처리
    wav = max(waves) if waves else None
    if wav is not None and wav <= 0:
        wav = None
    return {
        "tmin": min(temps) if temps else None,
        "tmax": max(temps) if temps else None,
        "wsd": max((to_float(v.get("WSD")) for _, v in hours), default=0.0),
        "wav": wav,
        "pop": max((int(to_float(v.get("POP"))) for _, v in hours), default=0),
        "rain_total": sum(rain_mm(v.get("PCP")) for _, v in hours),
        "rain_hours": rain_hours,
        "sky": describe_sky(hours, rain_hours),
    }


def describe_sky(hours, rain_hours):
    """하루 날씨 한 줄 요약: 가장 많은 하늘 상태 + 비/눈 오는 시간대"""
    skies = [SKY.get(v.get("SKY", ""), "") for _, v in hours]
    skies = [x for x in skies if x]
    main = max(set(skies), key=skies.count) if skies else ""
    if not rain_hours:
        return main
    kinds = [PTY.get(v.get("PTY", "0"), "비") for h, v in hours if h in rain_hours and v.get("PTY", "0") != "0"]
    kind = max(set(kinds), key=kinds.count) if kinds else "비"
    span = f"{rain_hours[0]}시" if rain_hours[0] == rain_hours[-1] else f"{rain_hours[0]}~{rain_hours[-1]}시"
    return f"{main}, {span} {kind}" if main else f"{span} {kind}"


# ---------------------------------------------------------------------------
# 5. 판단 규칙
# ---------------------------------------------------------------------------
def fishing_advice(s, is_today=True, active=None, prelim=None):
    """실제 특보를 우선 반영하고, 그다음 풍랑주의보 기준(풍속 14m/s, 파고 3m)으로 판단"""
    wsd, wav = s["wsd"], s["wav"]
    wav_text = f"{wav}m" if wav is not None else "정보 없음"
    head = f"🚢 어업 | 최대 풍속 {wsd}m/s, 최대 파고 {wav_text}"
    sea_active = [n for n, _ in (active or []) if "풍랑" in n]
    sea_prelim = [t for t, _, _ in (prelim or []) if "풍랑" in t]

    if is_today and sea_active:
        verdict = f"⛔ 출항 금지: {sea_active[0]}가 발효 중이에요."
    elif wsd >= 14 or (wav is not None and wav >= 3):
        verdict = "⛔ 출항 위험: 풍랑주의보 수준의 바람·파도예요. 출항하지 마세요."
    elif sea_active:
        verdict = "⚠️ 출항 주의: 지금 풍랑특보가 발효 중이에요. 해제됐는지 출발 전에 다시 물어보세요."
    elif sea_prelim:
        verdict = "⚠️ 출항 주의: 풍랑 예비특보가 있어요. 특보로 바뀔 수 있으니 출발 전에 다시 확인하세요."
    elif wsd >= 9 or (wav is not None and wav >= 2):
        verdict = "⚠️ 출항 주의: 소형 어선은 위험할 수 있어요. 가까운 바다에서 짧게 조업하세요."
    elif wav is None:
        # 파고를 모르는데 '양호'라고 하면 위험한 오판이 될 수 있음
        verdict = "⚠️ 판단 보류: 바람은 약하지만 파고 정보가 없어요. 해상예보로 파도를 꼭 확인하세요."
    else:
        verdict = "✅ 출항 양호: 바람과 파도가 잔잔한 편이에요."

    lines = [head, verdict]
    if s["rain_hours"]:
        lines.append(f"☔ {s['rain_hours'][0]}시~{s['rain_hours'][-1]}시 비 소식, 시야와 갑판 미끄럼에 주의하세요.")
    return lines


# 특보 종류별로 농가가 할 일 (순서대로 확인)
FARM_WARN_ACTIONS = [
    ("태풍", "하우스를 단단히 고정하고, 거둘 수 있는 작물은 미리 수확하세요. 바람이 불 때는 밭에 나가지 마세요."),
    ("호우", "배수로와 물꼬를 미리 정비하고, 거둘 수 있는 작물은 미리 수확하세요."),
    ("강풍", "비닐하우스 고정끈·출입문을 점검하고, 지주대를 단단히 묶어두세요."),
    ("한파", "하우스 보온·난방을 점검하고, 노지 작물은 덮개를 씌우세요. 수도관 동파에 주의하세요."),
    ("대설", "하우스 지붕의 눈을 수시로 털어주고, 필요하면 지지대를 보강하세요."),
    ("폭염", "한낮(12~17시) 작업은 피하고, 하우스 환기와 물 주기를 늘리세요."),
    ("건조", "불씨에 주의하고, 논·밭두렁 태우기는 하지 마세요."),
]


def farm_warning_advice(active, prelim, is_today):
    """발효 중인 특보·예비특보를 농가 행동으로 바꿔줍니다 (풍랑 같은 바다 특보는 제외)."""
    lines = []
    for name, _ in active or []:
        for key, action in FARM_WARN_ACTIONS:
            if key in name:
                when = "발효 중" if is_today else "지금 발효 중(그날까지 이어질 수 있어요)"
                lines.append(f"🚨 {name} {when}: {action}")
                break
    for title, when, _ in prelim or []:
        for key, action in FARM_WARN_ACTIONS:
            if key in title:
                lines.append(f"🔔 {title}({when}): 특보로 바뀔 수 있어요. {action}")
                break
    return lines


def water_advice(rain_day, rain_next, tmax):
    """물 주기 판단: 그날과 다음 날 예상 강수량 기준"""
    if rain_day >= 10:
        return f"🚿 물 주기 ✗: 약 {rain_day:.0f}mm 비 예보라 물 안 줘도 돼요."
    if rain_day >= 1:
        return f"🚿 물 주기 △: 비가 약 {rain_day:.0f}mm로 적게 와요. 흙이 마르면 주세요."
    if rain_next >= 10:
        return f"🚿 물 주기 △: 다음 날 약 {rain_next:.0f}mm 비 예보라 오늘은 조금만 주세요."
    if tmax is not None and tmax >= 25:
        return "🚿 물 주기 ○: 비 소식 없고 더워요. 아침이나 저녁 선선할 때 주세요."
    return "🚿 물 주기 ○: 비 소식이 없어요. 평소대로 주세요."


def frost_advice(night, asked=False):
    """서리 판단: 새벽(0~8시) 기온·하늘·바람으로 판단.
    서리는 맑고 바람이 약한 밤에 땅이 식으면서 잘 내립니다.
    asked=True(질문에 '서리' 등이 있음)면 위험이 없어도 결과를 알려줍니다."""
    if not night:
        return "❄️ 서리: 해당 새벽 예보가 아직 없어요." if asked else None
    date_label, hours = night
    temps = [(h, to_float(v["TMP"])) for h, v in hours if "TMP" in v]
    if not temps:
        return "❄️ 서리: 해당 새벽 예보가 아직 없어요." if asked else None
    coldest_h, tmin = min(temps, key=lambda x: x[1])
    if tmin > 4:
        if asked:
            return f"✅ 서리 걱정 없음: {date_label} 새벽 최저 {tmin:.0f}°C({coldest_h}시)예요."
        return None
    clear = sum(1 for _, v in hours if v.get("SKY") == "1") >= len(hours) / 2
    calm = max(to_float(v.get("WSD")) for _, v in hours) <= 2
    head = f"{date_label} 새벽 최저 {tmin:.0f}°C({coldest_h}시)"
    if tmin <= 0:
        return f"❄️ 서리·냉해 위험: {head} - 영하라 노지 작물은 덮개를 씌우고, 수확할 작물은 미리 거두세요."
    if clear and calm:
        return f"❄️ 서리 가능성 높음: {head}, 맑고 바람이 약해요 - 보온 덮개를 준비하세요."
    return f"🌡️ 저온 주의: {head} - 구름이나 바람 때문에 서리 가능성은 낮지만 냉해에 주의하세요."


FROST_WORDS = ["서리", "냉해", "추워", "추위", "얼어", "얼음"]


def farming_advice(s, is_today=True, active=None, prelim=None, rain_day=0.0, rain_next=0.0, night=None,
                   asked_frost=False):
    lines = [f"🌾 농업 | 기온 {s['tmin']:.0f}~{s['tmax']:.0f}°C, 최대 풍속 {s['wsd']}m/s, 강수확률 최대 {s['pop']}%"]
    lines += farm_warning_advice(active, prelim, is_today)

    # 농약 살포: 비가 오면 씻겨 내려가고, 바람이 세면 날려서 옆 밭에 피해
    storm = [n for n, _ in (active or []) if "호우" in n or "태풍" in n]
    if is_today and storm:
        lines.append(f"🧪 농약 살포 ✗: {storm[0]} 발효 중이라 약이 씻겨 내려가요.")
    elif s["rain_hours"]:
        lines.append(f"🧪 농약 살포 ✗: {s['rain_hours'][0]}시쯤부터 비 소식이 있어 약이 씻겨 내려가요.")
    elif s["wsd"] >= 4:
        lines.append(f"🧪 농약 살포 ✗: 바람({s['wsd']}m/s)이 세서 약이 날려요. 바람 잦을 때 하세요.")
    else:
        lines.append("🧪 농약 살포 ○: 비 없고 바람도 약해서 방제하기 좋아요.")

    # 수확물 건조
    if s["rain_hours"] or s["pop"] >= 30:
        lines.append("🌶️ 건조 작업 ✗: 비 가능성이 있어요. 말리던 작물은 덮어두세요.")

    # 비가 많이 오면 배수 점검
    if s["rain_total"] >= 30:
        lines.append(f"💧 예상 강수량 약 {s['rain_total']:.0f}mm - 배수로를 미리 점검하세요.")

    # 강풍: 비닐하우스
    if s["wsd"] >= 9:
        lines.append("💨 강풍 주의: 비닐하우스 고정끈과 출입문을 점검하세요.")

    # 물 주기
    lines.append(water_advice(rain_day, rain_next, s["tmax"]))

    # 서리
    frost = frost_advice(night, asked_frost)
    if frost:
        lines.append(frost)

    if s["tmax"] is not None and s["tmax"] >= 33:
        lines.append(f"🥵 폭염 주의: 최고 {s['tmax']:.0f}°C - 한낮 작업은 피하고 물을 자주 드세요.")
    return lines


def answer(question, now=None):
    now = now or datetime.now(ZoneInfo("Asia/Seoul")).replace(tzinfo=None)
    place, target_date, hour, mode = parse_question(question, now)
    name, _, lat, lon = place

    # "특보", "주의보", "경보"를 물으면 특보만 바로 알려줌 (전화로 확인하던 걸 대신)
    if any(w in question for w in WARN_WORDS):
        warn = fetch_warnings(now)
        if "전체" in question:
            return f"[전국 특보 원문]\n{warn['t6']}\n\n[예비특보 원문]\n{warn['t7']}"
        lines, _, _ = warning_lines(name, warn)
        return f"[{name}]\n" + "\n".join(lines)

    nx, ny = latlon_to_grid(lat, lon)
    fc = fetch_forecast(nx, ny, now)
    date_str = target_date.strftime("%Y%m%d")

    day_hours = sorted((int(t[:2]), v) for (d, t), v in fc.items() if d == date_str)
    if hour is not None:
        hours = [(h, v) for h, v in day_hours if hour <= h < hour + WORK_HOURS]
        period = f"{hour}시~{hour + WORK_HOURS}시"
    else:
        start = now.hour if target_date == now.date() else 0
        hours = [(h, v) for h, v in day_hours if h >= start]
        period = "하루" if start == 0 else f"{start}시 이후"

    if not hours:
        return f"{name} 해당 시간 예보가 아직 없거나 이미 지난 시간이에요. (단기예보는 약 3~4일 뒤까지 제공돼요)"

    s = summarize(hours)
    lines = [f"[{name} {date_str[4:6]}/{date_str[6:]} {period}] {s['sky']}"]

    # 특보 자동 확인 (특보는 '지금' 기준이라 오늘이 아니면 주의로만 반영)
    is_today = target_date == now.date()
    active, prelim = [], []
    try:
        warn_lines, active, prelim = warning_lines(name, fetch_warnings(now))
        # 예비특보는 보통 하루 이틀 안의 일이라, 그보다 먼 날 질문에는 반영하지 않음
        if (target_date - now.date()).days > 1:
            prelim = []
        if mode in ("어업", "전체"):
            lines += warn_lines
    except Exception as e:
        lines.append(f"📢 특보 확인 실패: {str(e).splitlines()[0]}")

    if mode in ("어업", "전체"):
        lines += fishing_advice(sea_summary(name, s, date_str, [h for h, _ in hours], now),
                                is_today, active, prelim)
    if mode in ("농업", "전체"):
        rain_day, rain_next, night = farm_context(fc, target_date, now)
        asked_frost = any(w in question for w in FROST_WORDS)
        lines += farming_advice(s, is_today, active, prelim, rain_day, rain_next, night, asked_frost)
    return "\n".join(lines)


def sea_summary(name, s, date_str, hour_list, now):
    """출항 판단용: 앞바다 지점 예보로 풍속·파고를 바꿔 넣음 (실패하면 육지 값 그대로)"""
    if name not in SEA_POINTS:
        return s
    try:
        sea_fc = fetch_forecast(*latlon_to_grid(*SEA_POINTS[name]), now)
    except Exception:
        return s
    sea_hours = [(h, sea_fc[(date_str, f"{h:02d}00")]) for h in hour_list if (date_str, f"{h:02d}00") in sea_fc]
    if not sea_hours:
        return s
    sea = summarize(sea_hours)
    return {**s, "wsd": sea["wsd"], "wav": sea["wav"]}


def farm_context(fc, target_date, now):
    """물 주기·서리 판단용: 그날/다음 날 강수량, 확인할 새벽 시간대"""
    def day_rain(d):
        ds = d.strftime("%Y%m%d")
        return sum(rain_mm(v.get("PCP")) for (dd, _), v in fc.items() if dd == ds)

    next_date = target_date + timedelta(days=1)
    # 오늘 아침 8시가 지났으면 '다음 날 새벽' 서리를 봄
    frost_date = next_date if (target_date == now.date() and now.hour >= 8) else target_date
    fd = frost_date.strftime("%Y%m%d")
    night_hours = sorted((int(t[:2]), v) for (d, t), v in fc.items() if d == fd and int(t[:2]) <= 8)
    label = {0: "오늘", 1: "내일", 2: "모레"}.get((frost_date - now.date()).days, frost_date.strftime("%m/%d"))
    night = (label, night_hours) if night_hours else None
    return day_rain(target_date), day_rain(next_date), night


# ---------------------------------------------------------------------------
# 6. 대화 루프
# ---------------------------------------------------------------------------
def main():
    if not SERVICE_KEY:
        print("⚠️ .env 파일에 KMA_SERVICE_KEY=발급받은키 를 넣어주세요.")
        sys.exit(1)

    print("🌊🌾 어업·농업 날씨 챗봇입니다. 종료하려면 '종료'를 입력하세요.")
    print("예) 내일 새벽 5시 목포 출항해도 돼? / 모레 무안 농약 쳐도 돼? / 성산 풍랑주의보 내렸어? / 특보 전체")
    print("알아듣는 장소:", ", ".join(p[0] for p in PLACES))
    while True:
        try:
            q = input("\n나> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not q:
            continue
        if q in ("종료", "exit", "quit"):
            break
        try:
            print("봇>", answer(q))
        except Exception as e:
            print("봇> 문제가 생겼어요:", e)
    print("봇> 안녕히 가세요!")


if __name__ == "__main__":
    main()
