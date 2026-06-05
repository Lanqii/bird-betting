"""
生成包含内嵌数据的静态 HTML 页面，并重新部署到 CloudStudio。
每次 Flask 刷新数据后调用此脚本。
"""
import json
import sqlite3
import subprocess
from pathlib import Path
from datetime import datetime, timezone

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "data.db"
STATIC_DIR = BASE_DIR / "static"
TEMPLATE_PATH = BASE_DIR / "static_template.html"

# 飞书多维表格押注表单链接（任何人打开飞书/手机端可以填写）
LARK_FORM_URL = "https://my.feishu.cn/base/LG7RblWFyaGV45sURmtcTmevnTX?table=tblmzStAbDAEbTZP&view=vewI2NT7G7"


def get_current_data():
    """从数据库读取最新数据"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    # 最新鸟种数据
    c.execute("SELECT * FROM stats_cache ORDER BY id DESC LIMIT 1")
    row = c.fetchone()
    raw = json.loads(row["raw_stats"]) if row else {}
    
    # 所有押注（从 Flask 数据库读取）
    c.execute("SELECT nickname, prediction, created_at FROM bets ORDER BY prediction DESC")
    bets = [
        {
            "nickname": r["nickname"],
            "prediction": r["prediction"],
            "created_at": r["created_at"] or ""
        }
        for r in c.fetchall()
    ]
    
    conn.close()
    
    return {
        "total_species": raw.get("total_species", 0),
        "new_species": raw.get("new_species"),
        "computed_at": row["computed_at"] if row else "",
        "bets": bets,
    }


def get_trip_status():
    """判断行程状态"""
    now = datetime.now()
    trip_start = datetime(2026, 6, 6, 0, 0, 0)
    trip_end = datetime(2026, 6, 9, 23, 59, 59)
    
    if now < trip_start:
        return "before"
    elif now <= trip_end:
        return "during"
    else:
        return "after"


def generate_html(data, trip_status):
    """生成包含内嵌数据的静态 HTML"""
    total_species = data["total_species"]
    new_species = data.get("new_species")
    computed_at = data.get("computed_at", "")
    bets = data.get("bets", [])
    
    # 生成排行榜 HTML
    bet_count_text = f"{len(bets)} 人已押注"
    if not bets:
        leaderboard_html = '<div class="empty-state">还没有人下注，成为第一个吧！</div>'
    else:
        rows = []
        for i, bet in enumerate(bets):
            rank = i + 1
            rank_icons = {1: "🥇", 2: "🥈", 3: "🥉"}
            rank_icon = rank_icons.get(rank, str(rank))
            rank_class = {1: "top1", 2: "top2", 3: "top3"}.get(rank, "normal")
            
            if trip_status != "before" and new_species is not None:
                diff = abs(bet["prediction"] - new_species)
                if diff == 0:
                    diff_html = '<span class="lb-diff exact">🎯 精准命中!</span>'
                else:
                    diff_class = "close" if diff <= 5 else "far"
                    diff_html = f'<span class="lb-diff {diff_class}">差 {diff} 种</span>'
            else:
                diff_html = '<span class="lb-diff waiting">等待揭晓</span>'
            
            rows.append(
                f'<div class="lb-row">'
                f'<div class="lb-rank {rank_class}">{rank_icon}</div>'
                f'<div class="lb-nickname">{bet["nickname"]}</div>'
                f'<div>{diff_html}</div>'
                f'<div class="lb-prediction">{bet["prediction"]}</div>'
                f'</div>'
            )
        leaderboard_html = "\n".join(rows)
    
    # 新鸟种显示
    if trip_status == "before":
        new_species_text = "还未开始"
        new_species_class = "stat-value pending"
    else:
        new_species_text = str(new_species) if new_species is not None else "-"
        new_species_class = "stat-value highlight"
    
    # 状态栏文字
    status_map = {
        "before": "等待行程开始 (6月6日)",
        "during": "行程进行中 - 实时更新",
        "after": "行程已结束",
    }
    status_text = status_map.get(trip_status, "")
    status_dot_live = ' live' if trip_status == "during" else ""
    
    # 下注区域
    if trip_status == "before":
        bet_section = f"""
        <div class="card" id="betCard">
            <div class="card-title"><span class="icon">🎯</span> 下注你的预测</div>
            <p style="font-size:13px;color:#555;margin-bottom:14px;">预测 HemLeu 在瓦屋山会加新多少种鸟？（行程期间首次见到的新鸟种）</p>
            <a href="{LARK_FORM_URL}" target="_blank" class="lark-form-btn">
                📝 在飞书表单中填写押注
            </a>
            <p style="font-size:11px;color:#aaa;margin-top:10px;text-align:center;">填写后刷新本页面查看排行榜</p>
        </div>
        """
    else:
        bet_section = """
        <div class="card" id="betCard">
            <div class="card-title"><span class="icon">🔒</span> 押注已结束</div>
            <p style="font-size:13px;color:#aaa;text-align:center;">行程已开始，下注已截止</p>
        </div>
        """
    
    update_text = f"更新时间：{computed_at}" if computed_at else "等待首次数据刷新..."
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>瓦屋山观鸟押注 🦅</title>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
:root {{
    --primary: #2d6a4f;
    --primary-light: #52b788;
    --primary-bg: #d8f3dc;
    --danger: #e63946;
    --text: #1b1b1b;
    --text-secondary: #666;
    --border: #e0e7da;
    --bg: #f6faf6;
    --card-bg: #fff;
    --font: -apple-system, BlinkMacSystemFont, 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif;
}}
body {{ background: var(--bg); color: var(--text); font-family: var(--font); min-height: 100vh; }}
.container {{ max-width: 520px; margin: 0 auto; padding: 16px 12px 40px; }}
.header {{ text-align: center; padding: 24px 0 16px; }}
.header-icon {{ font-size: 48px; display: block; margin-bottom: 8px; }}
h1 {{ font-size: 24px; font-weight: 800; color: var(--primary); letter-spacing: -0.5px; }}
.subtitle {{ font-size: 14px; color: var(--text-secondary); margin-top: 4px; }}
.trip-info {{ display: inline-block; margin-top: 8px; background: var(--primary-bg); color: var(--primary); border-radius: 20px; padding: 4px 14px; font-size: 13px; font-weight: 600; }}
.status-bar {{ display: flex; align-items: center; gap: 8px; background: var(--card-bg); border-radius: 12px; padding: 10px 14px; margin-bottom: 12px; border: 1px solid var(--border); font-size: 13px; color: var(--text-secondary); }}
.status-dot {{ width: 8px; height: 8px; border-radius: 50%; background: #ccc; flex-shrink: 0; }}
.status-dot.live {{ background: #22c55e; box-shadow: 0 0 0 3px rgba(34,197,94,.2); animation: pulse 2s infinite; }}
@keyframes pulse {{ 0%,100% {{ box-shadow: 0 0 0 3px rgba(34,197,94,.2); }} 50% {{ box-shadow: 0 0 0 6px rgba(34,197,94,.05); }} }}
.card {{ background: var(--card-bg); border-radius: 16px; padding: 20px; margin-bottom: 16px; border: 1px solid var(--border); }}
.card-title {{ font-weight: 700; font-size: 15px; margin-bottom: 14px; display: flex; align-items: center; gap: 6px; }}
.card-title .icon {{ font-size: 18px; }}
.stats-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }}
.stat-item {{ text-align: center; background: var(--bg); border-radius: 12px; padding: 14px 8px; }}
.stat-value {{ font-size: 38px; font-weight: 800; color: var(--primary); line-height: 1.1; }}
.stat-value.highlight {{ color: var(--danger); font-size: 42px; }}
.stat-value.pending {{ font-size: 18px; color: #999; font-weight: 600; }}
.stat-label {{ font-size: 12px; color: var(--text-secondary); margin-top: 4px; }}
.last-update {{ font-size: 11px; color: #aaa; text-align: center; margin-top: 8px; }}
.lb-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }}
.lb-count {{ font-size: 13px; color: var(--text-secondary); }}
.lb-row {{ display: flex; align-items: center; gap: 8px; padding: 10px 0; border-bottom: 1px solid var(--border); }}
.lb-row:last-child {{ border-bottom: none; }}
.lb-rank {{ width: 28px; text-align: center; font-size: 18px; font-weight: 700; color: #bbb; flex-shrink: 0; }}
.lb-rank.top1 {{ color: #f59e0b; }}
.lb-rank.top2 {{ color: #94a3b8; }}
.lb-rank.top3 {{ color: #b45309; }}
.lb-nickname {{ flex: 1; font-size: 15px; font-weight: 600; }}
.lb-diff {{ font-size: 12px; color: var(--text-secondary); }}
.lb-diff.exact {{ color: #22c55e; font-weight: 700; }}
.lb-diff.close {{ color: var(--primary); }}
.lb-diff.far {{ color: #aaa; }}
.lb-diff.waiting {{ color: #bbb; font-style: italic; }}
.lb-prediction {{ font-size: 18px; font-weight: 800; color: var(--primary); min-width: 36px; text-align: right; }}
.empty-state {{ text-align: center; color: #aaa; padding: 24px; font-size: 14px; }}
.lark-form-btn {{ display: block; background: var(--primary); color: white; text-align: center; padding: 14px; border-radius: 12px; font-size: 16px; font-weight: 700; text-decoration: none; }}
.lark-form-btn:hover {{ background: #1e4d38; }}
@media (max-width: 480px) {{
    .stats-grid {{ grid-template-columns: repeat(2, 1fr); gap: 6px; }}
    .stat-value {{ font-size: 32px; }}
    .stat-value.highlight {{ font-size: 36px; }}
    .card {{ padding: 16px; }}
    .container {{ padding: 12px 10px 30px; }}
}}
</style>
</head>
<body>
<div class="container">
    <div class="header">
        <div class="header-icon">🦅</div>
        <h1>瓦屋山观鸟押注</h1>
        <p class="subtitle">竞猜 HemLeu 能加新多少种鸟？</p>
        <span class="trip-info">📅 2026.06.05 - 06.09</span>
    </div>

    <div class="status-bar">
        <span class="status-dot{status_dot_live}"></span>
        <span>{status_text}</span>
    </div>

    <div class="card">
        <div class="card-title"><span class="icon">📊</span> HemLeu 实时数据</div>
        <div class="stats-grid">
            <div class="stat-item">
                <div class="stat-value">{total_species}</div>
                <div class="stat-label">个人总鸟种数</div>
            </div>
            <div class="stat-item">
                <div class="{new_species_class}">{new_species_text}</div>
                <div class="stat-label">🏆 瓦屋山加新</div>
            </div>
        </div>
        <div class="last-update">{update_text}</div>
    </div>

    <div class="card">
        <div class="lb-header">
            <div class="card-title" style="margin-bottom:0"><span class="icon">🏆</span> 押注排行榜</div>
            <div class="lb-count">{bet_count_text}</div>
        </div>
        {leaderboard_html}
    </div>

    {bet_section}

    <div style="text-align:center;padding:16px;color:#aaa;font-size:11px;">
        数据来源：中国观鸟记录中心 · 每日 12:00 / 00:00 自动刷新<br>
        页面生成时间：{generated_at}
    </div>
</div>
</body>
</html>"""


def refresh_bird_data_if_stale():
    """如果鸟种数据缓存超过 6 小时，从 birdreport.cn API 刷新"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT computed_at FROM stats_cache ORDER BY id DESC LIMIT 1")
    row = c.fetchone()
    conn.close()
    
    if row and row[0]:
        last_update = datetime.fromisoformat(row[0].replace("Z", "+00:00").replace(" ", "T"))
        age_hours = (datetime.now(timezone.utc).replace(tzinfo=None) - last_update.replace(tzinfo=None)).total_seconds() / 3600
        if age_hours < 6:
            print(f"[bird-refresh] 缓存新鲜 ({age_hours:.1f}小时前)，跳过 API 请求")
            return
    
    print("[bird-refresh] 缓存过期或不存在，从 birdreport.cn 获取数据...")
    try:
        from bird_fetcher import fetch_hemleu_data
        from database import save_api_stats
        stats = fetch_hemleu_data()
        save_api_stats(stats)
        print(
            f"[bird-refresh] OK: total_species={stats.get('total_species')}, "
            f"total_records={stats.get('total_records')}, "
            f"trip_records={stats.get('trip_records')}"
        )
    except Exception as e:
        print(f"[bird-refresh] 失败（使用旧缓存）: {e}")


def main():
    # Step 1: 刷新鸟种数据（如果缓存过期）——保证即使 Flask 没跑也能拿到最新数据
    try:
        refresh_bird_data_if_stale()
    except Exception as e:
        print(f"[generate_static] 鸟种刷新跳过: {e}")

    # Step 2: 从飞书多维表格同步最新押注数据
    try:
        from sync_lark_bets import main as sync_lark
        sync_lark()
    except Exception as e:
        print(f"[generate_static] 飞书同步跳过: {e}")

    data = get_current_data()
    trip_status = get_trip_status()
    
    html = generate_html(data, trip_status)
    
    output_path = STATIC_DIR / "index.html"
    output_path.write_text(html, encoding="utf-8")
    
    print(f"[OK] Generated static/index.html")
    print(f"   total_species={data['total_species']}, bets={len(data['bets'])}条, trip_status={trip_status}")
    # Note: stats cached by CloudStudio CDN; add ?t= cache buster on share
    return True


if __name__ == "__main__":
    main()
