"""Document retrieval and explicitly sourced ESP32 chip-charge estimates."""

from __future__ import annotations

import math
from typing import Any

from src.config import DATASHEET_URL
from src.rag.retrieve import Retriever

# ESP32 Series Datasheet v5.3, §4.3.1 Table 4-2 (PDF page 30).
# Deep-sleep: RTC timer + RTC memory = 10 µA; ULP powered = 150 µA.
# Light-sleep = 0.8 mA; hibernation RTC timer only = 5 µA.
# §5.4 Table 5-4 (PDF page 52): Wi-Fi TX 802.11b 1 Mbps +19.5 dBm
# = 240 mA at 3.3 V / 25°C / 50% TX duty. Do NOT halve that value again.
# These chip typical values exclude board regulators and peripherals.
CURRENT_MA: dict[str, float] = {
    "deep_sleep": 0.01, "light_sleep": 0.8, "deep_sleep_ulp": 0.15,
    "hibernation": 0.005, "wifi_tx_11b": 240.0,
}


def search_docs(query: str, retriever: Retriever) -> list[dict[str, Any]]:
    """Search the local official corpus with a nonempty query."""
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query 必须为非空字符串")
    return retriever.search(query)


def estimate_power(mode: str, duration_h: float) -> dict[str, Any]:
    """Estimate chip charge in mAh from a specific datasheet typical-current profile."""
    if not isinstance(mode, str) or mode not in CURRENT_MA:
        raise ValueError("mode 必须是已记录的 datasheet 功耗配置")
    if isinstance(duration_h, bool) or not isinstance(duration_h, (int, float)) or not math.isfinite(duration_h) or duration_h < 0:
        raise ValueError("duration_h 必须为有限非负数，单位小时")
    charge = CURRENT_MA[mode] * duration_h
    if not math.isfinite(charge):
        raise ValueError("估算超出有限数值范围")
    wifi = mode == "wifi_tx_11b"
    return {
        "mode": mode, "duration_h": duration_h, "current_ma": CURRENT_MA[mode], "charge_mah": charge,
        "section_path": ["ESP32 Series Datasheet v5.3", "Table 5-4 Current Consumption in Active Mode" if wifi else "Table 4-2 Power Consumption in Low-Power Modes"],
        "url": DATASHEET_URL + ("#page=52" if wifi else "#page=30"),
        "explanation": "Chip typical-current estimate only; excludes board regulators/peripherals and is not measured battery life.",
        "conditions": "3.3 V, 25°C, 802.11b 1 Mbps, +19.5 dBm, measured 50% TX duty" if wifi else
                      ("RTC timer and RTC memory retained" if mode == "deep_sleep" else "See the named Table 4-2 profile"),
    }


def format_power_estimate(result: dict[str, Any], *, chinese: bool) -> str:
    """Render the tool's verified numeric charge directly, without model arithmetic."""
    citation = f"[{' > '.join(result['section_path'])}]({result['url']})"
    formula = f"{result['current_ma']:g} mA × {result['duration_h']:g} h = {result['charge_mah']:g} mAh"
    if chinese:
        return (f"`{result['mode']}` 的芯片电量估算：**{formula}**。{citation}\n\n"
                f"采用 datasheet 的芯片典型电流，配置条件：{result['conditions']}。"
                f"不包含稳压器或外设消耗，不代表整板实测或实际电池寿命。{citation}")
    return (f"Chip charge estimate for `{result['mode']}`: **{formula}**. {citation}\n\n"
            f"Typical-current conditions: {result['conditions']}. "
            f"This excludes board regulators/peripherals and is not measured battery life. {citation}")
