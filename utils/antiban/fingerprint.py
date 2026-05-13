"""指纹持久化强化。

职责：
  1. 以 fp_map 为底，扩展附加字段并持久化（screen、cores、device_memory 等）；
  2. 保证同一 token/账号在多次请求间指纹不变（防漂移）；
  3. 提供只读访问给 proofofWork/ChatService，避免直接修改 fp_map 污染。

注意：UA/impersonate/oai-device-id 仍由 chatgpt/fp.py 创建；
本模块只在其基础上补齐字段，不替代 fp.py。
"""

import json
import random
import threading
from typing import Dict, Optional

import utils.globals as globals
from utils import configs
from utils.Logger import logger

_write_lock = threading.Lock()

_SCREEN_POOL = [
    {"width": 1920, "height": 1080, "color_depth": 24},
    {"width": 2560, "height": 1440, "color_depth": 24},
    {"width": 1920, "height": 1200, "color_depth": 24},
    {"width": 2560, "height": 1600, "color_depth": 30},
    {"width": 1680, "height": 1050, "color_depth": 24},
]
_CORES_POOL = [4, 8, 8, 8, 12, 16]
_DEVICE_MEMORY_POOL = [4, 8, 8, 16]
# 真实设备 devicePixelRatio：绝大多数设备是 1.0（普通屏）或 2.0（Retina/HiDPI）
# 1.5、1.25、2.5 等占比极小；硬编码 1.5 是典型自动化客户端特征
_PIXEL_RATIO_POOL = [1.0, 1.0, 2.0, 2.0, 2.0, 1.5]
# 浏览器窗口实际可视区（page_height/width）：真实用户通常最大化 / 大部分屏幕
# page_height 必然 < screen_height，page_width <= screen_width
_VIEWPORT_RATIO_POOL = [0.95, 0.92, 0.85, 0.78]  # 占屏比例


def _persist_fp() -> None:
    with _write_lock:
        with open(globals.FP_FILE, "w", encoding="utf-8") as f:
            json.dump(globals.fp_map, f, indent=4, ensure_ascii=False)


def ensure_extended(token: str) -> Dict:
    """确保 fp_map[token] 含扩展字段；缺失则补齐并持久化。返回 fp 副本。"""
    if not token:
        return {}
    fp = globals.fp_map.setdefault(token, {})
    dirty = False

    if "screen" not in fp:
        fp["screen"] = random.choice(_SCREEN_POOL)
        dirty = True
    if "hardware_concurrency" not in fp:
        fp["hardware_concurrency"] = random.choice(_CORES_POOL)
        dirty = True
    if "device_memory" not in fp:
        fp["device_memory"] = random.choice(_DEVICE_MEMORY_POOL)
        dirty = True
    if "pixel_ratio" not in fp:
        fp["pixel_ratio"] = random.choice(_PIXEL_RATIO_POOL)
        dirty = True
    # 浏览器实际可视区：基于 screen 推导，token 级稳定（同账号多次请求不再抖动）
    if "viewport" not in fp:
        screen = fp.get("screen") or {}
        sw = int(screen.get("width") or 1920)
        sh = int(screen.get("height") or 1080)
        ratio_w = random.choice(_VIEWPORT_RATIO_POOL)
        ratio_h = random.choice(_VIEWPORT_RATIO_POOL)
        fp["viewport"] = {
            "page_width": int(sw * ratio_w),
            "page_height": int(sh * ratio_h - 120),  # 减去浏览器 chrome 高度（地址栏+标签栏）
            "screen_width": sw,
            "screen_height": sh,
        }
        dirty = True

    if dirty:
        try:
            _persist_fp()
            logger.info(f"[antiban] fingerprint extended for token={token[:12]}...")
        except Exception as e:  # pragma: no cover
            logger.error(f"[antiban] failed to persist extended fp: {e}")

    return dict(fp)


def get_stable_fp(token: str) -> Optional[Dict]:
    if not configs.enable_antiban or not token:
        return None
    fp = globals.fp_map.get(token)
    if not fp:
        return None
    return dict(fp)


def get_screen_resolution_sum(token: str) -> Optional[int]:
    """供 proofofWork.get_config 使用：返回 width+height 总和。"""
    if not configs.enable_antiban or not token:
        return None
    fp = globals.fp_map.get(token, {})
    screen = fp.get("screen")
    if isinstance(screen, dict) and "width" in screen and "height" in screen:
        return int(screen["width"]) + int(screen["height"])
    return None


def get_hardware_concurrency(token: str) -> Optional[int]:
    if not configs.enable_antiban or not token:
        return None
    val = globals.fp_map.get(token, {}).get("hardware_concurrency")
    return int(val) if val else None


def get_contextual_info(token: str) -> Optional[Dict]:
    """返回 token 级稳定的 client_contextual_info 数据，供 ChatService 注入到 chat_request。

    未启用 antiban 或 fp 未持久化 → 返回 None，调用方走老逻辑（随机）。
    """
    if not configs.enable_antiban or not token:
        return None
    fp = globals.fp_map.get(token, {})
    viewport = fp.get("viewport")
    pixel_ratio = fp.get("pixel_ratio")
    if not viewport:
        return None
    return {
        "page_width": int(viewport.get("page_width") or 1820),
        "page_height": int(viewport.get("page_height") or 960),
        "screen_width": int(viewport.get("screen_width") or 1920),
        "screen_height": int(viewport.get("screen_height") or 1080),
        "pixel_ratio": float(pixel_ratio) if pixel_ratio else 2.0,
    }


def is_fingerprint_locked(token: str) -> bool:
    return bool(globals.fp_map.get(token, {}).get("user-agent"))
