"""
瓦屋山观鸟押注 - Flask 后端服务

功能:
- 用户押注 (昵称 + 预测加新数)
- HemLeu 实时数据查询 (API自动 + 管理员手动)
- 排行榜展示
- 定时刷新鸟种数据 (6月5-9日每天12:00和00:00)
"""

import logging
import threading
import time as time_module
from datetime import datetime
from pathlib import Path

from flask import Flask, request, jsonify, make_response

from database import (
    init_db, create_bet, get_all_bets, get_bet_by_nickname,
    get_latest_api_stats, get_latest_manual_stats,
    save_api_stats, set_manual_stats, set_baseline,
    get_trip_date_status,
)
from bird_fetcher import fetch_hemleu_data
from sync_to_jsonblob import sync_to_jsonblob

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
BASE_DIR = Path(__file__).parent

# ============================================================
# 飞书多维表格同步（押注数据）
# ============================================================
def sync_lark_bets():
    """从飞书多维表格同步押注数据到本地 data.db（try/except 包装，失败不阻断主流程）"""
    try:
        from sync_lark_bets import main as do_sync
        do_sync()
        logger.info("[lark-sync] 飞书押注数据同步完成")
    except Exception as e:
        logger.warning(f"[lark-sync] 飞书同步失败（已跳过）: {e}")

# CORS 支持 - 允许 CloudStudio 静态页跨域调用
@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, bypass-tunnel-reminder"
    return response

@app.route("/api/<path:p>", methods=["OPTIONS"])
def handle_options(p):
    """处理 CORS 预检请求"""
    resp = make_response("", 204)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, bypass-tunnel-reminder"
    return resp

# 刷新锁
_refresh_lock = threading.Lock()
_is_refreshing = threading.Event()

# Admin secret key for manual stats update
ADMIN_KEY = "hemleu2026wawu"


def refresh_bird_data():
    """刷新鸟种数据 (从API获取)"""
    global _is_refreshing
    
    if _is_refreshing.is_set():
        logger.info("数据刷新进行中，跳过")
        return
    
    with _refresh_lock:
        _is_refreshing.set()
        try:
            logger.info("=" * 60)
            logger.info("开始刷新 HemLeu 活动数据...")
            stats = fetch_hemleu_data()
            save_api_stats(stats)
            logger.info(
                f"刷新完成: 总鸟种={stats.get('total_species')}, "
                f"总活动={stats.get('total_records')}, "
                f"行程活动={stats.get('trip_records')}"
            )
            
            # 重新生成静态 HTML（供 CloudStudio 部署，替代 jsonblob）
            try:
                from generate_static import main as regenerate_static
                regenerate_static()
                logger.info("静态 HTML 已重新生成 -> static/index.html")
            except Exception as e:
                logger.warning(f"静态 HTML 生成跳过: {e}")
            
            logger.info("=" * 60)
        except Exception as e:
            logger.error(f"数据刷新失败: {e}", exc_info=True)
        finally:
            _is_refreshing.clear()


def scheduler_loop():
    """
    定时刷新调度器 - 每日 12:00 和 00:00 运行
    最后一次更新: 2026-06-10 00:00 (6月9日晚12点)
    """
    logger.info("定时调度器已启动 (每日 12:00 / 00:00 刷新)")
    
    FINAL_UPDATE = datetime(2026, 6, 10, 0, 0, 0)  # 6月9日晚12点
    
    while True:
        now = datetime.now()
        
        # 已过最后更新时间，停止调度
        if now >= FINAL_UPDATE:
            logger.info("已过最后更新时间 (6月10日 00:00)，调度器停止")
            break
        
        # 计算下一个目标时间: 今天12:00 或 明天0:00
        today_noon = now.replace(hour=12, minute=0, second=0, microsecond=0)
        tomorrow_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if tomorrow_midnight <= now:
            tomorrow_midnight = tomorrow_midnight.replace(day=now.day + 1)
        
        if now < today_noon:
            target = today_noon
        else:
            target = tomorrow_midnight
        
        # 不超出最后更新时间
        if target > FINAL_UPDATE:
            target = FINAL_UPDATE
        
        wait_seconds = max(0, (target - now).total_seconds())
        logger.info(
            f"下次数据刷新: {target.strftime('%Y-%m-%d %H:%M:%S')} "
            f"({wait_seconds:.0f}秒后)"
        )
        
        if wait_seconds > 0:
            time_module.sleep(wait_seconds)
        
        # 到达目标时间，执行刷新
        try:
            refresh_bird_data()
        except Exception as e:
            logger.error(f"定时刷新失败: {e}")


# ========== 路由 ==========

@app.route("/")
def index():
    """主页 - 返回静态 HTML"""
    html_path = BASE_DIR / "templates" / "index.html"
    if html_path.exists():
        return html_path.read_text(encoding='utf-8')
    return "页面不存在", 404


@app.route("/api/health")
def health():
    """健康检查"""
    return jsonify({"status": "ok", "time": datetime.now().isoformat()})


@app.route("/api/status")
def get_status():
    """获取当前状态"""
    return jsonify({
        "trip_status": get_trip_date_status(),
        "is_refreshing": _is_refreshing.is_set(),
        "current_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })


@app.route("/api/stats")
def get_stats():
    """
    获取 HemLeu 的鸟种统计和全体押注排行榜
    HemLeu 数据仅展示: 个人总鸟种数(API自动) + 瓦屋山加新数(手动基线计算)
    """
    api_stats = get_latest_api_stats()
    manual = get_latest_manual_stats()
    trip_status = get_trip_date_status()
    
    # 总鸟种数始终从 API 自动获取 (来自 record/chart/summary 的 taxon_num_1)
    total_species = api_stats.get("total_species", 0)
    
    # 加新数: 优先使用 API 自动计算的，否则使用手动设置的
    if trip_status == "before":
        new_species = None
    else:
        # 优先用 API 自动计算的加新数（save_api_stats 自动算好存在表字段里）
        new_species = api_stats.get("new_species")
        if new_species is None:
            new_species = manual.get("new_species", 0)
    
    # 数据更新时间 (优先用手动更新的时间戳)
    computed_at = api_stats.get("computed_at") or manual.get("updated_at")
    
    # 每次返回排行榜前，先从飞书同步最新押注数据
    sync_lark_bets()

    # 获取所有押注
    bets = get_all_bets()
    
    # 按预测差距排序
    ranked_bets = []
    for bet in bets:
        if trip_status != "before" and new_species is not None:
            diff = abs(bet["prediction"] - new_species)
        else:
            diff = None
        ranked_bets.append({
            "nickname": bet["nickname"],
            "prediction": bet["prediction"],
            "diff": diff,
            "created_at": bet["created_at"],
        })
    
    if trip_status != "before":
        ranked_bets.sort(key=lambda b: (
            b["diff"] if b["diff"] is not None else 99999,
            b["prediction"]
        ))
    else:
        ranked_bets.sort(key=lambda b: b["prediction"])
    
    return jsonify({
        "stats": {
            "total_species": total_species,
            "new_species": new_species,
            "computed_at": computed_at,
        },
        "bets": ranked_bets,
        "trip_status": trip_status,
    })


@app.route("/api/bet", methods=["POST"])
def place_bet():
    """提交押注 (仅6月5日前允许)"""
    data = request.get_json()
    
    if not data:
        return jsonify({"error": "请求体不能为空"}), 400
    
    nickname = data.get("nickname", "").strip()
    prediction = data.get("prediction")
    
    if not nickname:
        return jsonify({"error": "昵称不能为空"}), 400
    if len(nickname) > 20:
        return jsonify({"error": "昵称不能超过20个字符"}), 400
    if prediction is None or not isinstance(prediction, int) or prediction < 0:
        return jsonify({"error": "预测数必须是非负整数"}), 400
    if prediction > 999:
        return jsonify({"error": "预测数不能超过999"}), 400
    
    # 6月6日0点后禁止下注
    if get_trip_date_status() != "before":
        return jsonify({"error": "下注阶段已结束"}), 400
    
    # 提交前先同步飞书数据（避免重复下注）
    sync_lark_bets()
    
    try:
        bet = create_bet(nickname, prediction)
        
        # 同步到 jsonblob
        try:
            api_stats = get_latest_api_stats()
            all_bets = get_all_bets()
            trip_status = get_trip_date_status()
            sync_to_jsonblob(api_stats, all_bets, trip_status)
        except Exception as e:
            logger.warning(f"押注后 jsonblob 同步跳过: {e}")
        
        return jsonify({"success": True, "bet": bet})
    except Exception as e:
        logger.error(f"创建押注失败: {e}")
        return jsonify({"error": "服务器错误"}), 500


@app.route("/api/bet/<nickname>", methods=["GET"])
def get_bet(nickname: str):
    """查询特定用户的押注"""
    bet = get_bet_by_nickname(nickname)
    if bet:
        return jsonify({"found": True, "bet": bet})
    return jsonify({"found": False})


@app.route("/api/admin/stats", methods=["POST"])
def admin_update_stats():
    """
    管理员更新 HemLeu 数据 (需要 admin_key)
    
    第一次调用 (设置基线):
        - admin_key, total_species (行程前总鸟种数，自动作为基线)
    
    后续调用 (更新当前数据):
        - admin_key, total_species (当前总鸟种数)
        - 加新数自动计算 = 当前总鸟种数 - 基线总鸟种数
    """
    data = request.get_json()
    
    if not data:
        return jsonify({"error": "请求体不能为空"}), 400
    
    if data.get("admin_key") != ADMIN_KEY:
        return jsonify({"error": "管理员密钥错误"}), 403
    
    total_species = data.get("total_species")
    
    if total_species is None:
        return jsonify({"error": "total_species 为必填项"}), 400
    
    # 检查是否已有基线，没有则此次作为基线
    existing = get_latest_manual_stats()
    if not existing.get("updated_at"):
        set_baseline(int(total_species))
    else:
        set_manual_stats(int(total_species))
    
    return jsonify({"success": True, "message": "统计数据已更新"})


@app.route("/api/refresh", methods=["POST"])
def force_refresh():
    """手动触发数据刷新 (从API获取)"""
    if _is_refreshing.is_set():
        return jsonify({"status": "already_refreshing"})
    
    t = threading.Thread(target=refresh_bird_data, daemon=True)
    t.start()
    return jsonify({"status": "refresh_started"})


# ========== 启动 ==========

if __name__ == "__main__":
    init_db()
    
    # 启动时尝试获取初始数据
    init_stats = get_latest_api_stats()
    if not init_stats.get("computed_at"):
        logger.info("未找到缓存数据，执行首次数据获取...")
        try:
            refresh_bird_data()
        except Exception as e:
            logger.warning(f"首次数据获取失败 (可能是网络问题): {e}")
    
    # 启动定时调度器
    scheduler_thread = threading.Thread(target=scheduler_loop, daemon=True)
    scheduler_thread.start()
    
    logger.info("=" * 60)
    logger.info("🦅 瓦屋山观鸟押注系统启动!")
    logger.info("   访问地址: http://localhost:5000")
    logger.info("   押注目标: HemLeu 瓦屋山 (2026-06-05 ~ 2026-06-09)")
    logger.info("=" * 60)
    
    app.run(host="0.0.0.0", port=5000, debug=False)
