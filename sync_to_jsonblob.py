"""
将 HemLeu 押注数据同步到 jsonblob.com (作为公网共享数据后端)
供 CloudStudio 静态页面读取
"""

import json
import logging
import requests

logger = logging.getLogger(__name__)

# jsonblob.com 上的数据存储 ID
JSONBLOB_ID = "019e87b5-904b-7505-95de-0970557f40c9"
JSONBLOB_URL = f"https://jsonblob.com/api/jsonBlob/{JSONBLOB_ID}"


def sync_to_jsonblob(stats: dict, bets: list, trip_status: str):
    """
    将当前数据推送到 jsonblob
    
    Args:
        stats: HemLeu 统计数据 {'total_species': 817, 'new_species': None, ...}
        bets: 押注列表 [{'nickname': 'xxx', 'prediction': 5}, ...]
        trip_status: 行程状态 'before' / 'during' / 'after'
    """
    payload = {
        "stats": {
            "total_species": stats.get("total_species", 0),
            "new_species": stats.get("new_species"),
            "computed_at": stats.get("computed_at", ""),
        },
        "bets": [
            {
                "nickname": b["nickname"],
                "prediction": b["prediction"],
                "created_at": b.get("created_at", ""),
            }
            for b in bets
        ],
        "trip_status": trip_status,
    }
    
    try:
        resp = requests.put(
            JSONBLOB_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        if resp.status_code in [200, 204]:
            logger.info(f"数据已同步到 jsonblob: {len(bets)} 条押注, total_species={stats.get('total_species')}")
        else:
            logger.warning(f"jsonblob 同步失败: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        logger.warning(f"jsonblob 同步异常: {e}")
