"""高德地图 API Tools：地理编码 / 天气 / 驾车路线"""

import httpx
from langchain_core.tools import tool
from app.config import settings
from app.tools import tool_retry


@tool
@tool_retry
def amap_geocode(address: str) -> dict:
    """获取城市经纬度和 adcode（高德地理编码）。"""
    if not settings.AMAP_API_KEY:
        return {"error": "高德 API Key 未配置", "location": "116.397,39.908", "adcode": "110000"}
    resp = httpx.get(
        "https://restapi.amap.com/v3/geocode/geo",
        params={"key": settings.AMAP_API_KEY, "address": address},
        timeout=10,
    )
    data = resp.json()
    if data.get("status") == "1" and data.get("geocodes"):
        g = data["geocodes"][0]
        return {"location": g.get("location", ""), "adcode": g.get("adcode", "")}
    return {"error": data.get("info", "查询失败"), "location": "", "adcode": ""}


@tool
@tool_retry
def amap_weather(adcode: str) -> dict:
    """获取该城市天气预报（高德天气）。"""
    if not settings.AMAP_API_KEY:
        return {"forecasts": [{"date": "2026-06-03", "dayweather": "晴", "nighttemp": "18", "daytemp": "28"}]}
    resp = httpx.get(
        "https://restapi.amap.com/v3/weather/weatherInfo",
        params={"key": settings.AMAP_API_KEY, "city": adcode, "extensions": "all"},
        timeout=10,
    )
    data = resp.json()
    if data.get("status") == "1":
        return data.get("forecasts", [{}])[0] if data.get("forecasts") else {}
    return {"error": data.get("info", "查询失败")}


@tool
@tool_retry
def amap_driving_route(origin: str, destination: str) -> dict:
    """计算驾车距离(km)和预计耗时(h)（高德驾车路径规划）。"""
    if not settings.AMAP_API_KEY:
        return {"distance_km": 800, "duration_hour": 3.5}
    # 先查两地坐标
    geo_origin = amap_geocode.invoke({"address": origin})
    geo_dest = amap_geocode.invoke({"address": destination})

    resp = httpx.get(
        "https://restapi.amap.com/v3/direction/driving",
        params={
            "key": settings.AMAP_API_KEY,
            "origin": geo_origin.get("location", ""),
            "destination": geo_dest.get("location", ""),
        },
        timeout=10,
    )
    data = resp.json()
    if data.get("status") == "1" and data.get("route", {}).get("paths"):
        path = data["route"]["paths"][0]
        return {
            "distance_km": round(int(path.get("distance", 0)) / 1000, 1),
            "duration_hour": round(int(path.get("duration", 0)) / 3600, 1),
        }
    return {"distance_km": 0, "duration_hour": 0, "error": data.get("info", "查询失败")}
