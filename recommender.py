"""推薦演算法 (不依賴 Streamlit，可單獨測試)"""
from datetime import datetime

from api import get_travel_time_mins, haversine_distance
from config import (
    MAX_BREAK_MINS,
    MIN_BREAK_MINS,
    TRANSPORT_CONFIG,
    UserPreferences,
)

BUFFER_MINS = 10  # 預留進教室的緩衝時間
MAX_ROUTE_CANDIDATES = 10  # 有 ORS Key 時，最多精算幾個候選地點 (免費額度有限)

WEIGHT_PROFILES = {
    "high": {"time": 0.25, "budget": 0.20, "workload": 0.40, "transport": 0.15},
    "medium": {"time": 0.30, "budget": 0.25, "workload": 0.25, "transport": 0.20},
    "low": {"time": 0.30, "budget": 0.30, "workload": 0.15, "transport": 0.25},
}


def calculate_break_minutes(prev_class_end: str, next_class_start: str) -> int:
  """計算空堂分鐘數 (輸入格式 'HH:MM')，不在 1～5 小時範圍會丟出 ValueError"""
  fmt = "%H:%M"
  t1 = datetime.strptime(prev_class_end, fmt)
  t2 = datetime.strptime(next_class_start, fmt)
  gap_mins = int((t2 - t1).total_seconds() // 60)
  if not (MIN_BREAK_MINS <= gap_mins <= MAX_BREAK_MINS):
    raise ValueError(
        f"空堂時間為 {gap_mins} 分鐘，不在系統支援的 1～5 小時"
        f" ({MIN_BREAK_MINS}～{MAX_BREAK_MINS} 分鐘) 範圍內。"
    )
  return gap_mins


def get_mock_audit_courses(
    campus_lat: float, campus_lon: float, break_mins: int
) -> list[dict]:
  """【模擬資料】校內旁聽課程 (座標為起點加偏移量，非真實位置)"""
  courses = [
      {
          "name": "【旁聽】資料科學與人工智慧導論 (博雅教學館)",
          "category": "audit_course",
          "category_zh": "旁聽課程",
          "lat": campus_lat + 0.0012,
          "lon": campus_lon - 0.0008,
          "est_cost": 0,
          "study_score": 0.85,
          "min_stay_mins": 100,
      },
      {
          "name": "【旁聽】心理學與現代生活 (普通教學館)",
          "category": "audit_course",
          "category_zh": "旁聽課程",
          "lat": campus_lat - 0.0010,
          "lon": campus_lon + 0.0015,
          "est_cost": 0,
          "study_score": 0.75,
          "min_stay_mins": 50,
      },
  ]
  return [c for c in courses if c["min_stay_mins"] <= break_mins - 15]


def recommend_activities(
    origin: tuple[float, float],
    break_mins: int,
    prefs: UserPreferences,
    facilities: list[dict],
    ors_api_key: str = "",
    next_class: tuple[float, float] | None = None,
) -> list[dict]:
  """綜合時間、預算、課業壓力與交通方式加權推薦。

  facilities 由呼叫端傳入 (方便快取與錯誤處理)；
  next_class 為下一堂課座標，未提供時視為回到原地。
  """
  if prefs.transport_mode not in TRANSPORT_CONFIG:
    raise ValueError(f"不支援的交通方式：{prefs.transport_mode}")

  max_radius = TRANSPORT_CONFIG[prefs.transport_mode]["max_radius_m"]
  next_class = next_class or origin
  w = WEIGHT_PROFILES.get(prefs.workload, WEIGHT_PROFILES["medium"])

  candidates = list(facilities) + get_mock_audit_courses(
      origin[0], origin[1], break_mins
  )

  # Step A/B：直線距離初篩 + 預算硬性過濾
  nearby = []
  for item in candidates:
    dist_m = haversine_distance(origin[0], origin[1], item["lat"], item["lon"])
    if dist_m > max_radius or item["est_cost"] > prefs.budget:
      continue
    nearby.append((dist_m, item))

  # 有 ORS Key 時只精算最近的幾個，避免超出免費額度
  nearby.sort(key=lambda x: x[0])
  if ors_api_key:
    nearby = nearby[:MAX_ROUTE_CANDIDATES]

  recommendations = []
  for _, item in nearby:
    pos = (item["lat"], item["lon"])

    # Step C：起點 -> 地點 -> 下一堂課，扣除緩衝後的實際停留時間
    to_mins = get_travel_time_mins(
        origin, pos, prefs.transport_mode, ors_api_key
    )
    back_mins = (
        to_mins
        if next_class == origin
        else get_travel_time_mins(
            pos, next_class, prefs.transport_mode, ors_api_key
        )
    )
    stay_mins = break_mins - to_mins - back_mins - BUFFER_MINS
    if stay_mins < item["min_stay_mins"]:
      continue

    # Step D：各維度標準化分數 (0~1)
    time_score = min(stay_mins / break_mins, 1.0)
    budget_score = (
        1.0
        if prefs.budget == 0
        else max(0.0, 1.0 - item["est_cost"] / prefs.budget)
    )
    if prefs.workload == "high":
      workload_score = item["study_score"]
    elif prefs.workload == "low":
      workload_score = 1.0 - item["study_score"]
    else:
      workload_score = 1.0 - abs(item["study_score"] - 0.65)
    transport_score = max(0.0, 1.0 - to_mins / 20.0)

    total_score = round(
        (
            w["time"] * time_score
            + w["budget"] * budget_score
            + w["workload"] * workload_score
            + w["transport"] * transport_score
        )
        * 100,
        1,
    )

    recommendations.append({
        "name": item["name"],
        "category": item["category_zh"],
        "total_score": total_score,
        "one_way_mins": to_mins,
        "return_mins": back_mins,
        "stay_mins": round(stay_mins, 1),
        "est_cost": item["est_cost"],
        "lat": item["lat"],
        "lon": item["lon"],
    })

  recommendations.sort(key=lambda x: x["total_score"], reverse=True)
  return recommendations
