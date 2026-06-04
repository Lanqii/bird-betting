"""
数据库操作模块
使用 SQLite 存储用户押注和鸟种统计数据
"""

import sqlite3
import json
import time
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent / "data.db"


def get_connection() -> sqlite3.Connection:
    """获取数据库连接"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    """初始化数据库表"""
    conn = get_connection()
    cursor = conn.cursor()
    
    # 用户押注表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nickname TEXT NOT NULL UNIQUE,
            prediction INTEGER NOT NULL CHECK(prediction >= 0),
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            updated_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)
    
    # 统计数据缓存表 (API自动获取的估算数据)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS stats_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            total_species_est INTEGER DEFAULT 0,
            trip_species_est INTEGER DEFAULT 0,
            total_records INTEGER DEFAULT 0,
            trip_records INTEGER DEFAULT 0,
            raw_stats TEXT,
            computed_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)
    
    # 管理员手动更新的精确数据
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS manual_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            total_species INTEGER DEFAULT 0,
            baseline_total_species INTEGER DEFAULT 0,
            new_species INTEGER DEFAULT 0,
            updated_by TEXT DEFAULT 'admin',
            updated_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)
    
    conn.commit()
    conn.close()
    logger.info("数据库初始化完成")


# ========== 押注相关 ==========

def create_bet(nickname: str, prediction: int) -> dict:
    """创建或更新押注"""
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        INSERT INTO bets (nickname, prediction, updated_at)
        VALUES (?, ?, datetime('now', 'localtime'))
        ON CONFLICT(nickname) DO UPDATE SET
            prediction = excluded.prediction,
            updated_at = datetime('now', 'localtime')
    """, (nickname, prediction))
    
    conn.commit()
    cursor.execute("SELECT * FROM bets WHERE nickname = ?", (nickname,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_bets() -> list:
    """获取所有押注"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM bets ORDER BY created_at ASC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_bet_by_nickname(nickname: str) -> dict:
    """根据昵称获取押注"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM bets WHERE nickname = ?", (nickname,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


# ========== 统计数据相关 ==========

def save_api_stats(stats: dict):
    """保存 API 自动获取的估算统计数据"""
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        INSERT INTO stats_cache (total_species_est, trip_species_est,
            total_records, trip_records, raw_stats)
        VALUES (?, ?, ?, ?, ?)
    """, (
        stats.get("total_species_est", 0),
        stats.get("trip_species_est", 0),
        stats.get("total_records", 0),
        stats.get("trip_records", 0),
        json.dumps(stats, ensure_ascii=False)
    ))
    
    conn.commit()
    conn.close()


def get_latest_api_stats() -> dict:
    """获取最新的 API 估算统计"""
    conn = get_connection()
    cursor = conn.cursor()
    # 按 id DESC 排序（而非 computed_at），避免同时间戳时顺序不确定
    cursor.execute(
        "SELECT * FROM stats_cache ORDER BY id DESC LIMIT 1"
    )
    row = cursor.fetchone()
    conn.close()
    
    if row:
        row_dict = dict(row)
        if row_dict.get("raw_stats"):
            try:
                parsed = json.loads(row_dict["raw_stats"])
                row_dict.update(parsed)
            except json.JSONDecodeError:
                pass
        return row_dict
    
    return {
        "total_species_est": 0,
        "trip_species_est": 0,
        "total_records": 0,
        "trip_records": 0,
        "computed_at": None,
    }


def set_baseline(baseline_total_species: int):
    """设置行程前的基线总鸟种数（6月5日前调用一次）"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO manual_stats (total_species, baseline_total_species, new_species)
        VALUES (?, ?, 0)
    """, (baseline_total_species, baseline_total_species))
    conn.commit()
    conn.close()
    logger.info(f"设置基线总鸟种数: {baseline_total_species}")

def set_manual_stats(total_species: int):
    """管理员更新当前总鸟种数，加新数 = 当前总鸟种数 - 基线总鸟种数"""
    conn = get_connection()
    cursor = conn.cursor()
    
    # 获取基线
    cursor.execute("SELECT baseline_total_species FROM manual_stats ORDER BY updated_at DESC LIMIT 1")
    row = cursor.fetchone()
    baseline = row[0] if row else total_species
    
    new_species = max(0, total_species - baseline)
    
    cursor.execute("""
        INSERT INTO manual_stats (total_species, baseline_total_species, new_species)
        VALUES (?, ?, ?)
    """, (total_species, baseline, new_species))
    
    conn.commit()
    conn.close()
    logger.info(f"更新统计数据: 总鸟种={total_species}, 基线={baseline}, 加新={new_species}")


def get_latest_manual_stats() -> dict:
    """获取最新的手动统计数据"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM manual_stats ORDER BY updated_at DESC LIMIT 1"
    )
    row = cursor.fetchone()
    conn.close()
    
    if row:
        return dict(row)
    
    return {
        "total_species": 0,
        "baseline_total_species": 0,
        "new_species": 0,
        "updated_at": None,
    }


def get_trip_date_status() -> str:
    """
    判断当前相对于行程日期的状态
    
    Returns:
        "before" - 行程开始前
        "during" - 行程进行中
        "after" - 行程结束后
    """
    now = time.strftime("%Y-%m-%d")
    TRIP_START = "2026-06-06"
    TRIP_END = "2026-06-09"
    
    if now < TRIP_START:
        return "before"
    elif now <= TRIP_END:
        return "during"
    else:
        return "after"
