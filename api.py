"""外部 API 存取：Nominatim、Overpass、OpenRouteService (不依賴 Streamlit)"""
import math
import time
from functools import lru_cache

import requests

from config import NTU_BUILDINGS, TRANSPORT_CONFIG, USER_AGENT

HEADERS = {"User-Agent": USER_AGENT}

# 主伺服器失敗時依序改試備用伺服器
OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

# 依設施類型賦予預估花費與讀書適合度 (自訂常數，報告中請說明依據)
CATEGORY_META = {
    "library": {"type_zh": "圖書館", "est_cost": 0, "study_score": 1.0},
    "cafe": {"type_zh": "咖啡廳", "est_cost": 150, "study_score": 0.8},
    "fast_food": {"type_zh": "速食/簡餐", "est_cost": 130, "study_score": 0.3},
    "restaurant": {"type_zh": "餐廳", "est_cost": 220, "study_score": 0.2},
    "park": {"type_zh": "公園綠地", "est_cost": 0, "study_score": 0.4},
}


def haversine_distance(
    lat1: float, lon1: float, lat2: float, lon2: float
) -> float:
  """Haversine 公式計算兩點直線距離 (公尺)，用於 API 呼叫前的初篩"""
  r = 6371000
  phi1, phi2 = math.radians(lat1), math.radians(lat2)
  d_phi = math.radians(lat2 - lat1)
  d_lambda = math.radians(lon2 - lon1)
  a = (
      math.sin(d_phi / 2) ** 2
      + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
  )
  return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def geocode_building(building_name: str) -> tuple[float, float] | None:
  """系館或地標名稱 -> (lat, lon)。查無結果回傳 None；網路問題會丟出 requests 例外。"""
  name = building_name.strip()
  if not name or len(name) > 100:
    return None
  if name in NTU_BUILDINGS:
    return NTU_BUILDINGS[name]

  query = (
      name
      if ("臺灣大學" in name or "台灣大學" in name)
      else f"國立臺灣大學 {name}"
  )
  response = requests.get(
      "https://nominatim.openstreetmap.org/search",
      params={"q": query, "format": "json", "limit": 1},
      headers=HEADERS,
      timeout=10,
  )
  response.raise_for_status()
  time.sleep(1)  # 遵守 Nominatim 每秒最多 1 次請求的規範
  data = response.json()
  if not data:
    return None
  return float(data[0]["lat"]), float(data[0]["lon"])


def fetch_nearby_facilities(
    lat: float, lon: float, radius_m: int = 1200
) -> list[dict]:
  """Overpass 查詢周邊咖啡廳、圖書館、餐廳與公園。全部伺服器失敗時丟出 RuntimeError。"""
  lat, lon = float(lat), float(lon)
  radius_m = max(100, min(int(radius_m), 5000))  # 限制範圍，避免過重查詢

  query = f"""
    [out:json][timeout:25];
    (
      nwr["amenity"~"^(cafe|library|restaurant|fast_food)$"](around:{radius_m},{lat},{lon});
      nwr["leisure"="park"](around:{radius_m},{lat},{lon});
    );
    out center;
    """

  elements = None
  last_error = None
  for url in OVERPASS_URLS:
    try:
      response = requests.post(
          url, data={"data": query}, headers=HEADERS, timeout=30
      )
      response.raise_for_status()
      elements = response.json().get("elements", [])
      break
    except (requests.RequestException, ValueError) as e:
      last_error = e
  if elements is None:
    raise RuntimeError(f"所有 Overpass 伺服器皆無法使用：{last_error}")

  facilities = []
  for el in elements:
    tags = el.get("tags", {})
    name = tags.get("name")
    if not name:
      continue  # 過濾無名稱的節點

    el_lat = el.get("lat") or el.get("center", {}).get("lat")
    el_lon = el.get("lon") or el.get("center", {}).get("lon")
    if el_lat is None or el_lon is None:
      continue

    raw_type = tags.get("amenity") or tags.get("leisure", "park")
    meta = CATEGORY_META.get(
        raw_type, {"type_zh": raw_type, "est_cost": 100, "study_score": 0.5}
    )
    facilities.append({
        "name": name,
        "category": raw_type,
        "category_zh": meta["type_zh"],
        "lat": float(el_lat),
        "lon": float(el_lon),
        "est_cost": meta["est_cost"],
        "study_score": meta["study_score"],
        "min_stay_mins": 45 if raw_type in ("library", "cafe") else 30,
    })
  return facilities


def _estimate_travel_mins(
    origin: tuple[float, float], dest: tuple[float, float], mode: str
) -> float:
  """直線距離 x 繞路係數 1.3 / 速度常數 的估算"""
  dist_m = haversine_distance(origin[0], origin[1], dest[0], dest[1])
  speed = TRANSPORT_CONFIG[mode]["fallback_speed_m_per_min"]
  return round((dist_m * 1.3) / speed, 1)


@lru_cache(maxsize=512)
def _ors_travel_mins(
    origin: tuple[float, float],
    dest: tuple[float, float],
    mode: str,
    api_key: str,
) -> float:
  """呼叫 OpenRouteService (成功結果會快取，失敗會丟出例外)"""
  response = requests.post(
      f"https://api.openrouteservice.org/v2/directions/{mode}",
      json={
          # ORS 座標順序為 [經度 lon, 緯度 lat]
          "coordinates": [[origin[1], origin[0]], [dest[1], dest[0]]],
          "instructions": False,
      },
      headers={"Authorization": api_key, "Content-Type": "application/json"},
      timeout=10,
  )
  response.raise_for_status()
  return round(response.json()["routes"][0]["summary"]["duration"] / 60.0, 1)


def get_travel_time_mins(
    origin: tuple[float, float],
    dest: tuple[float, float],
    mode: str = "foot-walking",
    ors_api_key: str = "",
) -> float:
  """單程通勤時間 (分鐘)。無 Key 或 ORS 失敗時自動改用估算。"""
  if not ors_api_key:
    return _estimate_travel_mins(origin, dest, mode)
  try:
    return _ors_travel_mins(tuple(origin), tuple(dest), mode, ors_api_key)
  except (requests.RequestException, KeyError, IndexError, ValueError):
    return _estimate_travel_mins(origin, dest, mode)
