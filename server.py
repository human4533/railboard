#!/usr/bin/env python3
"""
한국 열차 시간대별 안내판 (간선 + 전국 도시철도)

- 간선(경부선·경부고속선·호남선·호남고속선·전라선·강릉선):
    국토교통부 TAGO 열차정보 API  (apis.data.go.kr/1613000/TrainInfo)
- 도시철도(서울·인천·부산·대구·광주·대전 등 전국):
    철도산업정보센터(KRIC) 열차별 운행시각표 API (openapi.kric.go.kr/openapi/trainUseInfo)
- 실시간(수도권 전철):
    서울 열린데이터광장 지하철 실시간 도착정보 API

외부 패키지 없이 파이썬 3.9+ 표준 라이브러리만 사용합니다.
실행:  python server.py   →  브라우저에서 http://localhost:8000
"""
import datetime as dt
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
CACHE_DIR = os.path.join(BASE_DIR, "cache")
KST = dt.timezone(dt.timedelta(hours=9))

TAGO_URL = "https://apis.data.go.kr/1613000/TrainInfo"
KRIC_URL = "https://openapi.kric.go.kr/openapi/trainUseInfo"
SEOUL_RT_URL = "http://swopenapi.seoul.go.kr/api/subway/{key}/json/realtimeStationArrival/0/80/{name}"

# ─────────────────────────────────────────────────────────────
# 간선 노선 정의 (역 순서 = 상행 → 하행). TAGO 역 이름으로 자동 매칭합니다.
# 역 이름이 TAGO와 다르면 config.json 의 "intercity_lines" 로 덮어쓸 수 있습니다.
# ─────────────────────────────────────────────────────────────
INTERCITY_LINES = {
    "gyeongbu": {"name": "경부선", "stations": [
        "서울", "영등포", "수원", "평택", "천안", "조치원", "대전", "영동", "김천",
        "구미", "대구", "동대구", "경산", "밀양", "구포", "부산"]},
    "gyeongbu_hs": {"name": "경부고속선", "stations": [
        "서울", "광명", "천안아산", "오송", "대전", "김천구미", "동대구", "경주",
        "울산", "부산"]},
    "honam": {"name": "호남선", "stations": [
        "용산", "영등포", "수원", "천안", "서대전", "계룡", "논산", "익산", "김제",
        "정읍", "장성", "광주송정", "나주", "목포"]},
    "honam_hs": {"name": "호남고속선", "stations": [
        "용산", "광명", "천안아산", "오송", "공주", "익산", "정읍", "광주송정",
        "나주", "목포"]},
    "jeolla": {"name": "전라선", "stations": [
        "용산", "익산", "전주", "남원", "곡성", "구례구", "순천", "여천", "여수EXPO"]},
    "gangneung": {"name": "강릉선", "stations": [
        "서울", "청량리", "상봉", "양평", "서원주", "만종", "횡성", "둔내", "평창",
        "진부", "강릉"]},
}

# KRIC 도시철도 권역 코드(mreaWideCd). 01=수도권, 02=부산은 실측 자료로 확인됨.
# 나머지는 추정값이므로 다르면 config.json 의 "urban_regions" 로 바꿀 수 있습니다.
URBAN_REGIONS = [
    {"code": "01", "name": "수도권(서울·인천)"},
    {"code": "02", "name": "부산"},
    {"code": "03", "name": "대구"},
    {"code": "04", "name": "광주"},
    {"code": "05", "name": "대전"},
]
# 노선 조회에 쓸 노선코드 후보 (권역 전체 조회가 안 될 때만 사용)
URBAN_LINE_CANDIDATES = (
    [str(i) for i in range(1, 10)]
    + ["A1", "B1", "D1", "E1", "G1", "I1", "I2", "K1", "K2", "K4", "KK", "S1",
       "SH", "U1", "UI", "W1", "WS", "GX"]
)
OPERATOR_NAMES = {  # 응답에 기관명이 없을 때 쓰는 표시 이름
    "S1": "서울교통공사", "KR": "코레일", "BS": "부산교통공사", "IC": "인천교통공사",
    "DG": "대구교통공사", "GJ": "광주교통공사", "DJ": "대전교통공사",
}
SEOUL_SUBWAY_IDS = {
    "1001": "1호선", "1002": "2호선", "1003": "3호선", "1004": "4호선", "1005": "5호선",
    "1006": "6호선", "1007": "7호선", "1008": "8호선", "1009": "9호선",
    "1061": "중앙선", "1063": "경의중앙선", "1065": "공항철도", "1067": "경춘선",
    "1075": "수인분당선", "1077": "신분당선", "1081": "경강선", "1092": "우이신설선",
    "1093": "서해선", "1094": "신림선", "1032": "GTX-A",
}

# 2026~2027 법정 공휴일(도시철도 휴일 시간표 판정용). config.json 의 "holidays" 로 추가 가능.
HOLIDAYS = {
    "20260101", "20260216", "20260217", "20260218", "20260301", "20260302",
    "20260505", "20260524", "20260525", "20260603", "20260606", "20260815",
    "20260817", "20260924", "20260925", "20260926", "20261003", "20261005",
    "20261009", "20261225",
    "20270101", "20270206", "20270207", "20270208", "20270209", "20270301",
    "20270505", "20270513", "20270606", "20270815", "20270816", "20270914",
    "20270915", "20270916", "20271003", "20271004", "20271009", "20271011",
    "20271225",
}


# ─────────────────────────────────────────────────────────────
# 설정
# ─────────────────────────────────────────────────────────────
def load_config():
    cfg = {}
    path = os.path.join(BASE_DIR, "config.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
    env_map = {"DATA_GO_KR_KEY": "data_go_kr_key", "KRIC_KEY": "kric_key",
               "SEOUL_KEY": "seoul_key"}
    for env, k in env_map.items():
        if os.environ.get(env):
            cfg[k] = os.environ[env]
    for k in ("data_go_kr_key", "kric_key", "seoul_key"):
        v = (cfg.get(k) or "").strip()
        if v.startswith("여기에") or v.startswith("YOUR"):
            v = ""
        # 포털의 'Encoding' 키를 넣어도 동작하도록 한 번 디코딩
        if "%" in v:
            v = urllib.parse.unquote(v)
        cfg[k] = v
    if cfg.get("intercity_lines"):
        INTERCITY_LINES.update(cfg["intercity_lines"])
    if cfg.get("urban_regions"):
        URBAN_REGIONS[:] = cfg["urban_regions"]
    HOLIDAYS.update(cfg.get("holidays", []))
    cfg["demo"] = bool(os.environ.get("DEMO")) or cfg.get("demo", False)
    return cfg


CFG = {}


# ─────────────────────────────────────────────────────────────
# 공통 유틸
# ─────────────────────────────────────────────────────────────
class ApiError(Exception):
    pass


_mem_cache = {}
_mem_lock = threading.Lock()


def cached(key, ttl, fn):
    now = time.time()
    with _mem_lock:
        hit = _mem_cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    val = fn()
    with _mem_lock:
        _mem_cache[key] = (now, val)
    return val


def disk_cache(name, max_age_days, fn):
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, name)
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < max_age_days * 86400:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    val = fn()
    if val:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(val, f, ensure_ascii=False)
    return val


def http_json(url, params=None, timeout=15):
    if params:
        url = url + "?" + urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(url, headers={"User-Agent": "train-board/1.0",
                                               "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        raise ApiError(f"HTTP {e.code} ({url.split('?')[0]})")
    except Exception as e:  # 네트워크 오류
        raise ApiError(f"연결 실패: {e}")
    raw = raw.strip()
    if raw.startswith("<"):
        # data.go.kr 게이트웨이 오류는 XML로 옴
        m = re.search(r"<returnAuthMsg>(.*?)</returnAuthMsg>", raw) or \
            re.search(r"<resultMsg>(.*?)</resultMsg>", raw) or \
            re.search(r"<errMsg>(.*?)</errMsg>", raw)
        raise ApiError("API 오류: " + (m.group(1) if m else raw[:150]))
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise ApiError("JSON이 아닌 응답: " + raw[:150])


def find_items(obj, required):
    """응답 구조가 조금 달라도 필요한 키를 가진 dict 들을 모두 찾아낸다."""
    out = []

    def walk(o):
        if isinstance(o, dict):
            if all(k in o for k in required):
                out.append(o)
                return
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(obj)
    return out


def find_result_code(obj):
    """resultCode / resultMsg 를 응답 어디서든 찾아낸다."""
    found = {}

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                lk = k.lower()
                if lk in ("resultcode", "result_code", "code") and "code" not in found and isinstance(v, (str, int)):
                    found["code"] = str(v)
                elif lk in ("resultmsg", "result_msg", "message") and "msg" not in found and isinstance(v, str):
                    found["msg"] = v
                else:
                    walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(obj)
    return found.get("code"), found.get("msg")


def norm_name(s):
    s = re.sub(r"\(.*?\)", "", str(s))
    s = s.replace(" ", "").replace("·", "").replace(".", "")
    if s.endswith("역") and len(s) > 2:
        s = s[:-1]
    return s.upper()


def hhmm_from_any(v):
    """'053000', '05:30:00', '0530', 20261119053000 등 → (분 단위 정수, 'HH:MM')."""
    if v is None:
        return None, ""
    d = re.sub(r"\D", "", str(v))
    if len(d) >= 12:          # YYYYMMDDHHMM(SS)
        d = d[8:]
    if len(d) < 3:
        return None, ""
    if len(d) in (3, 5):
        d = "0" + d
    h, m = int(d[:2]), int(d[2:4])
    return h * 60 + m, f"{h % 24:02d}:{m:02d}"


def service_minutes(mins):
    """새벽 0~2시대는 전날 운행일의 24시 이후로 본다."""
    if mins is None:
        return None
    return mins + 1440 if mins < 180 else mins


def parse_window(date, t_from, t_to):
    now = dt.datetime.now(KST)
    ymd = (date or now.strftime("%Y%m%d")).replace("-", "")
    f, _ = hhmm_from_any(t_from or now.strftime("%H%M"))
    t, _ = hhmm_from_any(t_to) if t_to else (None, "")
    if t is None:
        t = f + 60
    if t <= f:
        t += 1440
    return ymd, f, t


def in_window(mins, f, t):
    if mins is None:
        return False
    for m in (mins, mins + 1440, mins - 1440):
        if f <= m <= t:
            return True
    return False


# ─────────────────────────────────────────────────────────────
# TAGO 간선 열차
# ─────────────────────────────────────────────────────────────
def tago(op, **params):
    key = CFG.get("data_go_kr_key")
    if not key:
        raise ApiError("공공데이터포털 인증키(data_go_kr_key)가 없습니다.")
    params.update(serviceKey=key, _type="json")
    data = http_json(f"{TAGO_URL}/{op}", params)
    code, msg = find_result_code(data)
    if code and code not in ("00", "0", "INFO-000"):
        if code in ("03",):  # 데이터 없음
            return []
        raise ApiError(f"TAGO {op} 오류 {code}: {msg}")
    return data


def tago_station_index():
    def build():
        cities = find_items(tago("GetCtyCodeList"), ["citycode"])
        stations = {}

        def one(c):
            d = tago("GetCtyAcctoTrainSttnList", cityCode=c["citycode"], numOfRows=1000)
            return find_items(d, ["nodeid", "nodename"])
        with ThreadPoolExecutor(8) as ex:
            for rows in ex.map(one, cities):
                for r in rows:
                    stations[r["nodename"]] = r["nodeid"]
        return stations
    return disk_cache("tago_stations.json", 30, build)


def resolve_tago(name, index):
    if name in index:
        return index[name]
    n = norm_name(name)
    for k, v in index.items():
        if norm_name(k) == n:
            return v
    for k, v in index.items():
        if norm_name(k).startswith(n) or n.startswith(norm_name(k)):
            return v
    return None


def tago_trains(dep_id, arr_id, ymd):
    def fetch():
        d = tago("GetStrtpntAlocFndTrainInfo", depPlaceId=dep_id, arrPlaceId=arr_id,
                 depPlandTime=ymd, numOfRows=500, pageNo=1)
        return find_items(d, ["trainno", "depplandtime"])
    return cached(("tago", dep_id, arr_id, ymd), 1800, fetch)


# TAGO 등급명 → 화면 표시 이름. config.json "grade_labels" 로 바꿀 수 있음.
GRADE_LABELS = {"KTX": "KTX-1"}


def vehicle_label(train_no, grade):
    """열차번호별 지정(config "train_vehicles") > 등급명 변환 > 원래 등급명."""
    by_no = CFG.get("train_vehicles") or {}
    no = str(train_no).lstrip("0")
    if no in by_no:
        return by_no[no], True
    labels = dict(GRADE_LABELS, **(CFG.get("grade_labels") or {}))
    g = str(grade).strip()
    return labels.get(g, g), False


def grade_class(name):
    n = str(name).upper()
    if "SRT" in n:
        return "srt"
    if "와인" in n:
        return "wine"
    if "청룡" in n:
        return "cheongryong"
    if "이음" in n:
        return "eum"
    if "산천" in n:
        return "sancheon"
    if "KTX" in n:
        return "ktx"
    if "ITX" in n or "새마을" in n:
        return "itx"
    if "무궁화" in n or "누리로" in n:
        return "mugunghwa"
    return "etc"


# 노선 밖으로 이어지는 열차의 진짜 시발·종착역을 찾기 위한 후보 (마산행, 포항행 등)
TERMINALS = ["행신", "서울", "용산", "청량리", "수서", "부산", "부전", "마산", "창원", "창원중앙", "진영", "진주",
             "포항", "태화강", "목포", "광주송정", "여수EXPO", "순천", "강릉", "동해", "정동진", "익산", "전주",
             "안동", "영주", "제천", "원주", "대구", "밀양"]


def _mins_between(a, b):
    """a 시각에서 b 시각까지 걸린 분 (자정 넘김 처리)."""
    if a is None or b is None:
        return None
    return (b - a) % 1440


def probe_station(station, names, ymd, warnings=None):
    """station ↔ names 의 모든 역 쌍을 TAGO로 조회. {이름: (station→이름 결과, 이름→station 결과)}"""
    index = tago_station_index()
    sid = resolve_tago(station, index)
    if not sid:
        raise ApiError(f"TAGO에서 '{station}'역을 찾지 못했습니다.")
    targets = []
    for n in dict.fromkeys(names):
        if n == station:
            continue
        oid = resolve_tago(n, index)
        if oid and oid != sid:
            targets.append((n, oid))
        elif not oid and warnings is not None and n not in TERMINALS:
            warnings.append(f"'{n}'역 ID를 찾지 못해 제외")

    def run(item):
        n, oid = item
        out = []
        for a, b in ((sid, oid), (oid, sid)):
            try:
                out.append(tago_trains(a, b, ymd))
            except ApiError as e:
                out.append([])
                if warnings is not None:
                    warnings.append(f"{n}: {e}")
        return n, out[0], out[1]

    result = {}
    with ThreadPoolExecutor(8) as ex:
        for n, fwd, back in ex.map(run, targets):
            result[n] = (fwd, back)
    return result


def intercity_board(line_id, station, ymd, f, t):
    line = INTERCITY_LINES.get(line_id)
    if not line:
        raise ApiError("알 수 없는 노선")
    names = line["stations"]
    if station not in names:
        raise ApiError("이 노선에 없는 역")
    idx = names.index(station)
    warnings = []
    res = probe_station(station, names + TERMINALS, ymd, warnings)

    trains = {}
    for n, (fwd, back) in res.items():
        j = names.index(n) if n in names else None
        for r in fwd:                      # 이 역 → n
            no = str(r.get("trainno")).lstrip("0") or "0"
            tr = trains.setdefault(no, {"train_no": no, "grade": r.get("traingradename", ""), "dep": None,
                                        "arr": None, "dest": None, "dest_m": -1, "origin": None,
                                        "origin_m": -1, "direction": None})
            tr["dep"] = str(r.get("depplandtime"))
            m = _mins_between(hhmm_from_any(r.get("depplandtime"))[0], hhmm_from_any(r.get("arrplandtime"))[0])
            if m is not None and m > tr["dest_m"]:          # 가장 오래 가는 역 = 종착역
                tr["dest"], tr["dest_m"] = r.get("arrplacename") or n, m
            if j is not None and not tr["direction"]:
                tr["direction"] = "down" if j > idx else "up"
        for r in back:                     # n → 이 역
            no = str(r.get("trainno")).lstrip("0") or "0"
            tr = trains.setdefault(no, {"train_no": no, "grade": r.get("traingradename", ""), "dep": None,
                                        "arr": None, "dest": None, "dest_m": -1, "origin": None,
                                        "origin_m": -1, "direction": None})
            tr["arr"] = str(r.get("arrplandtime"))
            m = _mins_between(hhmm_from_any(r.get("depplandtime"))[0], hhmm_from_any(r.get("arrplandtime"))[0])
            if m is not None and m > tr["origin_m"]:        # 가장 멀리서 온 역 = 시발역
                tr["origin"], tr["origin_m"] = r.get("depplacename") or n, m
            if j is not None and not tr["direction"]:
                tr["direction"] = "up" if j > idx else "down"

    out = []
    for tr in trains.values():
        dep_m, dep_s = hhmm_from_any(tr["dep"])
        arr_m, arr_s = hhmm_from_any(tr["arr"])
        key_m = dep_m if dep_m is not None else arr_m
        if not (in_window(dep_m, f, t) or in_window(arr_m, f, t)):
            continue
        label, custom = vehicle_label(tr["train_no"], tr["grade"])
        best = form_best(ic_key(tr["train_no"]), ymd)
        if best:
            label, custom = best, True
        status = "시발" if not tr["arr"] else ("종착" if not tr["dep"] else "정차")
        out.append({
            "time": dep_s or arr_s, "sort": service_minutes(key_m),
            "arr": arr_s, "dep": dep_s, "train_no": tr["train_no"],
            "grade": label, "grade_raw": tr["grade"], "custom_vehicle": custom,
            "grade_class": grade_class(label),
            "from": tr["origin"] or station, "to": tr["dest"] or station,
            "direction": {"up": "상행", "down": "하행"}.get(tr["direction"], ""),
            "status": status,
        })
    out.sort(key=lambda x: x["sort"])
    return {"trains": out, "warnings": warnings,
            "note": "코레일 간선 열차는 실시간 위치를 공개 API로 제공하지 않아 계획 시각만 표시합니다."}


# ─────────────────────────────────────────────────────────────
# KRIC 도시철도
# ─────────────────────────────────────────────────────────────
def kric(op, **params):
    key = CFG.get("kric_key")
    if not key:
        raise ApiError("철도산업정보센터 인증키(kric_key)가 없습니다.")
    params.update(serviceKey=key, format="json")
    data = http_json(f"{KRIC_URL}/{op}", params)
    code, msg = find_result_code(data)
    if code and code not in ("00", "0", "200"):
        if code == "03":   # 데이터 없음
            return {}
        raise ApiError(f"KRIC {op} 오류 {code}: {msg}")
    return data


def _route_rows(region_code, ln=None):
    p = {"mreaWideCd": region_code}
    if ln:
        p["lnCd"] = ln
    try:
        return find_items(kric("subwayRouteInfo", **p), ["stinCd", "stinNm"])
    except ApiError:
        return []


def urban_lines():
    def build():
        rows_all = []
        for reg in URBAN_REGIONS:
            rows = _route_rows(reg["code"])
            if not rows:
                with ThreadPoolExecutor(8) as ex:
                    for rs in ex.map(lambda ln: _route_rows(reg["code"], ln), URBAN_LINE_CANDIDATES):
                        rows.extend(rs)
            for r in rows:
                r["_region"] = reg["name"]
            rows_all.extend(rows)
        lines = {}
        for r in rows_all:
            opr = str(r.get("railOprIsttCd", ""))
            ln = str(r.get("lnCd", ""))
            k = f"{opr}|{ln}"
            name = (r.get("lnNm") or r.get("routNm") or r.get("lnCdNm") or f"{ln}호선")
            opr_name = r.get("railOprIsttNm") or OPERATOR_NAMES.get(opr, opr)
            L = lines.setdefault(k, {"key": k, "opr": opr, "ln": ln, "region": r["_region"],
                                     "operator": opr_name, "name": str(name), "stations": [],
                                     "_seen": set()})
            sc = str(r["stinCd"])
            if sc in L["_seen"]:
                continue
            L["_seen"].add(sc)
            order = r.get("stinConsOrdr")
            try:
                order = float(order)
            except (TypeError, ValueError):
                order = len(L["stations"])
            L["stations"].append({"code": sc, "name": str(r["stinNm"]), "_o": order})
        result = []
        for L in lines.values():
            L["stations"].sort(key=lambda s: s["_o"])
            for s in L["stations"]:
                s.pop("_o", None)
            L.pop("_seen", None)
            result.append(L)
        result.sort(key=lambda L: (L["region"], L["operator"], L["name"]))
        return result

    lines = disk_cache("kric_lines.json", 30, build)
    manual = CFG.get("urban_lines_manual") or []
    for m in manual:  # 수동 정의 노선 (자동 조회가 안 될 때)
        m.setdefault("key", f"{m['opr']}|{m['ln']}")
        m["stations"] = [s if isinstance(s, dict) else {"code": s[0], "name": s[1]}
                         for s in m["stations"]]
        if not any(L["key"] == m["key"] for L in lines):
            lines.append(m)
    return cached(("branch_lines", len(lines)), 600, lambda: build_branch_lines(lines))



# ─────────────────────────────────────────────────────────────
# 갈래가 있는 노선 (1호선): 실제 모양대로 갈래별 노선으로 나눠 보여 준다
#   데이터에는 경원·경부·경인·장항 구간이 한 목록에 섞여 오므로 역 이름으로 다시 줄 세운다.
#   config.json 의 "branch_paths" 로 바꾸거나 다른 노선을 추가할 수 있다.
# ─────────────────────────────────────────────────────────────
_L1_NORTH = ["연천", "전곡", "청산", "소요산", "동두천", "보산", "동두천중앙", "지행", "덕정", "덕계", "양주",
             "녹양", "가능", "의정부", "회룡", "망월사", "도봉산", "도봉", "방학", "창동", "녹천", "월계",
             "광운대", "석계", "신이문", "외대앞", "회기", "청량리", "제기동", "신설동", "동묘앞", "동대문",
             "종로5가", "종로3가", "종각", "시청", "서울역", "남영", "용산", "노량진", "대방", "신길", "영등포",
             "신도림", "구로"]
_L1_GYEONGIN = ["구일", "개봉", "오류동", "온수", "역곡", "소사", "부천", "중동", "송내", "부개", "부평", "백운",
                "동암", "간석", "주안", "도화", "제물포", "도원", "동인천", "인천"]
_L1_GYEONGBU = ["가산디지털단지", "독산", "금천구청", "석수", "관악", "안양", "명학", "금정", "군포", "당정",
                "의왕", "성균관대", "화서", "수원", "세류", "병점", "세마", "오산대", "오산", "진위", "송탄",
                "서정리", "평택지제", "평택", "성환", "직산", "두정", "천안", "봉명", "쌍용", "아산", "탕정",
                "배방", "온양온천", "신창"]
BRANCH_PATHS = {
    "1호선": {
        "seoul_name": "1호선",
        "paths": [
            ("1호선 · 인천 방면 (경인선)", _L1_NORTH + _L1_GYEONGIN),
            ("1호선 · 신창 방면 (경부·장항선)", _L1_NORTH + _L1_GYEONGBU),
            ("1호선 · 광명 셔틀", ["영등포", "신도림", "구로", "가산디지털단지", "독산", "금천구청", "광명"]),
            ("1호선 · 서동탄 지선", ["수원", "세류", "병점", "서동탄"]),
        ],
    },
}


def build_branch_lines(lines):
    """섞여 있는 노선을 갈래별 가상 노선으로 바꾼다. 원래 노선은 목록에서 숨긴다."""
    groups = dict(BRANCH_PATHS)
    groups.update(CFG.get("branch_paths") or {})
    out, hidden = [], set()
    for gname, g in groups.items():
        all_names = {norm_name(n) for _, p in g["paths"] for n in p}
        for region in {L.get("region") for L in lines}:
            rl = [L for L in lines if L.get("region") == region]
            # 이 갈래 묶음과 역이 6개 이상 겹치는 노선 = 같은 노선 계열 (예: 코레일 1호선, 서울교통공사 1호선)
            fam = []
            for L in rl:
                ov = sum(1 for s_ in L["stations"] if norm_name(s_["name"]) in all_names)
                if ov >= 6 and ov >= len(L["stations"]) * 0.6:
                    fam.append((ov, L))
            if not fam:
                continue
            fam.sort(key=lambda p: -p[0])
            where = {}                                  # 역 이름 → 이 역이 들어 있는 모든 (기관, 노선, 역코드)
            for _, L in fam:
                for s_ in L["stations"]:
                    where.setdefault(norm_name(s_["name"]), []).append(
                        {"opr": L["opr"], "ln": L["ln"], "stin": str(s_["code"]), "name": s_["name"]})
            made = 0
            for j, (pname, path) in enumerate(g["paths"]):
                sts = []
                for n in path:
                    alts = where.get(norm_name(n))
                    if not alts:
                        continue
                    a0 = alts[0]
                    sts.append({"code": f"{a0['opr']}~{a0['ln']}~{a0['stin']}", "name": a0["name"], "alts": alts})
                if len(sts) >= 2:
                    out.append({"key": f"V~{gname}~{j}", "opr": fam[0][1]["opr"], "ln": fam[0][1]["ln"],
                                "region": region, "operator": "코레일·서울교통공사" if len(fam) > 1 else fam[0][1]["operator"],
                                "name": pname, "stations": sts, "virtual": True, "seoul_name": g.get("seoul_name", gname)})
                    made += 1
            if made:
                hidden |= {L["key"] for _, L in fam}
    return [L for L in lines if L["key"] not in hidden] + out


def station_timetable(L, st, day):
    """역 하나의 시간표. 갈래 노선의 역은 여러 데이터(기관·노선)에 걸쳐 있을 수 있어 모두 합친다."""
    alts = st.get("alts") or [{"opr": L["opr"], "ln": L["ln"], "stin": st["code"]}]
    rows, seen_, err = [], set(), None
    for a in alts:
        try:
            for r in kric_station_timetable(a["opr"], a["ln"], a["stin"], day):
                k = (str(r.get("trnNo")), str(r.get("arvTm")), str(r.get("dptTm")))
                if k not in seen_:
                    seen_.add(k)
                    rows.append(r)
        except ApiError as e:
            err = e
    if not rows and err:
        raise err
    return rows


def day_code(ymd):
    d = dt.datetime.strptime(ymd, "%Y%m%d").date()
    if ymd in HOLIDAYS or d.weekday() == 6:
        return "9"
    if d.weekday() == 5:
        return "7"
    return "8"


def kric_station_timetable_raw(opr, ln, stin, day):
    # 급행 표시가 있는 Exp 버전을 먼저 쓰고, 없으면 일반 버전
    last_err = None
    for op in ("subwayTimetableExp", "subwayTimetable"):
        try:
            rows = find_items(kric(op, railOprIsttCd=opr, lnCd=ln, stinCd=stin, dayCd=day), ["trnNo"])
        except ApiError as e:
            rows, last_err = [], e
        if rows:
            return rows
    if last_err:
        raise last_err
    return []


def _tt(opr, ln, stin, day):
    safe = re.sub(r"[^0-9A-Za-z_-]", "_", f"{opr}_{ln}_{stin}_{day}")
    return cached(("kric", opr, ln, stin, day), 6 * 3600,
                  lambda: disk_cache(f"tt_{safe}.json", 1, lambda: kric_station_timetable_raw(opr, ln, stin, day)) or [])


def kric_station_timetable(opr, ln, stin, day):
    rows = _tt(opr, ln, stin, day)
    if not rows and day == "7":   # 토요일 시간표가 따로 없는 기관은 휴일 시간표 사용
        rows = _tt(opr, ln, stin, "9")
    return rows


def express_kind(row):
    """'급행', '특급', '준급행' 등 급행 종류 글자. 일반 열차면 None."""
    words = []
    for k, v in row.items():
        sv = str(v)
        for w in re.findall(r"[가-힣A-Za-z]*(?:급행|특급)", sv):
            words.append(w)
    if words:
        w = min(words, key=len)                      # '동인천급행' 같은 긴 글자보다 '급행'/'준급행'
        for kind in ("준급행", "특급", "급행"):
            if kind in w:
                return kind
    for k, v in row.items():
        if "exp" in k.lower() and str(v).upper() in ("Y", "1", "E", "TRUE"):
            return "급행"
    return None


def is_express(row):
    for k, v in row.items():
        sv = str(v)
        if "급행" in sv or "특급" in sv:
            return True
        if "exp" in k.lower() and sv.upper() in ("Y", "1", "E", "TRUE"):
            return True
    return False


# 도시철도 열차가 끝나는 역 후보 (행선지·시발역 찾기용). 노선에 있는 역만 쓴다.
URBAN_TERMINALS = [
    # 수도권 1호선·경인·경부·경원·장항
    "소요산", "동두천", "양주", "의정부", "광운대", "청량리", "서울역", "용산", "구로", "영등포", "신도림",
    "인천", "동인천", "부평", "주안", "병점", "서동탄", "천안", "신창", "신창(순천향대)", "수원", "평택", "오산",
    # 수도권 기타
    "오이도", "안산", "금정", "사당", "불암산", "당고개", "진접", "대화", "구파발", "오금", "수서", "성수",
    "까치산", "방화", "마천", "상일동", "하남검단산", "응암", "봉화산", "신내", "장암", "도봉산", "석남",
    "개화", "중앙보훈병원", "문산", "지평", "용문", "덕소", "도농", "춘천", "평내호평", "상봉", "왕십리",
    "죽전", "고색", "판교", "광교", "신사", "일산", "원시", "소사", "강남", "선릉", "삼성", "홍대입구",
    "계양", "송도달빛축제공원", "검단호수공원", "운연", "별내", "북한산우이", "신설동", "관악산", "샛강",
    "김포공항", "양촌", "기흥", "전대·에버랜드", "인천공항2터미널", "검암", "청라국제도시",
    # 부산·대구·광주·대전
    "노포", "다대포해수욕장", "신평", "서면", "장산", "양산", "호포", "대저", "수영", "안평", "미남", "사상",
    "설화명곡", "안심", "하양", "문양", "칠곡경대병원", "용지", "녹동", "평동", "판암", "반석",
]


def _plain(name):
    """표시용 역 이름: 괄호 설명과 끝의 '역'을 뗀다 (서울역 → 서울, 신창(순천향대) → 신창)."""
    n = re.sub(r"\(.*?\)", "", str(name)).strip()
    return n[:-1] if n.endswith("역") and len(n) > 2 else n


def urban_board(line_key, stin, ymd, f, t):
    lines = {L["key"]: L for L in urban_lines()}
    L = lines.get(line_key)
    if not L:
        raise ApiError("알 수 없는 노선")
    codes = [s["code"] for s in L["stations"]]
    if stin not in codes:
        raise ApiError("이 노선에 없는 역")
    idx = codes.index(stin)
    day = day_code(ymd)
    rows = station_timetable(L, L["stations"][idx], day)
    warnings = []
    if not rows:
        warnings.append("이 역의 시간표 데이터가 없습니다 (해당 요일 미제공일 수 있음).")

    # 종착역 후보: 노선 양 끝 + 알려진 종착역 중 이 노선에 있는 역
    tset = {norm_name(x) for x in URBAN_TERMINALS}
    seen = {}            # 열차번호키 → [(역 이름, 시각(분))]

    def scan(Lx, skip_idx=None):
        cand = [i for i, s_ in enumerate(Lx["stations"])
                if i != skip_idx and (i in (0, len(Lx["stations"]) - 1) or norm_name(s_["name"]) in tset)]

        def load(i):
            try:
                return i, station_timetable(Lx, Lx["stations"][i], day), None
            except ApiError as e:
                return i, [], str(e)
        with ThreadPoolExecutor(8) as ex:
            for i, trs, err in ex.map(load, cand):
                if err:
                    warnings.append(f"{Lx['stations'][i]['name']} 시간표 조회 실패: {err}")
                for r in trs:
                    m = service_minutes(hhmm_from_any(r.get("arvTm") or r.get("dptTm"))[0])
                    if m is not None:
                        for k in trn_keys(r.get("trnNo", "")):
                            seen.setdefault(k, []).append((Lx["stations"][i]["name"], m))

    def ends_for(no, here):
        pts = {}
        for k in trn_keys(no):
            for n, m in seen.get(k, []):
                pts[(n, m)] = 1
        later = [(n, m) for n, m in pts if here is not None and 0 < m - here <= 300]
        earlier = [(n, m) for n, m in pts if here is not None and 0 < here - m <= 300]
        return (max(later, key=lambda x: x[1])[0] if later else "",
                min(earlier, key=lambda x: x[1])[0] if earlier else "")

    scan(L, idx)

    # 1호선처럼 운영기관별로 쪼개진 노선: 행선지가 안 잡히면 이어지는 노선(같은 역을 가진 노선)까지 찾아본다
    def needs_more():
        for r in rows:
            here = service_minutes(hhmm_from_any(r.get("dptTm") or r.get("arvTm"))[0])
            d, o = ends_for(r["trnNo"], here)
            if (r.get("dptTm") and not d) or (r.get("arvTm") and not o):
                return True
        return False

    if rows and needs_more():
        region_lines = [x for x in urban_lines() if x.get("region") == L.get("region") and x["key"] != L["key"]]
        visited, frontier, extra = {L["key"]}, [L], []
        for _depth in range(2):
            nxt = []
            for cur in frontier:
                names_cur = {norm_name(s_["name"]) for s_ in cur["stations"]}
                for x in region_lines:
                    if x["key"] in visited:
                        continue
                    shared = len(names_cur & {norm_name(s_["name"]) for s_ in x["stations"]})
                    if shared:
                        visited.add(x["key"])
                        nxt.append((shared, x))
            nxt.sort(key=lambda p: -p[0])
            frontier = [x for _, x in nxt]
            extra += frontier
        for x in extra[:8]:
            scan(x)
        if needs_more():                 # 그래도 못 찾으면 같은 지역 모든 노선의 종착역 후보까지
            done = {x["key"] for x in extra[:8]} | {L["key"]}
            for x in region_lines[:30]:
                if x["key"] not in done:
                    scan(x)

    out = []
    for r in rows:
        arr_m, arr_s = hhmm_from_any(r.get("arvTm"))
        dep_m, dep_s = hhmm_from_any(r.get("dptTm"))
        key_m = dep_m if dep_m is not None else arr_m
        if not (in_window(dep_m, f, t) or in_window(arr_m, f, t)):
            continue
        no = str(r["trnNo"])
        here = service_minutes(key_m)
        dest, origin = ends_for(no, here)
        status = "종착" if not dep_s else ("시발" if not arr_s else "정차")
        kind = express_kind(r)
        if status == "종착":
            dest = L["stations"][idx]["name"]            # 이 역이 종착 (예: 용산급행)
            to_txt = "이 역 종착"
        elif dest:
            to_txt = f"{_plain(dest)}행"
        else:
            to_txt = "행선지 미확인"
        grade = (f"{_plain(dest)}{kind}" if dest else kind) if kind else "일반"
        out.append({
            "time": dep_s or arr_s, "sort": here, "arr": arr_s, "dep": dep_s,
            "train_no": no, "formation": form_best(urban_key(line_key, no), ymd),
            "grade": grade, "express_kind": kind or "",
            "grade_class": "express" if kind else "local",
            "to": to_txt, "from": _plain(origin) if origin else "", "status": status,
        })
    out.sort(key=lambda x: (x["sort"] if x["sort"] is not None else 99999))
    if str(L.get("region", "")).startswith("수도권") and CFG.get("seoul_key") and ymd == dt.datetime.now(KST).strftime("%Y%m%d"):
        try:
            live = {}
            for tr in linemap(line_key)["trains"]:
                for k in trn_keys(tr["no"]):
                    live[k] = tr
            for row in out:
                tr = next((live[k] for k in trn_keys(row["train_no"]) if k in live), None)
                if tr:
                    row["live"] = {"delay": tr.get("delay"), "parked": bool(tr.get("parked")),
                                   "stale": bool(tr.get("stale")), "where": names_at(L, tr["i"]),
                                   "status": tr.get("status", "")}
        except Exception as e:
            warnings.append(f"실시간 지연을 붙이지 못했습니다: {e}")
    return {"trains": out, "warnings": warnings,
            "day": {"8": "평일", "7": "토요일", "9": "휴일"}[day],
            "note": "KRIC 열차별 운행시각표 기준. 행선지는 종착역 후보들의 시간표에서 같은 열차번호를 찾아 계산합니다."}


def names_at(L, i):
    try:
        return L["stations"][i]["name"]
    except (IndexError, KeyError, TypeError):
        return ""


def urban_raw(line_key, stin):
    """확인용: 이 역 시간표 원본 몇 줄 (필드 이름 확인)."""
    L = next((x for x in urban_lines() if x["key"] == line_key), None)
    if not L:
        raise ApiError("알 수 없는 노선")
    st = next((x for x in L["stations"] if x["code"] == stin or norm_name(x["name"]) == norm_name(stin)), None)
    if not st:
        raise ApiError("이 노선에 없는 역")
    a = (st.get("alts") or [{"opr": L["opr"], "ln": L["ln"], "stin": st["code"]}])[0]
    day = day_code(dt.datetime.now(KST).strftime("%Y%m%d"))
    out = {"station": st["name"], "source": a}
    for op in ("subwayTimetableExp", "subwayTimetable"):
        try:
            data = kric(op, railOprIsttCd=a["opr"], lnCd=a["ln"], stinCd=a["stin"], dayCd=day)
            rows = find_items(data, ["trnNo"])
            exp = [r for r in rows if express_kind(r)]
            out[op] = {"count": len(rows), "sample": rows[:3], "express_sample": exp[:5]}
        except ApiError as e:
            out[op] = {"error": str(e)}
    return out


# ─────────────────────────────────────────────────────────────
# 서울 실시간 (수도권)
# ─────────────────────────────────────────────────────────────
def seoul_realtime(name):
    key = CFG.get("seoul_key")
    if not key:
        raise ApiError("서울 열린데이터광장 인증키(seoul_key)가 없습니다.")
    q = re.sub(r"\(.*?\)", "", name).strip()
    if q.endswith("역") and len(q) > 2:
        q = q[:-1]
    url = SEOUL_RT_URL.format(key=urllib.parse.quote(key), name=urllib.parse.quote(q))

    def fetch():
        d = http_json(url)
        rows = find_items(d, ["subwayId", "arvlMsg2"])
        out = []
        for r in rows:
            out.append({
                "line": SEOUL_SUBWAY_IDS.get(str(r.get("subwayId")), str(r.get("subwayId"))),
                "updn": r.get("updnLine", ""), "heading": r.get("trainLineNm", ""),
                "msg": r.get("arvlMsg2", ""), "where": r.get("arvlMsg3", ""),
                "train_no": str(r.get("btrainNo", "")).lstrip("0"),
                "kind": r.get("btrainSttus", "") or "일반",
                "dest": r.get("bstatnNm", ""), "seconds": r.get("barvlDt", ""),
                "received": r.get("recptnDt", ""),
            })
        if not out:
            code, msg = find_result_code(d)
            if code and not code.endswith("000"):
                raise ApiError(f"서울 실시간 {code}: {msg}")
        return out
    return cached(("seoulrt", q), 20, fetch)



# ─────────────────────────────────────────────────────────────
# 편성 공유 (친구들이 직접 입력)
# ─────────────────────────────────────────────────────────────
DATA_DIR = os.path.join(BASE_DIR, "data")
_form_lock = threading.Lock()


def _form_path():
    return os.path.join(DATA_DIR, "formations.json")


def form_load():
    try:
        with open(_form_path(), encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"entries": []}


def form_save(d):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = _form_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, _form_path())


def _clean(v, n):
    return re.sub(r"\s+", " ", str(v or "")).strip()[:n]


def _sort_entries(es):
    return sorted(es, key=lambda e: (-len(e.get("likes", [])), e.get("ts", 0) * -1))


def form_list(key, ymd):
    with _form_lock:
        es = [e for e in form_load()["entries"] if e["key"] == key and e["date"] == ymd]
    return _sort_entries(es)


def form_best(key, ymd):
    es = form_list(key, ymd)
    return es[0]["text"] if es else None


def form_add(key, ymd, text, name, where, label):
    key, text, name = _clean(key, 60), _clean(text, 40), _clean(name, 12) or "익명"
    if not key or not text:
        raise ApiError("편성 내용을 입력하세요.")
    import secrets
    now = dt.datetime.now(KST)
    e = {"id": secrets.token_hex(5), "key": key, "date": _clean(ymd, 8), "text": text, "name": name,
         "where": _clean(where, 30), "label": _clean(label, 40), "time": now.strftime("%H:%M"),
         "ts": int(now.timestamp()), "likes": [name]}
    with _form_lock:
        d = form_load()
        d["entries"].append(e)
        d["entries"] = d["entries"][-5000:]   # 너무 커지지 않게
        form_save(d)
    return e


def form_like(entry_id, name):
    name = _clean(name, 12) or "익명"
    with _form_lock:
        d = form_load()
        for e in d["entries"]:
            if e["id"] == entry_id:
                if name in e["likes"]:
                    e["likes"].remove(name)
                else:
                    e["likes"].append(name)
                form_save(d)
                return e
    raise ApiError("항목을 찾을 수 없습니다.")


def form_recent(limit=40):
    with _form_lock:
        es = form_load()["entries"]
    return sorted(es, key=lambda e: -e.get("ts", 0))[:limit]


def ic_key(no):
    return f"ic:{str(no).lstrip('0') or '0'}"


def urban_key(line_key, no):
    return f"u:{line_key}:{no}"


# ─────────────────────────────────────────────────────────────
# 노선도 (수도권 실시간 열차 위치)
# ─────────────────────────────────────────────────────────────
SEOUL_POS_URL = "http://swopenapi.seoul.go.kr/api/subway/{key}/json/realtimePosition/0/150/{line}"
POS_STATUS = {"0": "진입", "1": "도착", "2": "출발", "3": "전역출발"}


def seoul_line_name(L):
    over = (CFG.get("seoul_line_names") or {}).get(L["key"])
    if over:
        return over
    if L.get("seoul_name"):
        return L["seoul_name"]
    n = str(L.get("name", "")).strip()
    if re.fullmatch(r"\d+", n):
        return f"{n}호선"
    return n


def all_urban_lines():
    if is_demo() or not CFG.get("kric_key"):
        return DEMO_URBAN
    return urban_lines()



def secs_from_any(v):
    """'06:27:30', '062730', '2026-11-20 06:27:30', 20261120062730 → 하루 중 초. 실패하면 None."""
    if v is None:
        return None
    d = re.sub(r"\D", "", str(v))
    if len(d) >= 14:
        d = d[8:14]
    elif len(d) == 12:
        d = d[8:12] + "00"
    elif len(d) == 4:
        d = d + "00"
    elif len(d) == 5:
        d = "0" + d
    if len(d) != 6:
        return None
    return int(d[:2]) * 3600 + int(d[2:4]) * 60 + int(d[4:6])


def trn_keys(no):
    """열차번호 비교용 키: 'K1013' → {'K1013', '1013'}, '02201' → {'2201'}"""
    s_ = re.sub(r"[^0-9A-Za-z]", "", str(no)).upper()
    keys = {s_.lstrip("0") or s_}
    digits = re.sub(r"\D", "", s_).lstrip("0")
    if digits:
        keys.add(digits)
    return keys


def planned_line_times(L, day):
    """노선 전체 역 시간표 → {열차번호키: {원래 열차번호: {"st": {역이름키: (도착초, 출발초)}, "kind": 급행종류}}}"""
    def build():
        def one(st):
            try:
                return st["name"], station_timetable(L, st, day)
            except ApiError:
                return st["name"], []
        out = {}
        with ThreadPoolExecutor(8) as ex:
            for name, rows in ex.map(one, list(L["stations"])):
                for r in rows:
                    full = str(r.get("trnNo", ""))
                    v = (secs_from_any(r.get("arvTm")), secs_from_any(r.get("dptTm")))
                    kind = express_kind(r)
                    for k in trn_keys(full):
                        ent = out.setdefault(k, {}).setdefault(full, {"st": {}, "kind": None})
                        ent["st"][norm_name(name)] = v
                        ent["kind"] = ent["kind"] or kind
        return out
    return cached(("plan_line", L["key"], day), 6 * 3600, build)


def live_match(plan, no, station, status, recv):
    """서울 위치 정보와 시간표를 맞춰 본다. → {"delay": 초 또는 None, "kind": 급행종류 또는 None, "trn": 시간표 열차번호}

    숫자만 같은 다른 열차(예: K506 / S506)가 있을 수 있어서, 이 역 계획 시각이
    실제 시각과 가장 가까운 열차를 고른다.
    """
    cands = {}
    for k in trn_keys(no):
        cands.update(plan.get(k, {}))
    if not cands:
        return {"delay": None, "kind": None, "trn": None, "basis": None}
    actual = secs_from_any(recv)
    best, best_d = None, None
    for full, ent in cands.items():
        st = ent["st"].get(norm_name(station))
        if not st or actual is None:
            continue
        arr, dep = st
        p = (arr or dep) if status in ("도착", "진입") else (dep or arr)
        if p is None:
            continue
        d = (actual - p) % 86400
        d = d - 86400 if d > 43200 else d
        if best_d is None or abs(d) < abs(best_d):
            best, best_d = full, d
    if best is None:                              # 이 역 기록이 없으면 종류만
        full = sorted(cands)[0]
        return {"delay": None, "kind": cands[full]["kind"], "trn": full, "basis": None}
    ok = status in ("도착", "출발") and abs(best_d) <= 3600
    arr, dep = cands[best]["st"].get(norm_name(station))
    p = (arr or dep) if status in ("도착", "진입") else (dep or arr)
    fmt = lambda x: "" if x is None else f"{x // 3600 % 24:02d}:{x // 60 % 60:02d}:{x % 60:02d}"
    basis = {"station": station, "status": status, "plan": fmt(p), "actual": fmt(actual), "trn": best,
             "plan_kind": "도착" if status in ("도착", "진입") and arr else "출발"} if ok else None
    return {"delay": best_d if ok else None, "kind": cands[best]["kind"], "trn": best, "basis": basis}


def live_delay(plan, no, station, status, recv):
    return live_match(plan, no, station, status, recv)["delay"]


class LineTracker:
    """노선별로 오늘 본 열차를 기억해서, 운행을 마친 열차는 종점에 남겨 둔다.

    - 실시간 목록에 있는 열차: 그대로 표시 (운행 중)
    - 목록에서 사라졌는데 마지막 위치가 자기 행선지(종착역): '운행 종료'로 그 역에 고정
    - 목록에서 사라졌는데 중간역: 3분 동안 '신호 없음'으로 보여 준 뒤 지움
    - 종점에 세워 둔 열차는, 40분 안에 그 역에서 새 열차번호가 처음 나타나면
      같은 차량이 회차해 나간 것으로 보고 하나 지움 (추정)
    - 새벽 3시에 하루를 새로 시작
    """
    STALE_SEC = 180
    TURN_SEC = 40 * 60

    def __init__(self):
        self.lock = threading.Lock()
        self.lines = {}

    @staticmethod
    def service_day(now):
        return (now - dt.timedelta(hours=3)).strftime("%Y%m%d")

    def update(self, line_key, names, live, now=None):
        now = now or dt.datetime.now(KST)
        ts = now.timestamp()
        with self.lock:
            st = self.lines.get(line_key)
            if not st or st["day"] != self.service_day(now):
                st = self.lines[line_key] = {"day": self.service_day(now), "seen": {}, "parked": {}}
            seen, parked = st["seen"], st["parked"]
            live_nos = set()
            for t in live:
                no = t["no"]
                live_nos.add(no)
                if no not in seen and no not in parked:          # 처음 보는 열차
                    cands = [p for p in parked.values() if p["i"] == t["i"] and ts - p["parked_at"] < self.TURN_SEC]
                    if cands:                                     # 종점에서 회차해 나간 것으로 추정
                        parked.pop(min(cands, key=lambda p: p["parked_at"])["no"], None)
                parked.pop(no, None)
                prev = seen.get(no, {})
                if t.get("delay") is None and prev.get("delay") is not None:
                    # 진입·전역출발처럼 지금 잴 수 없을 때는 직전 값을 '직전 역 기준'으로 유지
                    t = {**t, "delay": prev["delay"], "delay_at": prev.get("delay_at"),
                         "basis": prev.get("basis"), "delay_old": True}
                seen[no] = {**t, "ts": ts}
            out = [{k: v for k, v in seen[t["no"]].items() if k != "ts"} for t in live]
            for no, info in list(seen.items()):
                if no in live_nos:
                    continue
                here = names[info["i"]] if 0 <= info["i"] < len(names) else ""
                if info.get("dest") and norm_name(here) == norm_name(info["dest"]):
                    parked[no] = {**info, "parked_at": info["ts"]}
                    del seen[no]
                elif ts - info["ts"] < self.STALE_SEC:
                    out.append({**info, "stale": True, "status": "신호 없음"})
                else:
                    del seen[no]
            for p in parked.values():
                out.append({k: v for k, v in p.items() if k not in ("ts", "parked_at")} |
                           {"parked": True, "status": "운행 종료", "express": False})
            return out


TRACKER = LineTracker()


def linemap(line_key):
    L = next((x for x in all_urban_lines() if x["key"] == line_key), None)
    if not L:
        raise ApiError("알 수 없는 노선")
    names = [s["name"] for s in L["stations"]]
    ymd = dt.datetime.now(KST).strftime("%Y%m%d")
    capital = str(L.get("region", "")).startswith("수도권")
    if is_demo():
        return demo_linemap(L, ymd)
    if not (capital and CFG.get("seoul_key")):
        return {"stations": names, "trains": [], "realtime": False,
                "note": "이 지역은 실시간 열차 위치를 공개 API로 제공하지 않습니다."}
    line = seoul_line_name(L)
    url = SEOUL_POS_URL.format(key=urllib.parse.quote(CFG["seoul_key"]), line=urllib.parse.quote(line))

    def fetch():
        return find_items(http_json(url), ["trainNo", "statnNm"])
    rows = cached(("pos", line), 12, fetch)
    idx = {norm_name(n): i for i, n in enumerate(names)}
    sday = (dt.datetime.now(KST) - dt.timedelta(hours=3)).strftime("%Y%m%d")
    try:
        plan = planned_line_times(L, day_code(sday))
    except Exception:
        plan = {}
    live, by_no = [], {}
    rank = {"도착": 0, "출발": 1, "진입": 2, "전역출발": 3}
    for r in rows:
        i = idx.get(norm_name(r.get("statnNm", "")))
        if i is None:
            continue
        no = str(r.get("trainNo", "")).lstrip("0")
        status = POS_STATUS.get(str(r.get("trainSttus")), "")
        prev = by_no.get(no)                       # 같은 열차가 두 번 오면 하나만 (도착·출발 우선)
        if prev and rank.get(prev["status"], 9) <= rank.get(status, 9):
            continue
        mt = live_match(plan, no, names[i], status, r.get("recptnDt") or r.get("lastRecptnDt"))
        da = str(r.get("directAt"))
        kind = mt["kind"] or {"1": "급행", "7": "특급"}.get(da)
        dest = r.get("statnTnm", "")
        t = {"i": i, "side": "a" if str(r.get("updnLine")) == "0" else "b", "no": no,
             "dest": dest, "express": bool(kind), "kind": kind or "",
             "express_label": f"{_plain(dest)}{kind}" if kind and dest else (kind or ""),
             "status": status, "delay": mt["delay"], "basis": mt["basis"],
             "delay_at": names[i] if mt["delay"] is not None else None}
        if prev:
            live.remove(prev)
        by_no[no] = t
        live.append(t)
    trains = TRACKER.update(line_key, names, live)
    for t in trains:
        t["formation"] = form_best(urban_key(line_key, t["no"]), ymd)
    n_run = sum(1 for t in trains if not t.get("parked"))
    n_park = len(trains) - n_run
    note = (f"운행 중 {n_run}대 · 운행 종료 {n_park}대" if trains else
            f"'{line}' 실시간 위치를 받지 못했습니다. (노선 이름이 서울 API와 다를 수 있음)" if not rows else "")
    return {"stations": names, "trains": trains, "realtime": True, "line": line, "note": note,
            "sides": ["상행·내선", "하행·외선"],
            "tracking_note": "운행 종료 열차는 이 서버가 켜진 뒤부터 본 열차만 표시됩니다."}


# ─────────────────────────────────────────────────────────────
# 열차 상세 (간선: 정차역 시각)
# ─────────────────────────────────────────────────────────────
def all_intercity_stations():
    names = []
    for L in INTERCITY_LINES.values():
        names += L["stations"]
    return list(dict.fromkeys(names + TERMINALS))


def intercity_train(no, ymd, station):
    """고른 역(station)에서 전국 주요역까지 조회해 이 열차의 정차역을 시각 순으로 모은다."""
    if not station:
        raise ApiError("역 정보가 없습니다.")
    no = str(no).lstrip("0")
    res = probe_station(station, all_intercity_stations(), ymd)
    here = {"name": station, "arr": "", "dep": "", "_m": None}
    stops, grade = [], ""
    for n, (fwd, back) in res.items():
        for r in fwd:
            if str(r.get("trainno")).lstrip("0") == no:
                grade = grade or r.get("traingradename", "")
                d0 = hhmm_from_any(r.get("depplandtime"))
                a1 = hhmm_from_any(r.get("arrplandtime"))
                here["dep"], here["_d"] = d0[1], d0[0]
                stops.append({"name": n, "arr": a1[1], "dep": "", "_rel": _mins_between(d0[0], a1[0])})
        for r in back:
            if str(r.get("trainno")).lstrip("0") == no:
                grade = grade or r.get("traingradename", "")
                d0 = hhmm_from_any(r.get("depplandtime"))
                a1 = hhmm_from_any(r.get("arrplandtime"))
                here["arr"], here["_a"] = a1[1], a1[0]
                stops.append({"name": n, "arr": "", "dep": d0[1], "_rel": -_mins_between(d0[0], a1[0])})
    if not stops:
        return [], grade
    stops.append({**here, "_rel": 0})
    stops.sort(key=lambda x: x["_rel"])
    for x in stops:
        for k in ("_rel", "_m", "_d", "_a"):
            x.pop(k, None)
    return stops, grade


def now_position(stops, ymd):
    """시간표 기준 지금 위치: 마지막으로 지난 정차역 인덱스(-1=출발 전, len=도착)."""
    now = dt.datetime.now(KST)
    if now.strftime("%Y%m%d") != ymd:
        return None
    cur = service_minutes(now.hour * 60 + now.minute)
    last = -1
    for i, s in enumerate(stops):
        m = service_minutes(hhmm_from_any(s.get("dep") or s.get("arr"))[0])
        if m is not None and m <= cur:
            last = i
    return last



# ─────────────────────────────────────────────────────────────
# 지연 기록 (한국철도공사_열차운행정보: 어제까지의 실제 운행 기록)
# ─────────────────────────────────────────────────────────────
KORAIL_RUN_URL = "https://apis.data.go.kr/B551457/run/v2/travelerTrainRunInfo2"


def korail_runs(no, ymd, days=14):
    """ymd 전날부터 days일 동안 이 열차의 실제 운행 기록. {운행일: [행...]}"""
    key = CFG.get("data_go_kr_key")
    if not key:
        raise ApiError("공공데이터포털 인증키가 없습니다.")
    end = dt.datetime.strptime(ymd, "%Y%m%d").date() - dt.timedelta(days=1)
    start = end - dt.timedelta(days=days - 1)
    no5 = str(no).lstrip("0").zfill(5)

    def fetch():
        params = {"serviceKey": key, "returnType": "json", "pageNo": 1, "numOfRows": 1000,
                  "cond[run_ymd::GTE]": start.strftime("%Y%m%d"), "cond[run_ymd::LTE]": end.strftime("%Y%m%d"),
                  "cond[trn_no::EQ]": no5}
        data = http_json(KORAIL_RUN_URL, params)
        code, msg = find_result_code(data)
        if code and code not in ("00", "0", "200", "INFO-000") and not code.startswith("0"):
            raise ApiError(f"열차운행정보 오류 {code}: {msg}")
        rows = find_items(data, ["stn_nm", "run_ymd"])
        by_day = {}
        for r in rows:
            by_day.setdefault(str(r["run_ymd"]).replace("-", "")[:8], []).append(r)
        return by_day
    return cached(("korail_run", no5, ymd), 6 * 3600, fetch), [
        (start + dt.timedelta(days=i)).strftime("%Y%m%d") for i in range(days)]


def _delay(plan_hhmm, actual_raw):
    p = hhmm_from_any(plan_hhmm)[0]
    a = hhmm_from_any(actual_raw)[0]
    if p is None or a is None:
        return None
    d = (a - p) % 1440
    return d - 1440 if d > 720 else d          # 일찍 온 경우 음수


def attach_history(stops, station, no, ymd):
    """정차역별 '어제' 지연과 고른 역의 14일 지연 기록을 붙인다. 반환: (history, note)"""
    by_day, days = korail_runs(no, ymd)
    if not by_day:
        return None, "최근 14일 운행 기록이 없습니다. (새 열차이거나 열차번호가 바뀌었을 수 있음)"
    plan = {norm_name(s["name"]): s for s in stops}

    def actual_at(rows, name, prefer):
        for r in rows:
            if norm_name(r.get("stn_nm", "")) == norm_name(name):
                a, d = r.get("trn_arvl_dt"), r.get("trn_dptre_dt")
                return (d or a) if prefer == "dep" else (a or d)
        return None

    # 어제 기록으로 정차역별 지연
    ylist = by_day.get(days[-1], [])
    for s in stops:
        if not ylist:
            break
        if s.get("arr"):
            s["yesterday"] = _delay(s["arr"], actual_at(ylist, s["name"], "arr"))
        elif s.get("dep"):
            s["yesterday"] = _delay(s["dep"], actual_at(ylist, s["name"], "dep"))
    # 고른 역 기준 14일
    me = plan.get(norm_name(station)) or (stops[-1] if stops else None)
    history = []
    for day in days:
        rows = by_day.get(day)
        if not rows or not me:
            history.append(None)
            continue
        if me.get("dep"):
            history.append(_delay(me["dep"], actual_at(rows, me["name"], "dep")))
        else:
            history.append(_delay(me["arr"], actual_at(rows, me["name"], "arr")))
    return history, f"{me['name'] if me else station}역 기준 · 오늘 계획 시각과 그날 실제 시각의 차이"


def train_detail(q):
    kind = q.get("kind", "ic")
    no = str(q.get("no", "")).lstrip("0")
    ymd = (q.get("date") or dt.datetime.now(KST).strftime("%Y%m%d")).replace("-", "")
    if kind == "urban":
        key = urban_key(q.get("line", ""), no)
        L = next((x for x in all_urban_lines() if x["key"] == q.get("line")), {})
        live = None
        st_ = TRACKER.lines.get(q.get("line", ""), {})
        for pool, park in ((st_.get("seen", {}), False), (st_.get("parked", {}), True)):
            for k, v in pool.items():
                if trn_keys(k) & trn_keys(no):
                    names_ = [x["name"] for x in L.get("stations", [])]
                    live = {"where": names_[v["i"]] if 0 <= v["i"] < len(names_) else "", "status": v.get("status"),
                            "delay": v.get("delay"), "delay_at": v.get("delay_at"), "delay_old": v.get("delay_old", False),
                            "basis": v.get("basis"), "parked": park}
        return {"kind": "urban", "key": key, "train_no": no, "line_name": f"{L.get('name', '')} · {L.get('operator', '')}",
                "live": live,
                "title": form_best(key, ymd) or (q.get("grade") or "일반"), "grade": q.get("grade") or "일반",
                "route": q.get("to", ""), "formations": form_list(key, ymd), "stops": [], "history": None,
                "note": "도시철도는 열차별 정차 시각을 아직 지원하지 않습니다."}
    key = ic_key(no)
    line = INTERCITY_LINES.get(q.get("line"), {})
    hist_note = ""
    if is_demo() or not CFG.get("data_go_kr_key"):
        stops, grade, history = demo_train_stops(q, no, ymd)
    else:
        history = None
        try:
            stops, grade = intercity_train(no, ymd, q.get("station"))
        except ApiError:
            stops, grade = [], ""
        if stops:
            try:
                history, hist_note = attach_history(stops, q.get("station"), no, ymd)
            except Exception as e:   # 지연 기록이 실패해도 상세 화면은 보여 준다
                hist_note = f"지연 기록을 불러오지 못했습니다: {e}"
    label, _ = vehicle_label(no, grade or q.get("grade_raw") or q.get("grade", ""))
    best = form_best(key, ymd)
    title = best or label
    return {"kind": "ic", "key": key, "train_no": no, "line_name": line.get("name", ""),
            "title": title, "grade": label, "grade_class": grade_class(title),
            "route": (f"{stops[0]['name']} {stops[0]['dep']} → {stops[-1]['name']} {stops[-1]['arr']}"
                      if stops else f"{q.get('from', '')} → {q.get('to', '')}"),
            "formations": form_list(key, ymd), "stops": stops, "now_after": now_position(stops, ymd),
            "history": history,
            "history_days": [(dt.datetime.strptime(ymd, "%Y%m%d") - dt.timedelta(days=14 - i)).strftime("%m/%d")
                             for i in range(14)],
            "note": hist_note, "history_note": hist_note}

# ─────────────────────────────────────────────────────────────
# 데모 데이터 (인증키 없이 화면 확인용)
# ─────────────────────────────────────────────────────────────
DEMO_URBAN = [
    {"key": "S1|2", "opr": "S1", "ln": "2", "region": "수도권(서울·인천)", "operator": "서울교통공사",
     "name": "2호선", "stations": [{"code": c, "name": n} for c, n in [
         ("201", "시청"), ("202", "을지로입구"), ("203", "을지로3가"), ("204", "을지로4가"),
         ("205", "동대문역사문화공원"), ("206", "신당"), ("207", "상왕십리"), ("208", "왕십리"),
         ("209", "한양대"), ("210", "뚝섬"), ("211", "성수"), ("212", "건대입구")]]},
    {"key": "IC|1", "opr": "IC", "ln": "1", "region": "수도권(서울·인천)", "operator": "인천교통공사",
     "name": "인천1호선", "stations": [{"code": c, "name": n} for c, n in [
         ("I110", "계양"), ("I120", "부평"), ("I130", "인천시청"), ("I140", "송도달빛축제공원")]]},
    {"key": "BS|1", "opr": "BS", "ln": "1", "region": "부산", "operator": "부산교통공사",
     "name": "1호선", "stations": [{"code": c, "name": n} for c, n in [
         ("0095", "다대포해수욕장"), ("0113", "부산역"), ("0119", "서면"), ("0134", "노포")]]},
    {"key": "DG|1", "opr": "DG", "ln": "1", "region": "대구", "operator": "대구교통공사",
     "name": "1호선", "stations": [{"code": c, "name": n} for c, n in [
         ("1110", "설화명곡"), ("1130", "반월당"), ("1140", "동대구"), ("1160", "하양")]]},
    {"key": "GJ|1", "opr": "GJ", "ln": "1", "region": "광주", "operator": "광주교통공사",
     "name": "1호선", "stations": [{"code": c, "name": n} for c, n in [
         ("100", "녹동"), ("110", "금남로4가"), ("120", "광주송정"), ("130", "평동")]]},
    {"key": "DJ|1", "opr": "DJ", "ln": "1", "region": "대전", "operator": "대전교통공사",
     "name": "1호선", "stations": [{"code": c, "name": n} for c, n in [
         ("101", "판암"), ("108", "대전역"), ("112", "시청"), ("122", "반석")]]},
]


def demo_intercity(line_id, station, ymd, f, t):
    import random
    line = INTERCITY_LINES[line_id]
    names = line["stations"]
    rnd = random.Random(f"{line_id}{station}{ymd}")
    grades = ["KTX", "KTX-산천", "KTX-청룡", "KTX-이음"] if "hs" in line_id or line_id == "gangneung" \
        else ["무궁화호", "ITX-새마을", "ITX-마음", "KTX"]
    out = []
    m = (f // 10) * 10
    while m <= t:
        m += rnd.choice([7, 9, 12, 15, 18])
        up = rnd.random() < 0.5
        g, custom = vehicle_label(0, rnd.choice(grades))
        if rnd.random() < 0.25 and "산천" in g:
            g, custom = "와인산천", True
        no = str(rnd.randint(1, 1999))
        raw = g
        best = form_best(ic_key(no), ymd)
        if best:
            g, custom = best, True
        out.append({"train_no": no, "grade_raw": raw, "custom_vehicle": custom, "time": f"{(m // 60) % 24:02d}:{m % 60:02d}", "sort": m,
                    "arr": f"{(m - 1) // 60 % 24:02d}:{(m - 1) % 60:02d}",
                    "dep": f"{(m // 60) % 24:02d}:{m % 60:02d}",
                    "grade": g, "grade_class": grade_class(g),
                    "from": names[-1] if up else names[0], "to": names[0] if up else names[-1],
                    "direction": "상행" if up else "하행", "status": "정차"})
    return {"trains": [x for x in out if f <= x["sort"] <= t], "warnings": [],
            "note": "데모 데이터입니다. 실제 시간표가 아닙니다."}


def demo_urban(line_key, stin, ymd, f, t):
    import random
    L = next(L for L in DEMO_URBAN if L["key"] == line_key)
    rnd = random.Random(f"{line_key}{stin}{ymd}")
    out, m = [], f
    while m <= t:
        m += rnd.choice([3, 4, 5, 6, 8])
        for to in (L["stations"][0]["name"], L["stations"][-1]["name"]):
            mm = m + rnd.randint(0, 2)
            exp = rnd.random() < 0.12
            out.append({"time": f"{mm // 60 % 24:02d}:{mm % 60:02d}", "sort": mm,
                        "arr": f"{mm // 60 % 24:02d}:{mm % 60:02d}",
                        "dep": f"{(mm + 1) // 60 % 24:02d}:{(mm + 1) % 60:02d}",
                        "train_no": (tn := str(rnd.randint(1000, 9999))),
                        "formation": form_best(urban_key(line_key, tn), ymd),
                        "grade": f"{to}급행" if exp else "일반", "grade_class": "express" if exp else "local",
                        "to": to + "행", "status": "정차"})
    out = [x for x in out if f <= x["sort"] <= t]
    out.sort(key=lambda x: x["sort"])
    return {"trains": out, "warnings": [], "day": {"8": "평일", "7": "토요일", "9": "휴일"}[day_code(ymd)],
            "note": "데모 데이터입니다. 실제 시간표가 아닙니다."}


def demo_realtime(name):
    return [
        {"line": "2호선", "updn": "내선", "heading": "성수행 - 을지로3가방면", "msg": "전역 출발",
         "where": "", "train_no": "2241", "kind": "일반", "dest": "성수", "seconds": "120", "received": ""},
        {"line": "2호선", "updn": "외선", "heading": "신도림행 - 시청방면", "msg": "3분 후 (동대문역사문화공원)",
         "where": "", "train_no": "2118", "kind": "일반", "dest": "신도림", "seconds": "180", "received": ""},
    ]


def demo_linemap(L, ymd):
    import random
    rnd = random.Random(f"{L['key']}{dt.datetime.now(KST).strftime('%H%M')}")
    names = [s["name"] for s in L["stations"]]
    trains = []
    for i in range(len(names)):
        for side in ("a", "b"):
            if rnd.random() < 0.38:
                no = str(rnd.randint(2001, 2299))
                late = rnd.choice([0, 0, 0, 1, 3, 4])
                exp = rnd.random() < 0.15
                dst = names[0] if side == "a" else names[-1]
                trains.append({"i": i, "side": side, "no": no, "dest": dst,
                               "express": exp, "express_label": f"{dst}급행" if exp else "",
                               "status": rnd.choice(["도착", "출발", "진입"]),
                               "delay": late * 60 + rnd.randint(-20, 40),
                               "formation": form_best(urban_key(L["key"], no), ymd)
                               or (f"2-{rnd.randint(1, 60):02d}편성" if rnd.random() < 0.2 else None)})
    for k in range(rnd.randint(2, 4)):
        end_i = 0 if k % 2 == 0 else len(names) - 1
        trains.append({"i": end_i, "side": "a" if end_i == 0 else "b", "no": str(rnd.randint(2301, 2399)),
                       "dest": names[end_i], "express": False, "status": "운행 종료", "parked": True,
                       "delay": rnd.choice([9, 0, 75, 132]), "formation": None})
    return {"stations": names, "trains": trains, "realtime": True, "line": L["name"], "note": "데모 데이터입니다.",
            "sides": ["내선순환", "외선순환"] if L["name"] == "2호선" else ["상행", "하행"]}


def demo_train_stops(q, no, ymd):
    import random
    line = INTERCITY_LINES.get(q.get("line"), INTERCITY_LINES["gyeongbu_hs"])
    names = line["stations"]
    o = names.index(q["from"]) if q.get("from") in names else 0
    d = names.index(q["to"]) if q.get("to") in names else len(names) - 1
    step = 1 if d > o else -1
    seq = [names[i] for i in range(o, d + step, step)]
    rnd = random.Random(f"{no}{ymd}")
    offs, acc = [], 0
    for _ in seq:
        offs.append(acc); acc += rnd.choice([14, 18, 22, 26])
    anchor = hhmm_from_any(q.get("dep") or q.get("time") or "0700")[0] or 420
    k = seq.index(q.get("station")) if q.get("station") in seq else 0
    base = anchor - offs[k] - 2           # 고른 역의 출발 시각과 맞춘다
    fmt = lambda x: f"{x // 60 % 24:02d}:{x % 60:02d}"
    stops, yd = [], 0
    for i, n in enumerate(seq):
        m = base + offs[i]
        yd = max(0, yd + rnd.choice([0, 0, 1, 1, 2, -1]))
        stops.append({"name": n, "arr": "" if i == 0 else fmt(m), "dep": "" if i == len(seq) - 1 else fmt(m + 2),
                      "yesterday": yd})
    history = [rnd.choice([0, 1, 1, 2, 2, 3, 6, 9]) for _ in range(14)]
    return stops, q.get("grade_raw") or q.get("grade") or "KTX-산천", history


# ─────────────────────────────────────────────────────────────
# HTTP 서버
# ─────────────────────────────────────────────────────────────
def is_demo():
    return CFG.get("demo") or not (CFG.get("data_go_kr_key") or CFG.get("kric_key"))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s\n" % (dt.datetime.now(KST).strftime("%H:%M:%S"), fmt % args))

    def send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def check_password(self):
        """config.json 의 "password" 가 있으면 브라우저 로그인 창으로 비밀번호를 묻는다."""
        pw = str(CFG.get("password") or "")
        if not pw:
            return True
        import base64
        import hmac
        h = self.headers.get("Authorization", "")
        if h.startswith("Basic "):
            try:
                given = base64.b64decode(h[6:]).decode("utf-8", "replace").split(":", 1)[-1]
            except Exception:
                given = ""
            if hmac.compare_digest(given.encode(), pw.encode()):
                return True
        body = "비밀번호가 필요합니다. (아이디 칸은 아무거나 입력)".encode("utf-8")
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="train-board", charset="UTF-8"')
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return False

    def do_GET(self):
        if not self.check_password():
            return
        u = urllib.parse.urlparse(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        try:
            if u.path in ("/", "/index.html"):
                with open(os.path.join(STATIC_DIR, "index.html"), "rb") as fh:
                    body = fh.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif u.path == "/api/meta":
                self.send_json({
                    "demo": bool(is_demo()),
                    "keys": {"tago": bool(CFG.get("data_go_kr_key")), "kric": bool(CFG.get("kric_key")),
                             "seoul": bool(CFG.get("seoul_key"))},
                    "intercity": [{"id": k, "name": v["name"], "stations": v["stations"]}
                                  for k, v in INTERCITY_LINES.items()],
                    "now": dt.datetime.now(KST).strftime("%Y-%m-%dT%H:%M"),
                })
            elif u.path == "/api/urban/lines":
                if is_demo() or not CFG.get("kric_key"):
                    self.send_json({"lines": DEMO_URBAN, "demo": True})
                else:
                    self.send_json({"lines": urban_lines(), "demo": False})
            elif u.path == "/api/board/intercity":
                ymd, f, t = parse_window(q.get("date"), q.get("from"), q.get("to"))
                fn = demo_intercity if (is_demo() or not CFG.get("data_go_kr_key")) else intercity_board
                self.send_json(fn(q.get("line"), q.get("station"), ymd, f, t))
            elif u.path == "/api/board/urban":
                ymd, f, t = parse_window(q.get("date"), q.get("from"), q.get("to"))
                fn = demo_urban if (is_demo() or not CFG.get("kric_key")) else urban_board
                self.send_json(fn(q.get("line"), q.get("station"), ymd, f, t))
            elif u.path == "/api/realtime/seoul":
                if not CFG.get("seoul_key"):
                    self.send_json({"rows": demo_realtime(q.get("station", "")) if is_demo() else [],
                                    "demo": bool(is_demo()), "available": bool(is_demo())})
                else:
                    self.send_json({"rows": seoul_realtime(q.get("station", "")), "available": True})
            elif u.path == "/api/raw/urban":
                self.send_json(urban_raw(q.get("line", ""), q.get("station", "")))
            elif u.path == "/api/linemap":
                self.send_json(linemap(q.get("line", "")))
            elif u.path == "/api/train":
                self.send_json(train_detail(q))
            elif u.path == "/api/formations":
                ymd = (q.get("date") or dt.datetime.now(KST).strftime("%Y%m%d")).replace("-", "")
                self.send_json({"entries": form_list(q.get("key", ""), ymd)})
            elif u.path == "/api/formations/recent":
                self.send_json({"entries": form_recent()})
            elif u.path == "/api/refresh":
                for n in ("tago_stations.json", "kric_lines.json"):
                    p = os.path.join(CACHE_DIR, n)
                    if os.path.exists(p):
                        os.remove(p)
                _mem_cache.clear()
                self.send_json({"ok": True})
            else:
                self.send_json({"error": "not found"}, 404)
        except ApiError as e:
            self.send_json({"error": str(e), "trains": [], "warnings": []}, 200)
        except Exception as e:  # 예기치 못한 오류도 화면에 보여준다
            self.send_json({"error": f"서버 오류: {type(e).__name__}: {e}", "trains": []}, 500)

    def do_POST(self):
        if not self.check_password():
            return
        u = urllib.parse.urlparse(self.path)
        try:
            n = min(int(self.headers.get("Content-Length") or 0), 10000)
            body = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
            if u.path == "/api/formations":
                ymd = str(body.get("date") or dt.datetime.now(KST).strftime("%Y%m%d")).replace("-", "")
                form_add(body.get("key"), ymd, body.get("text"), body.get("name"), body.get("where"),
                         body.get("label"))
                self.send_json({"entries": form_list(_clean(body.get("key"), 60), ymd)})
            elif u.path == "/api/formations/like":
                e = form_like(body.get("id"), body.get("name"))
                self.send_json({"entries": form_list(e["key"], e["date"])})
            else:
                self.send_json({"error": "not found"}, 404)
        except ApiError as e:
            self.send_json({"error": str(e)}, 400)
        except Exception as e:
            self.send_json({"error": f"서버 오류: {type(e).__name__}: {e}"}, 500)


def main():
    global CFG
    CFG = load_config()
    port = int(os.environ.get("PORT") or CFG.get("port") or 8000)
    host = os.environ.get("HOST") or CFG.get("host") or "0.0.0.0"
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"열차 안내판 실행 중 → http://localhost:{port}")
    print("데모 모드 (인증키 없음)" if is_demo() else
          f"키: TAGO={'O' if CFG['data_go_kr_key'] else 'X'}  KRIC={'O' if CFG['kric_key'] else 'X'}  "
          f"서울실시간={'O' if CFG['seoul_key'] else 'X'}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n종료")


if __name__ == "__main__":
    main()
