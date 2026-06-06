from typing import NotRequired
from app.state.agent_state import AgentState


class TravelState(AgentState):
    """
    旅游规划子图专用 State，继承 AgentState 全部字段。

    用于收集旅行规划所需的关键信息，包括人数、预算、天数、起止地点等，
    并整合地理、天气、路线等信息生成完整的旅行计划。
    """
    traveler_count: NotRequired[int]
    """旅行人数。"""

    budget: NotRequired[int]
    """旅行预算（单位：元）。"""

    days: NotRequired[int]
    """旅行天数。"""

    origin: NotRequired[str]
    """出发地。"""

    destination: NotRequired[str]
    """目的地。"""

    geo_info: NotRequired[dict]
    """地理位置信息，包含经纬度等数据。"""

    weather_info: NotRequired[dict]
    """天气信息，包含目的地的天气预报数据。"""

    route_info: NotRequired[dict]
    """路线信息，包含交通方式和路径规划。"""

    travel_plan: NotRequired[str]
    """生成的完整旅行计划内容。"""
