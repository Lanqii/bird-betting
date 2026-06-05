"""
从飞书多维表格同步押注数据到本地 data.db。
- 以昵称为 key 做 upsert（已有则更新，没有则插入）
- 跳过昵称为空或预测数为空/非法的行
- 使用 lark-cli（WorkBuddy 内置），通过 shell 调用（系统 PATH）
"""
import json
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "data.db"

# 飞书多维表格参数
LARK_BASE_TOKEN = "LG7RblWFyaGV45sURmtcTmevnTX"
LARK_TABLE_ID = "tblmzStAbDAEbTZP"

# 忽略的测试昵称（只过滤明显的英文测试占位符）
IGNORE_NICKNAMES = {"test", "Test", "TEST"}


def fetch_lark_bets():
    """
    用 lark-cli 读取飞书多维表格中所有押注记录。
    返回 list of dict: [{"nickname": str, "prediction": int}, ...]
    """
    # 用 shell=True + 直接调 lark-cli 命令名（走系统 PATH）
    cmd = (
        f"lark-cli base +record-list"
        f" --base-token {LARK_BASE_TOKEN}"
        f" --table-id {LARK_TABLE_ID}"
        f" --as user"
        f" --format json"
    )

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            encoding="utf-8",
            shell=True,   # Windows 下通过 shell 找到 lark-cli
        )

        raw = result.stdout.strip()
        if not raw:
            print(f"[sync_lark] lark-cli 无输出, stderr={result.stderr[:200]}", file=sys.stderr)
            return []

        # lark-cli 可能在 stdout 里混入 WARN 行，提取 JSON 部分
        lines = raw.splitlines()
        json_lines = [l for l in lines if l.strip().startswith("{") or l.strip().startswith("[")]
        # 取最后一个连续 JSON 块（跳过 WARN 日志行）
        json_str = "\n".join(l for l in lines if not l.startswith("[lark-cli]"))
        json_str = json_str.strip()

        data = json.loads(json_str)
        if not data.get("ok"):
            print(f"[sync_lark] lark-cli ok=false: {json_str[:200]}", file=sys.stderr)
            return []

        inner = data.get("data", {})
        fields = inner.get("fields", [])          # ["ID", "预测加新数", "昵称"]
        rows = inner.get("data", [])              # [[val0, val1, val2], ...]

        # 建立列名 -> 列下标的映射
        try:
            idx_nickname = fields.index("昵称")
            idx_prediction = fields.index("预测加新数")
        except ValueError as e:
            print(f"[sync_lark] 字段不存在: {e}，fields={fields}", file=sys.stderr)
            return []

        results = []
        for row in rows:
            try:
                nickname_raw = row[idx_nickname]
                # 富文本可能是列表，普通文本是字符串
                if isinstance(nickname_raw, list):
                    nickname = "".join(
                        seg.get("text", "") if isinstance(seg, dict) else str(seg)
                        for seg in nickname_raw
                    ).strip()
                else:
                    nickname = str(nickname_raw).strip()

                if not nickname or nickname in IGNORE_NICKNAMES:
                    continue

                pred_raw = row[idx_prediction]
                if pred_raw is None:
                    continue
                prediction = int(float(pred_raw))
                if not (0 <= prediction <= 999):
                    continue

                results.append({"nickname": nickname, "prediction": prediction})
            except (IndexError, ValueError, TypeError):
                continue

        return results

    except subprocess.TimeoutExpired:
        print("[sync_lark] lark-cli 超时", file=sys.stderr)
        return []
    except json.JSONDecodeError as e:
        print(f"[sync_lark] JSON 解析失败: {e}\n原始输出: {raw[:300]}", file=sys.stderr)
        return []
    except Exception as e:
        print(f"[sync_lark] 调用失败: {e}", file=sys.stderr)
        return []


def sync_to_db(bets):
    """将解析后的押注 upsert 到本地 data.db"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    inserted = 0
    updated = 0

    for bet in bets:
        nickname = bet["nickname"]
        prediction = bet["prediction"]

        existing = c.execute(
            "SELECT id, prediction FROM bets WHERE nickname = ?", (nickname,)
        ).fetchone()

        if existing:
            if existing[1] != prediction:
                c.execute(
                    "UPDATE bets SET prediction = ?, updated_at = ? WHERE nickname = ?",
                    (prediction, now, nickname),
                )
                updated += 1
        else:
            c.execute(
                "INSERT INTO bets (nickname, prediction, created_at, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (nickname, prediction, now, now),
            )
            inserted += 1

    conn.commit()
    conn.close()
    return inserted, updated


def main():
    print("[sync_lark] 开始从飞书多维表格同步押注数据...")
    bets = fetch_lark_bets()

    if not bets:
        print("[sync_lark] 未获取到有效记录，跳过同步")
        return False

    print(f"[sync_lark] 获取到 {len(bets)} 条有效押注")
    inserted, updated = sync_to_db(bets)
    print(f"[sync_lark] 同步完成: 新增={inserted}, 更新={updated}")
    return True


if __name__ == "__main__":
    main()
