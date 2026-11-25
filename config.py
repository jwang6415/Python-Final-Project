"""共用設定與資料結構"""
from dataclasses import dataclass

MIN_BREAK_MINS = 60  # 最短空堂：1 小時
MAX_BREAK_MINS = 300  # 最長空堂：5 小時

# Nominatim 規範要求 User-Agent 能聯絡到你，請改成自己的 GitHub 連結或信箱
USER_AGENT = "CampusBreakPlanner/1.0 (https://github.com/你的帳號/campus-break-planner)"


@dataclass
class UserPreferences:
  """使用者篩選條件定義"""

  min_break_mins: int = MIN_BREAK_MINS
  max_break_mins: int = MAX_BREAK_MINS
  budget: int = 200  # 預算上限 (NTD)
  transport_mode: str = "foot-walking"  # "foot-walking" 或 "cycling-regular"
  workload: str = "high"  # 課業壓力："low", "medium", "high"


# 交通速度與初始篩選半徑設定
TRANSPORT_CONFIG = {
    "foot-walking": {"max_radius_m": 1500, "fallback_speed_m_per_min": 75},
    "cycling-regular": {"max_radius_m": 4500, "fallback_speed_m_per_min": 250},
}

# 常用系館座標備援 (Nominatim 查不到或無法連線時使用)
# 座標為約略值，請自行用 Google Maps 校對後再擴充
NTU_BUILDINGS = {
    "德田館": (25.0194, 121.5415),
    "總圖書館": (25.0174, 121.5405),
}
