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


def is_fingerprint_locked(token: str) -> bool:
    return bool(globals.fp_map.get(token, {}).get("user-agent"))
