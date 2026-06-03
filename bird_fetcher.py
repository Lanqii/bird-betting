"""
观鸟记录中心 (birdreport.cn) 数据抓取模块
用于获取指定用户 HemLeu 的观鸟记录数据

API说明:
- 活动搜索: POST https://api.birdreport.cn/front/activity/search
- 加密: RSA加密请求体 + MD5签名 + AES解密响应
- 注意: 活动搜索仅返回摘要数据(时间/地点/鸟种数量), 不含具体鸟种名称
        具体鸟种名称需登录后查看, 本模块通过 taxonCount 估算
"""

import base64
import hashlib
import json
import random
import time
import logging
from typing import Optional

import requests
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_v1_5, AES
from Crypto.Util.Padding import unpad

logger = logging.getLogger(__name__)

# ========== 常量 ==========
API_BASE = "https://api.birdreport.cn"
ACTIVITY_SEARCH = "/front/activity/search"

# RSA 公钥 (Base64编码)
RSA_PUBLIC_KEY_B64 = (
    "MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQCvxXa98E1uWXnBzXkS2yHUfnBM6n3P"
    "CwLdfIox03T91joBvjtoDqiQ5x3tTOfpHs3LtiqMMEafls6b0YWtgB1dse1W5m+Fpeus"
    "VkCOkQxB4SZDH6tuerIknnmB/Hsq5wgEkIvO5Pff9biig6AyoAkdWpSek/1/B7zYIepY"
    "Y0lxKQIDAQAB"
)

# AES 解密密钥和偏移量 (二选一, 尝试两个版本)
AES_KEYS = [
    ("C8EB5514AF5ADDB94B2207B08C66601C", "55DD79C6F04E1A67"),
    ("3583ec0257e2f4c8195eec7410ff1619", "d93c0d5ec6352f20"),
]

# 目标用户
TARGET_USER = "HemLeu"

# 瓦屋山行程日期范围
TRIP_START = "2026-06-05"
TRIP_END = "2026-06-09"


def _generate_request_id() -> str:
    """生成自定义 UUID"""
    hex_digits = "0123456789abcdef"
    s = [random.choice(hex_digits) for _ in range(32)]
    s[14] = '4'
    s[19] = hex_digits[(ord(s[19]) & 3) | 8]
    s[8] = s[13] = s[18] = s[23]
    return ''.join(s)


def _generate_sign(params_str: str, request_id: str, timestamp: str) -> str:
    """生成 MD5 签名"""
    raw = params_str + request_id + timestamp
    return hashlib.md5(raw.encode('utf-8')).hexdigest()


def _rsa_encrypt(plaintext: str) -> str:
    """RSA 公钥加密"""
    public_key = RSA.import_key(base64.b64decode(RSA_PUBLIC_KEY_B64))
    cipher = PKCS1_v1_5.new(public_key)
    enc_data = cipher.encrypt(plaintext.encode('utf-8'))
    return base64.b64encode(enc_data).decode('utf-8')


def _try_aes_decrypt(enc_data: str) -> Optional[str]:
    """尝试用多个 AES 密钥解密"""
    for aes_key, aes_iv in AES_KEYS:
        try:
            cipher = AES.new(aes_key.encode(), AES.MODE_CBC, iv=aes_iv.encode())
            decrypted = unpad(
                cipher.decrypt(base64.b64decode(enc_data)),
                AES.block_size
            )
            return decrypted.decode('utf-8')
        except Exception:
            continue
    return None


def _api_request(params: dict, endpoint: str = ACTIVITY_SEARCH, sort_keys: bool = False) -> dict:
    """
    发送加密请求到观鸟记录中心 API
    
    Args:
        params: 查询参数字典 (保持简短, RSA 1024 最多加密 ~117 字节)
        endpoint: API 端点路径
        sort_keys: 是否对参数 key 做 ASCII 排序 (部分端点需要)
    
    Returns:
        解密后的 JSON 响应数据 (或原文, 如果 data 不是 AES 加密的)
    """
    # 前端 JavaScript 会对参数 key 做 sort_ASCII 后再 JSON.stringify
    # 部分端点 (如 chart/summary) 严格要求排序，否则签名验证失败
    import collections
    if sort_keys:
        sorted_params = collections.OrderedDict(sorted(params.items()))
    else:
        sorted_params = params
    
    params_str = json.dumps(sorted_params, separators=(',', ':'), ensure_ascii=False)
    
    timestamp = str(int(time.time() * 1000))
    request_id = _generate_request_id()
    sign = _generate_sign(params_str, request_id, timestamp)
    encrypted_body = _rsa_encrypt(params_str)
    
    headers = {
        "Origin": "https://www.birdreport.cn",
        "Referer": "https://www.birdreport.cn/",
        "Requestid": request_id,
        "Sign": sign,
        "Timestamp": timestamp,
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    }
    
    url = API_BASE + endpoint
    resp = requests.post(url, data=encrypted_body, headers=headers, timeout=30)
    resp.raise_for_status()
    
    result = resp.json()
    
    # 尝试 AES 解密 data 字段 (仅当 data 是字符串时才可能是 AES 密文)
    if isinstance(result, dict) and isinstance(result.get('data'), str):
        decrypted = _try_aes_decrypt(result['data'])
        if decrypted:
            try:
                return json.loads(decrypted)
            except json.JSONDecodeError:
                pass
    
    return result


def search_activities(
    username: str = TARGET_USER,
    page: int = 1,
    limit: int = 50
) -> list:
    """
    搜索用户活动记录 (仅返回摘要, 不含具体鸟种名称)
    
    Args:
        username: 用户名
        page: 页码
        limit: 每页条数 (最大 50, 超过会触发服务器限流)
    
    Returns:
        活动记录列表, 每条包含:
        - serialId: 记录编号
        - startTime/endTime: 观测时间
        - taxonCount: 鸟种数量
        - pointName/address: 观测地点
    """
    params = {
        "limit": str(min(limit, 50)),
        "page": str(page),
        "username": username,
    }
    
    try:
        result = _api_request(params, ACTIVITY_SEARCH)
    except Exception as e:
        logger.warning(f"API 请求失败 (page={page}): {e}")
        return []
    
    if isinstance(result, list):
        return result
    
    if isinstance(result, dict):
        # 检查是否服务器错误
        if not result.get("success", True) or result.get("code") in [500, 403]:
            logger.warning(f"API 返回错误: code={result.get('code')}, msg={result.get('msg', '')}")
            return []
        
        for key in ['records', 'list', 'data']:
            val = result.get(key)
            if isinstance(val, list):
                return val
    
    logger.warning(f"无法解析活动记录: type={type(result).__name__}")
    return []


def fetch_species_summary(username: str = TARGET_USER, version: str = "CH4") -> dict:
    """
    获取用户鸟种汇总统计 (来自 record/chart/summary 端点)

    这是观鸟记录中心搜索页面上显示的三个汇总指标:
    - taxon_num_1: 个人总鸟种数 — 按指定鸟种版本去重后的准确值
    - report_num_1: 报告数量
    - record_num_1: 记录数量

    注意:
    - 此端点要求参数 key 按 ASCII 排序后签名（前端 JS 的 sort_ASCII）
    - version='CH4' 对应"中国鸟类分类与分布名录(第四版-郑四)"，这才是准确的"鸟种数量"
      不加 version 参数时返回的是全版本汇总值（偏大）

    Args:
        username: 用户名
        version: 鸟种版本, 'CH4'=郑四版, 'G3'=年报3.0版, ''=不限

    Returns:
        {
            "species_count": 594,      # 个人总鸟种数 (按版本去重)
            "report_count": 495,       # 报告数量
            "record_count": 10933,     # 记录数量
            "raw": {...}               # 原始响应
        }
    """
    CHART_SUMMARY = "/front/record/chart/summary"
    params = {
        "mode": "0",
        "taxonid": "",
        "username": username,
    }
    if version:
        params["version"] = version

    try:
        result = _api_request(params, CHART_SUMMARY, sort_keys=True)
    except Exception as e:
        logger.warning(f"获取鸟种汇总失败: {e}")
        return {"species_count": 0, "report_count": 0, "record_count": 0}

    if not isinstance(result, dict) or not result.get("success"):
        logger.warning(f"鸟种汇总API返回错误: {result.get('msg', '')}")
        return {"species_count": 0, "report_count": 0, "record_count": 0}

    data = result.get("data", {})
    if not isinstance(data, dict):
        return {"species_count": 0, "report_count": 0, "record_count": 0}

    return {
        "species_count": data.get("taxon_num_1", 0),
        "report_count": data.get("report_num_1", 0),
        "record_count": data.get("record_num_1", 0),
        "reports_total": data.get("reports_count", 0),
        "records_total": data.get("record_count", 0),
        "raw": data,
    }


def fetch_all_activities(username: str = TARGET_USER) -> list:
    """
    获取用户所有活动记录 (分页获取全部)
    
    Returns:
        所有活动记录的列表
    """
    all_activities = []
    page = 1
    page_size = 50  # API 最大允许 50 条/页
    max_pages = 40  # 安全上限: 最多 40 页 = 2000 条记录
    
    logger.info(f"开始获取 {username} 的所有活动记录...")
    
    while page <= max_pages:
        records = search_activities(username=username, page=page, limit=page_size)
        
        if not records:
            logger.info(f"  第 {page} 页无记录，停止获取")
            break
        
        all_activities.extend(records)
        logger.info(f"  第 {page} 页: {len(records)} 条, 累计 {len(all_activities)} 条")
        
        if len(records) < page_size:
            break
        
        page += 1
        time.sleep(0.5)  # 避免触发限流
    
    logger.info(f"共获取 {len(all_activities)} 条活动记录")
    return all_activities


def compute_stats_from_activities(activities: list) -> dict:
    """
    基于活动记录计算统计数据 (使用 taxonCount 估算)
    
    注意: 由于活动记录不含具体鸟种名称, 这里的统计是估算值:
    - total_species: 所有记录 taxonCount 的总和 (上限估计)
    - trip_species: 行程期间 taxonCount 的总和 (上限估计)  
    - new_species: 行程期间记录的新活动数 (即首次记录到该鸟种数量级别的活动)
    
    精确的"加新"数量需要从观鸟记录中心网站手动查看并更新
    """
    # 按时间分类
    pre_trip_activities = []
    trip_activities = []
    
    for act in activities:
        start = act.get('startTime') or act.get('start_time') or ''
        if TRIP_START <= start[:10] <= TRIP_END:
            trip_activities.append(act)
        elif start < TRIP_START:
            pre_trip_activities.append(act)
    
    # 计算总鸟种估算 (所有 taxonCount 之和)
    total_est = sum(int(a.get('taxonCount', 0) or 0) for a in activities)
    pre_trip_est = sum(int(a.get('taxonCount', 0) or 0) for a in pre_trip_activities)
    trip_est = sum(int(a.get('taxonCount', 0) or 0) for a in trip_activities)
    
    # 获取行程活动详情
    trip_details = []
    for act in trip_activities:
        start = act.get('startTime') or act.get('start_time') or ''
        end = act.get('endTime') or act.get('end_time') or ''
        location = act.get('pointName') or act.get('point_name') or act.get('address', '')
        count = act.get('taxonCount') or act.get('taxoncount') or 0
        sid = act.get('serialId') or act.get('serial_id') or ''
        trip_details.append({
            'date': start[:10] if start else '',
            'location': location,
            'count': int(count) if count else 0,
            'serialId': str(sid),
        })
    
    return {
        # 估算值
        "total_species_est": total_est,
        "pre_trip_species_est": pre_trip_est,
        "trip_species_est": trip_est,
        # 记录计数
        "total_records": len(activities),
        "trip_records": len(trip_activities),
        # 行程详情
        "trip_details": trip_details,
        # 更新时间
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def fetch_hemleu_data() -> dict:
    """
    获取 HemLeu 的完整数据 (从 API 获取并计算统计)
    
    数据来源:
    - species_count: 来自 record/chart/summary 的 taxon_num_1 (按郑四版去重后的个人总鸟种数)
    - 活动记录: 来自 activity/search (用于行程期间分析)
    
    Returns:
        统计数据字典
    """
    # 1. 获取准确的鸟种总数 (按郑四版 CH4 去重)
    summary = fetch_species_summary(TARGET_USER, version="CH4")
    species_count = summary.get("species_count", 0)
    report_count = summary.get("report_count", 0)
    record_count = summary.get("record_count", 0)
    
    # 2. 获取活动记录 (用于行程期间分析)
    activities = fetch_all_activities(TARGET_USER)
    stats = compute_stats_from_activities(activities)
    
    # 3. 使用准确的鸟种数替换估算值
    stats["total_species"] = species_count  # 准确值! (郑四版去重)
    stats["total_species_est"] = species_count  # 向后兼容
    stats["total_reports"] = report_count
    stats["total_records_count"] = record_count
    
    return stats


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    stats = fetch_hemleu_data()
    print(json.dumps(stats, ensure_ascii=False, indent=2))
