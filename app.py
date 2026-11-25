"""Streamlit 介面：台大學生的空堂時間推薦器
執行方式：streamlit run app.py
"""
import html
import os
from datetime import time as dtime

import folium
import requests
import streamlit as st
from streamlit_folium import st_folium

from api import fetch_nearby_facilities, geocode_building
from config import MAX_BREAK_MINS, MIN_BREAK_MINS, TRANSPORT_CONFIG, UserPreferences
from recommender import calculate_break_minutes, recommend_activities

st.set_page_config(page_title="台大空堂時間推薦器", page_icon="🎓", layout="wide")

TRANSPORT_LABELS = {"步行": "foot-walking", "腳踏車": "cycling-regular"}
WORKLOAD_LABELS = {"高 (近期有考試/作業)": "high", "中": "medium", "低": "low"}


def load_ors_key() -> str:
  """依序從 Streamlit secrets、環境變數讀取金鑰；都沒有就回傳空字串 (啟用估算模式)"""
  try:
    key = st.secrets.get("ORS_API_KEY", "")
    if key:
      return str(key)
  except Exception:  # 沒有 secrets.toml 時會丟例外
    pass
  return os.environ.get("ORS_API_KEY", "")


@st.cache_data(ttl=3600, show_spinner=False)
def cached_geocode(name: str):
  return geocode_building(name)


@st.cache_data(ttl=3600, show_spinner=False)
def cached_facilities(lat: float, lon: float, radius_m: int):
  return fetch_nearby_facilities(lat, lon, radius_m)


def locate(name: str, label: str):
  """查詢座標；網路失敗或查無結果時顯示錯誤並停止"""
  try:
    coords = cached_geocode(name)
  except requests.RequestException:
    st.error(f"無法連線到地理編碼服務 (Nominatim)，請稍後再試，或改輸入「德田館」等常用系館名稱。")
    st.stop()
  if coords is None:
    st.error(f"找不到{label}「{name}」，請換個關鍵字試試。")
    st.stop()
  return coords


def build_map(origin, next_class, recs) -> folium.Map:
  m = folium.Map(location=origin, zoom_start=16)
  folium.Marker(origin, tooltip="出發地", icon=folium.Icon(color="red")).add_to(m)
  if next_class != origin:
    folium.Marker(
        next_class, tooltip="下一堂課", icon=folium.Icon(color="blue")
    ).add_to(m)
  for i, r in enumerate(recs[:5], start=1):
    # 地點名稱來自 OSM 使用者編輯資料，一律跳脫 HTML 避免注入
    name = html.escape(r["name"])
    popup = folium.Popup(
        f"{i}. {name}<br>{html.escape(r['category'])}<br>推薦度 {r['total_score']}",
        max_width=250,
    )
    folium.Marker(
        [r["lat"], r["lon"]],
        tooltip=f"{i}. {name}",
        popup=popup,
        icon=folium.Icon(color="green"),
    ).add_to(m)
  return m


st.title("🎓 台大學生的空堂時間推薦器")
st.caption("輸入上下課地點與時間，推薦適合這段空堂的活動。")

ors_key = load_ors_key()
if not ors_key:
  st.info("未設定 OpenRouteService 金鑰，路程時間將以直線距離估算。")

with st.form("preferences"):
  col1, col2 = st.columns(2)
  with col1:
    prev_building = st.text_input("目前所在系館 (上一堂課)", value="德田館", max_chars=50)
    next_building = st.text_input(
        "下一堂課的系館 (留空表示回原地)", value="", max_chars=50
    )
    prev_end = st.time_input("上一堂課結束", value=dtime(10, 10))
    next_start = st.time_input("下一堂課開始", value=dtime(12, 10))
  with col2:
    budget = st.number_input("預算上限 (NTD)", min_value=0, max_value=2000, value=200, step=10)
    transport_label = st.selectbox("交通方式", list(TRANSPORT_LABELS))
    workload_label = st.selectbox("課業壓力", list(WORKLOAD_LABELS))
  submitted = st.form_submit_button("推薦活動")

if submitted:
  # 1. 空堂時間驗證
  try:
    break_mins = calculate_break_minutes(
        prev_end.strftime("%H:%M"), next_start.strftime("%H:%M")
    )
  except ValueError as e:
    st.error(str(e))
    st.stop()

  # 2. 地點轉座標
  with st.spinner("查詢地點中..."):
    origin = locate(prev_building.strip(), "系館")
    next_class = (
        locate(next_building.strip(), "下一堂課的系館")
        if next_building.strip()
        else origin
    )

  prefs = UserPreferences(
      budget=int(budget),
      transport_mode=TRANSPORT_LABELS[transport_label],
      workload=WORKLOAD_LABELS[workload_label],
  )

  # 3. 周邊設施 (失敗時退回只用旁聽課程)
  radius = TRANSPORT_CONFIG[prefs.transport_mode]["max_radius_m"]
  warning = ""
  with st.spinner("搜尋周邊設施中..."):
    try:
      facilities = cached_facilities(origin[0], origin[1], radius)
    except Exception:
      facilities = []
      warning = "無法取得周邊設施資料 (Overpass 服務異常)，目前僅顯示校內旁聽課程。"

  # 4. 推薦
  recs = recommend_activities(
      origin, break_mins, prefs, facilities, ors_key, next_class
  )
  st.session_state["result"] = {
      "origin": origin,
      "next_class": next_class,
      "break_mins": break_mins,
      "recs": recs,
      "warning": warning,
  }

result = st.session_state.get("result")
if result:
  st.subheader(f"空堂 {result['break_mins']} 分鐘")
  if result["warning"]:
    st.warning(result["warning"])

  if not result["recs"]:
    st.warning("找不到符合條件的活動，可以試著提高預算、改用腳踏車，或確認空堂時間是否足夠。")
  else:
    st.dataframe(
        [
            {
                "排名": i,
                "類別": r["category"],
                "名稱": r["name"],
                "推薦度": r["total_score"],
                "單程(分)": r["one_way_mins"],
                "可停留(分)": r["stay_mins"],
                "預估花費(元)": r["est_cost"],
            }
            for i, r in enumerate(result["recs"][:5], start=1)
        ],
        hide_index=True,
        use_container_width=True,
    )
    st.caption("旁聽課程目前為模擬資料；預估花費為依類別設定的參考值。")
    st_folium(
        build_map(result["origin"], result["next_class"], result["recs"]),
        height=450,
        use_container_width=True,
        returned_objects=[],  # 不回傳互動資料，避免地圖操作觸發重跑
    )
