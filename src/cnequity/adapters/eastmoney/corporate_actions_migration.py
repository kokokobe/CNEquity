"""BSE's published old (43/83/87xxx) → new (920xxx) code table.

Readers that meet a legacy Beijing code use it to find the same company under
its current code (delisted coverage, Tushare ST history, the BSE code repair).
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

__all__: list[str] = []


# Snapshot of BSE's published old/new code table (bse.cn/service/code_mapping.html).
# It is data, not a suffix heuristic: collisions such as 870726→920926 and
# 873305→920505 are real and would otherwise query another company's history.
_MAPPING_PATH = Path(__file__).with_name("seeds") / "bse_code_mapping.json"


@lru_cache(maxsize=1)
def _code_mapping() -> dict[str, str]:
    try:
        raw = json.loads(_MAPPING_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(
            f"BSE legacy/current code mapping is unreadable: {_MAPPING_PATH}"
        ) from exc
    if not isinstance(raw, dict):
        raise RuntimeError(f"BSE legacy/current code mapping is not an object: {_MAPPING_PATH}")
    return {str(old): str(new) for old, new in raw.items()}
