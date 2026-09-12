# =========================================================
# NWSL / WSL 順位表・日程結果・得点/アシストランキング
# 静的HTML生成スクリプト(GitHub Actions実行用)
# =========================================================
# Colab版(nwsl_wsl_all_in_one.py)から、IPython表示部分を除き、
# docs/index.html への出力に変更したもの。
# ロジック(データ取得・HTML生成)はColab版と同一。

from __future__ import annotations  # 型ヒント(dict | None等)の互換性のため

from curl_cffi import requests as cffi_requests
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import pandas as pd
import os
import json
import sys
import time
from pathlib import Path

JST = ZoneInfo("Asia/Tokyo")
LEAGUES = {"NWSL": "usa.nwsl", "WSL": "eng.w.1"}
TIMEOUT = 15

LEAGUE_STYLE = {
    "NWSL": {"band_bg": "#eaf2fb", "label_bg": "#2f6fb3"},
    "WSL":  {"band_bg": "#fbeaea", "label_bg": "#b3392f"},
}
MEDAL_COLORS = {1: "#c9a227", 2: "#9aa0a6", 3: "#b5651d"}

STAT_NAME_CANDIDATES = {
    "played":        ["gamesPlayed", "played"],
    "win":           ["wins", "win"],
    "draw":          ["ties", "draws", "draw"],
    "loss":          ["losses", "loss"],
    "goals_for":     ["pointsFor", "goalsFor"],
    "goals_against": ["pointsAgainst", "goalsAgainst"],
    "diff":          ["pointDifferential", "goalDifferential", "differential"],
    "points":        ["points"],
}


def _get(url, params=None, max_retries=3, backoff_seconds=5):
    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = cffi_requests.get(url, params=params, impersonate="chrome124", timeout=TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            last_error = e
            print(f"  [警告] リクエスト失敗 (試行 {attempt}/{max_retries}): {url}\n    詳細: {e}")
            if attempt < max_retries:
                time.sleep(backoff_seconds * attempt)  # 5秒, 10秒, ... と間隔を広げる
    # 全リトライ失敗時は例外を投げ、呼び出し元(main)で捕捉させる
    raise last_error


def _find_stat(stats_dict, key):
    for candidate in STAT_NAME_CANDIDATES[key]:
        if candidate in stats_dict:
            return stats_dict[candidate]
    return None


def _parse_dt(s: str) -> datetime:
    return datetime.strptime(s.replace(" JST", ""), "%Y-%m-%d %H:%M")


# ---------------------------------------------------------
# データ取得
# ---------------------------------------------------------

def get_standings(league_name: str) -> pd.DataFrame:
    slug = LEAGUES[league_name]
    url = f"https://site.api.espn.com/apis/v2/sports/soccer/{slug}/standings"
    data = _get(url)
    entries = data["children"][0]["standings"]["entries"]
    rows = []
    for entry in entries:
        team = entry.get("team", {}).get("displayName")
        stats_dict = {s.get("name"): s.get("value") for s in entry.get("stats", [])}
        rows.append({
            "順位": stats_dict.get("rank"), "チーム": team,
            "試合数": _find_stat(stats_dict, "played"),
            "勝": _find_stat(stats_dict, "win"), "分": _find_stat(stats_dict, "draw"),
            "敗": _find_stat(stats_dict, "loss"),
            "得点": _find_stat(stats_dict, "goals_for"),
            "失点": _find_stat(stats_dict, "goals_against"),
            "得失点差": _find_stat(stats_dict, "diff"),
            "勝点": _find_stat(stats_dict, "points"),
        })
    df = pd.DataFrame(rows)
    if df["順位"].notna().all():
        df = df.sort_values("順位")
    return df.reset_index(drop=True)


def _to_jst_str(utc_str: str) -> str:
    dt_utc = datetime.strptime(utc_str, "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)
    return dt_utc.astimezone(JST).strftime("%Y-%m-%d %H:%M JST")


def get_fixtures(league_name: str, days_back: int = 14, days_forward: int = 21) -> pd.DataFrame:
    slug = LEAGUES[league_name]
    url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/scoreboard"
    today = datetime.now(timezone.utc)
    start_date = datetime.fromordinal(today.date().toordinal() - days_back).strftime("%Y%m%d")
    end_date = datetime.fromordinal(today.date().toordinal() + days_forward).strftime("%Y%m%d")
    data = _get(url, params={"dates": f"{start_date}-{end_date}"})
    rows = []
    for ev in data.get("events", []):
        comp = ev.get("competitions", [{}])[0]
        competitors = comp.get("competitors", [])
        home = next((c for c in competitors if c.get("homeAway") == "home"), {})
        away = next((c for c in competitors if c.get("homeAway") == "away"), {})
        status = comp.get("status", {}).get("type", {})
        is_done = status.get("completed", False)
        rows.append({
            "event_id": ev.get("id"),
            "日時(JST)": _to_jst_str(ev.get("date")),
            "ホーム": home.get("team", {}).get("displayName"),
            "アウェイ": away.get("team", {}).get("displayName"),
            "スコア": f"{home.get('score')} - {away.get('score')}" if is_done else "未消化",
            "状況": status.get("description"),
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("日時(JST)").reset_index(drop=True)
    return df


def get_leaders(league_name: str, category: str = "goalsLeaders") -> pd.DataFrame:
    slug = LEAGUES[league_name]
    url = f"https://site.web.api.espn.com/apis/site/v2/sports/soccer/{slug}/statistics"
    data = _get(url)
    stats = data.get("stats", [])
    target = next((c for c in stats if c.get("name") == category), None)
    if target is None:
        raise ValueError(f"カテゴリ '{category}' が見つかりません。利用可能: {[c.get('name') for c in stats]}")
    rows = []
    for i, leader in enumerate(target.get("leaders", []), start=1):
        athlete = leader.get("athlete", {})
        team = (leader.get("team", {}) or athlete.get("team", {}) or {}).get("displayName")
        rows.append({
            "順位": i, "選手": athlete.get("displayName"), "チーム": team,
            target.get("displayName", category): leader.get("value"),
            "詳細": leader.get("displayValue"),
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------
# データ取得: 試合詳細(スタメン・得点者/時刻・チームスタッツ)
# ---------------------------------------------------------

# 28項目あるチームスタッツのうち、表示する主要項目を選定
KEY_STAT_NAMES = [
    ("possessionPct", "支配率", "%"),
    ("totalShots", "シュート", ""),
    ("shotsOnTarget", "枠内シュート", ""),
    ("totalPasses", "パス数", ""),
    ("passPct", "パス成功率", ""),
    ("wonCorners", "コーナー", ""),
    ("foulsCommitted", "ファウル", ""),
    ("yellowCards", "警告", ""),
    ("redCards", "退場", ""),
]


def get_match_details(league_name: str, event_id: str, home_name: str, away_name: str) -> dict | None:
    slug = LEAGUES[league_name]
    url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/summary"
    try:
        data = _get(url, params={"event": event_id})
    except Exception as e:
        print(f"  [警告] 試合詳細の取得に失敗しました(event_id={event_id}): {e}")
        return None

    # --- スタメン(ホーム/アウェイをチーム名で突き合わせる) ---
    lineups = {"home": [], "away": []}
    for roster in data.get("rosters", []):
        team_name = roster.get("team", {}).get("displayName")
        starters = [
            p.get("athlete", {}).get("displayName")
            for p in roster.get("roster", [])
            if p.get("starter")
        ]
        if team_name == home_name:
            lineups["home"] = starters
        elif team_name == away_name:
            lineups["away"] = starters

    # --- 得点者・得点時刻 ---
    goals = []
    for ev in data.get("keyEvents", []):
        if not ev.get("scoringPlay"):
            continue
        participants = ev.get("participants", [])
        scorer = participants[0].get("athlete", {}).get("displayName") if participants else None
        team_name = ev.get("team", {}).get("displayName")
        goals.append({
            "minute": ev.get("clock", {}).get("displayValue"),
            "scorer": scorer,
            "team": "home" if team_name == home_name else "away",
            "own_goal": ev.get("type", {}).get("type") == "own-goal",
        })
    goals.sort(key=lambda g: g["minute"] or "")

    # --- チームスタッツ(主要項目のみ抽出、ホーム/アウェイをチーム名で突き合わせ) ---
    stats = {"home": {}, "away": {}}
    for team_box in data.get("boxscore", {}).get("teams", []):
        team_name = team_box.get("team", {}).get("displayName")
        side = "home" if team_name == home_name else ("away" if team_name == away_name else None)
        if side is None:
            continue
        stat_map = {s.get("name"): s.get("displayValue") for s in team_box.get("statistics", [])}
        for key, _, _ in KEY_STAT_NAMES:
            stats[side][key] = stat_map.get(key)

    return {
        "home_name": home_name, "away_name": away_name,
        "lineups": lineups, "goals": goals, "stats": stats,
    }


def get_all_match_details(league_name: str, fixtures_df: pd.DataFrame) -> dict:
    """終了済みの試合すべてについて詳細を取得し、event_idをキーにした辞書で返す"""
    details = {}
    completed = fixtures_df[fixtures_df["状況"] == "Full Time"]
    for _, row in completed.iterrows():
        detail = get_match_details(league_name, row["event_id"], row["ホーム"], row["アウェイ"])
        if detail is not None:
            details[row["event_id"]] = detail
    return details
# ---------------------------------------------------------

def _fixtures_to_date_dict(fixtures_df):
    result = {}
    for _, row in fixtures_df.iterrows():
        dt = _parse_dt(row["日時(JST)"])
        date_label = dt.strftime("%m/%d")
        result.setdefault(date_label, []).append({
            "event_id": row["event_id"],
            "home": row["ホーム"], "away": row["アウェイ"], "score": row["スコア"],
            "time": dt.strftime("%H:%M"), "done": row["状況"] == "Full Time",
        })
    return result


def _match_card_html(m):
    border = "#999999" if m["done"] else "#cccccc"
    cursor = "pointer" if m["done"] else "default"
    onclick = f' onclick="openMatchModal(\'{m["event_id"]}\')"' if m["done"] else ""
    if m["done"]:
        score_html = f'<div style="font-size:14px; font-weight:700; color:#111111; margin-top:4px;">{m["score"]}</div>'
    else:
        score_html = f'<div style="font-size:12px; font-weight:600; color:#666666; margin-top:4px;">{m["time"]}〜</div>'
    return f'''<div{onclick} style="border:1px solid {border}; border-radius:6px; padding:7px 9px; background:#ffffff; width:150px; cursor:{cursor};">
        <div style="font-size:13px; font-weight:600; color:#222222; line-height:1.4;">{m["home"]}</div>
        <div style="font-size:13px; font-weight:600; color:#222222; line-height:1.4;">{m["away"]}</div>
        {score_html}
    </div>'''


def build_timeline_html(nwsl_df, wsl_df):
    nwsl_by_date = _fixtures_to_date_dict(nwsl_df)
    wsl_by_date = _fixtures_to_date_dict(wsl_df)
    all_dates = []
    for df in (nwsl_df, wsl_df):
        for _, row in df.iterrows():
            label = _parse_dt(row["日時(JST)"]).strftime("%m/%d")
            if label not in all_dates:
                all_dates.append(label)
    all_dates = sorted(set(all_dates), key=lambda d: (int(d.split("/")[0]), int(d.split("/")[1])))
    COL_W = 150

    def date_header_html():
        cells = "".join(
            f'<div style="width:{COL_W}px; flex-shrink:0; font-size:13px; font-weight:700; '
            f'color:#111111; background:#e8e8e5; text-align:center; padding:5px 0 7px; '
            f'border-radius:4px 4px 0 0; border-bottom:2px solid #999;">{d}</div>' for d in all_dates)
        return f'<div style="display:flex; gap:8px; margin-left:70px; margin-top:14px;">{cells}</div>'

    def now_line_html():
        col_w, gap, label_w = COL_W, 8, 70
        today = datetime.now(JST)
        today_val = today.month * 100 + today.day
        idx = len(all_dates)
        for i, d in enumerate(all_dates):
            m, day = d.split("/")
            if int(m) * 100 + int(day) >= today_val:
                idx = i
                break
        line_left = label_w + idx * (col_w + gap) - gap / 2
        return f'''<div style="position:absolute; left:{line_left}px; top:0; bottom:0; width:2px; background:#d33333; z-index:1;"></div>
        <div style="position:absolute; left:{line_left}px; top:-22px; transform:translateX(-50%); font-size:11px; font-weight:700; color:#ffffff; background:#d33333; padding:2px 7px; border-radius:3px; white-space:nowrap; z-index:2;">現在</div>'''

    def league_row_html(label, by_date):
        style = LEAGUE_STYLE[label]
        cells = []
        for d in all_dates:
            matches = by_date.get(d)
            if matches:
                cards = "".join(_match_card_html(m) for m in matches)
                cells.append(f'<div style="display:flex; flex-direction:column; gap:6px; width:{COL_W}px; flex-shrink:0;">{cards}</div>')
            else:
                cells.append(f'<div style="width:{COL_W}px; flex-shrink:0;"></div>')
        return f'''<div style="display:flex; align-items:stretch; margin-top:14px; background:{style["band_bg"]}; border-radius:8px; padding:10px 10px 10px 0;">
            <div style="width:70px; flex-shrink:0; display:flex; align-items:flex-start; justify-content:center; padding-top:2px;">
                <span style="display:inline-block; font-size:13px; font-weight:700; color:#ffffff; background:{style["label_bg"]}; padding:4px 9px; border-radius:4px;">{label}</span>
            </div>
            <div style="display:flex; gap:8px;">{"".join(cells)}</div>
        </div>'''

    return f'''<div style="overflow-x:auto; overflow-y:visible; padding:24px 16px 16px; background:#ffffff; border:1px solid #ddd; border-radius:8px;">
        <div style="position:relative; display:inline-block; min-width:100%;">
          {now_line_html()}
          {date_header_html()}
          {league_row_html("NWSL", nwsl_by_date)}
          {league_row_html("WSL", wsl_by_date)}
        </div>
      </div>'''


STAT_LABELS = [(key, label) for key, label, _ in KEY_STAT_NAMES]


def build_match_modal_html(all_match_details: dict) -> str:
    """全リーグ分の試合詳細を1つのJSオブジェクトとして埋め込み、
    クリックでモーダル表示するためのHTML+JSを返す"""
    stat_labels_json = json.dumps(STAT_LABELS, ensure_ascii=False)
    details_json = json.dumps(all_match_details, ensure_ascii=False)

    return f'''
    <div id="match-modal-overlay" onclick="if(event.target===this) closeMatchModal()"
         style="display:none; position:fixed; inset:0; background:rgba(0,0,0,0.5); z-index:100; align-items:center; justify-content:center;">
      <div style="background:#ffffff; border-radius:10px; padding:20px; width:90%; max-width:520px; max-height:85vh; overflow-y:auto;">
        <div style="display:flex; justify-content:flex-end;">
          <button onclick="closeMatchModal()" style="border:none; background:none; font-size:18px; cursor:pointer; color:#888;">×</button>
        </div>
        <div id="match-modal-body"></div>
      </div>
    </div>

    <script>
      const MATCH_DETAILS = {details_json};
      const STAT_LABELS = {stat_labels_json};

      function openMatchModal(eventId) {{
        const d = MATCH_DETAILS[eventId];
        if (!d) return;

        const goalsHtml = d.goals.length ? d.goals.map(g => {{
          const side = g.team === 'home' ? d.home_name : d.away_name;
          const og = g.own_goal ? '(OG)' : '';
          return `<div style="font-size:13px; color:#222; padding:3px 0;">${{g.minute}} — ${{g.scorer}}${{og}} <span style="color:#888;">(${{side}})</span></div>`;
        }}).join('') : '<div style="font-size:13px; color:#888;">得点なし</div>';

        const lineupCol = (names) => names.length
          ? names.map(n => `<div style="font-size:12px; color:#222; padding:2px 0;">${{n}}</div>`).join('')
          : '<div style="font-size:12px; color:#888;">データなし</div>';

        const statsRows = STAT_LABELS.map(([key, label]) => {{
          const h = d.stats.home[key] ?? '-';
          const a = d.stats.away[key] ?? '-';
          return `<tr>
            <td style="font-size:13px; font-weight:600; color:#222; padding:4px 8px; text-align:right; width:35%;">${{h}}</td>
            <td style="font-size:12px; color:#888; padding:4px 8px; text-align:center; width:30%;">${{label}}</td>
            <td style="font-size:13px; font-weight:600; color:#222; padding:4px 8px; text-align:left; width:35%;">${{a}}</td>
          </tr>`;
        }}).join('');

        document.getElementById('match-modal-body').innerHTML = `
          <h2 style="font-size:16px; font-weight:700; color:#111; margin:0 0 14px;">${{d.home_name}} vs ${{d.away_name}}</h2>

          <div style="font-size:13px; font-weight:700; color:#111; margin-bottom:6px;">得点</div>
          <div style="margin-bottom:16px;">${{goalsHtml}}</div>

          <div style="font-size:13px; font-weight:700; color:#111; margin-bottom:6px;">スタメン</div>
          <div style="display:flex; gap:16px; margin-bottom:16px;">
            <div style="flex:1;"><div style="font-size:11px; color:#888; margin-bottom:4px;">${{d.home_name}}</div>${{lineupCol(d.lineups.home)}}</div>
            <div style="flex:1;"><div style="font-size:11px; color:#888; margin-bottom:4px;">${{d.away_name}}</div>${{lineupCol(d.lineups.away)}}</div>
          </div>

          <div style="font-size:13px; font-weight:700; color:#111; margin-bottom:6px;">スタッツ比較</div>
          <table style="width:100%; border-collapse:collapse;"><tbody>${{statsRows}}</tbody></table>
        `;

        document.getElementById('match-modal-overlay').style.display = 'flex';
      }}

      function closeMatchModal() {{
        document.getElementById('match-modal-overlay').style.display = 'none';
      }}
    </script>
    '''


# ---------------------------------------------------------
# HTML生成: 順位表 + ランキング
# ---------------------------------------------------------

def _fmt_value(v):
    """floatの整数値(例: 23.0)をintとして表示する(23.0 -> '23')。それ以外はそのまま文字列化。"""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if v is None:
        return ""
    return str(v)


def _standings_table_html(df):
    header_cells = "".join(
        f'<th style="font-size:12px; font-weight:700; color:#333; padding:6px 8px; '
        f'border-bottom:2px solid #ccc; background:#f0f0ee; white-space:nowrap;">{col}</th>' for col in df.columns)
    rows_html = []
    for _, row in df.iterrows():
        cells = "".join(
            f'<td style="font-size:13px; font-weight:600; color:#222; padding:5px 8px; border-bottom:1px solid #e5e5e2; white-space:nowrap;">{_fmt_value(row[col])}</td>' for col in df.columns)
        rows_html.append(f"<tr>{cells}</tr>")
    return f'''<table style="border-collapse:collapse; background:#fff; border-radius:6px; overflow:hidden; width:100%;">
        <thead><tr>{header_cells}</tr></thead><tbody>{"".join(rows_html)}</tbody></table>'''


def _rank_row_html(rank, name, team, value, unit):
    badge_bg = MEDAL_COLORS.get(rank, "#e5e5e2")
    badge_color = "#fff" if rank in MEDAL_COLORS else "#444"
    return f'''<div style="display:flex; align-items:center; gap:10px; padding:6px 10px; border-bottom:1px solid #eee;">
        <div style="width:22px; height:22px; border-radius:50%; background:{badge_bg}; color:{badge_color};
                    font-size:11px; font-weight:700; display:flex; align-items:center; justify-content:center; flex-shrink:0;">{rank}</div>
        <div style="flex:1; min-width:0;">
            <div style="font-size:13px; font-weight:600; color:#222; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">{name}</div>
            <div style="font-size:11px; font-weight:500; color:#888; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">{team}</div>
        </div>
        <div style="font-size:14px; font-weight:700; color:#222; flex-shrink:0;">{_fmt_value(value)}{unit}</div>
    </div>'''


def _leader_panel_html(title, leaders_df, value_col, unit):
    rows = [ _rank_row_html(i, row["選手"], row["チーム"], row[value_col], unit)
             for i, (_, row) in enumerate(leaders_df.iterrows(), start=1) ]
    return f'''<div style="flex:1; min-width:240px; background:#fff; border-radius:8px; overflow:hidden; border:1px solid #ddd;">
        <div style="font-size:13px; font-weight:700; color:#222; padding:8px 10px; background:#f0f0ee; border-bottom:1px solid #ccc;">{title}</div>
        {"".join(rows)}
    </div>'''


def _league_block_html(league_name, standings_df, goals_df, assists_df):
    style = LEAGUE_STYLE[league_name]
    return f'''<div style="background:{style["band_bg"]}; border-radius:10px; padding:16px; margin-bottom:24px;">
        <div style="display:inline-block; font-size:14px; font-weight:700; color:#fff; background:{style["label_bg"]};
                    padding:5px 12px; border-radius:4px; margin-bottom:14px;">{league_name}</div>
        <div style="margin-bottom:16px; overflow-x:auto;">{_standings_table_html(standings_df)}</div>
        <div style="display:flex; gap:14px; flex-wrap:wrap;">
            {_leader_panel_html("得点ランキング TOP10", goals_df, "Goals", "点")}
            {_leader_panel_html("アシストランキング TOP10", assists_df, "Assists", "本")}
        </div>
    </div>'''


def build_standings_leaders_html(data):
    return "".join(
        _league_block_html(league, d["standings"], d["goals"], d["assists"])
        for league, d in data.items()
    )


# ---------------------------------------------------------
# 更新要否の判定(状態はファイルのmtimeではなく、JSONの中身に保存する)
# ---------------------------------------------------------
#
# 重要: GitHub Actionsは毎回 actions/checkout でリポジトリを取得し直すため、
# チェックアウトされたファイルの mtime は「コミット日時」ではなく
# 「チェックアウトが行われた瞬間」になる。そのため mtime を使った経過時間の
# 判定はGitHub Actions上では機能しない。
# 代わりに、タイムスタンプを JSON ファイルの中身として明示的に保存し、
# それを読んで比較する。

STATE_FILE = Path("data/state.json")
# NWSL(米国)は西海岸の試合がJST正午〜午後にずれ込むため、特定の時刻を
# 「本日分は確定した」基準にはできない。代わりに、前回更新からの経過時間で
# 判定する(WSL/NWSL双方の時差の偏りに影響されないローリング方式)。
MAX_STALE_HOURS = 22            # 前回更新からこの時間を超えたら強制的に更新する
MATCH_DURATION_MINUTES = 110    # キックオフからこの分数経過したら「終了しているはず」とみなす


def _load_state() -> dict | None:
    if not STATE_FILE.exists():
        return None
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _save_state(fixtures_data: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    snapshot = {
        league: df.to_dict(orient="records")
        for league, df in fixtures_data.items()
    }
    state = {
        "last_full_update": datetime.now(JST).isoformat(),
        "fixtures_snapshot": snapshot,
    }
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _should_update(state: dict | None, now: datetime) -> tuple[bool, str]:
    # 条件C: 初回実行(状態ファイルがまだない)
    if state is None:
        return True, "初回実行のため更新します"

    last_update = datetime.fromisoformat(state["last_full_update"])

    # 条件B: 前回の更新からMAX_STALE_HOURSを超えて経過している(安全網)
    # ※ 特定の時刻(例:JST 7:00)に固定しないのは、NWSL(米国)の西海岸カードが
    #   JST正午〜午後にずれ込み、リーグごとに「確実に終わっている時刻」が
    #   大きく異なるため。経過時間ベースならこの偏りの影響を受けない。
    elapsed_hours = (now - last_update).total_seconds() / 3600
    if elapsed_hours >= MAX_STALE_HOURS:
        return True, f"前回更新から{elapsed_hours:.1f}時間経過(基準:{MAX_STALE_HOURS}時間)のため更新します"

    # 条件A: 前回の更新以降に「終了しているはず」の試合がある
    snapshot = state.get("fixtures_snapshot", {})
    for league, rows in snapshot.items():
        for row in rows:
            if row.get("状況") == "Full Time":
                continue  # 前回時点で既に終了済みの試合はスキップ対象
            try:
                kickoff = _parse_dt(row["日時(JST)"]).replace(tzinfo=JST)
            except (KeyError, ValueError):
                continue
            estimated_end = kickoff + pd.Timedelta(minutes=MATCH_DURATION_MINUTES)
            if last_update < estimated_end <= now:
                return True, f"{league}で終了しているはずの試合を検知したため更新します({row.get('ホーム')} vs {row.get('アウェイ')})"

    return False, "更新条件に該当しないためスキップします"


# ---------------------------------------------------------
# 実行: データ取得 → 1枚のindex.htmlに統合して出力
# ---------------------------------------------------------

def main():
    force = "--force" in sys.argv

    now = datetime.now(JST)
    state = _load_state()

    if force:
        print("--force が指定されたため、更新判定をスキップして強制的に更新します")
    else:
        should_update, reason = _should_update(state, now)
        print(reason)
        if not should_update:
            sys.exit(0)

    leaders_data = {}
    fixtures_data = {}

    for league in LEAGUES:
        leaders_data[league] = {
            "standings": get_standings(league),
            "goals": get_leaders(league, "goalsLeaders").head(10),
            "assists": get_leaders(league, "assistsLeaders").head(10),
        }
        fixtures_data[league] = get_fixtures(league)

    # 終了済み試合の詳細(スタメン・得点者/時刻・スタッツ)をまとめて取得
    all_match_details = {}
    for league in LEAGUES:
        print(f"{league}の試合詳細を取得中...")
        league_details = get_all_match_details(league, fixtures_data[league])
        all_match_details.update(league_details)

    updated_at = now.strftime("%Y-%m-%d %H:%M JST")

    html = f'''<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NWSL / WSL ダッシュボード</title>
</head>
<body style="margin:0; background:#f7f7f5;">
  <div style="font-family:-apple-system,'Hiragino Sans','Yu Gothic',sans-serif; background:#f7f7f5; color:#111; padding:24px; max-width:1100px; margin:0 auto;">
    <h1 style="font-size:20px; font-weight:700; color:#111; margin:0 0 4px;">NWSL / WSL ダッシュボード</h1>
    <p style="font-size:12px; font-weight:600; color:#888; margin:0 0 16px;">最終更新: {updated_at}</p>

    <div style="display:flex; gap:8px; margin-bottom:16px; border-bottom:2px solid #ddd;">
      <button id="tab-btn-standings" onclick="showTab('standings')"
              style="font-size:14px; font-weight:700; padding:8px 16px; border:none; background:none; cursor:pointer; color:#888; border-bottom:3px solid transparent; margin-bottom:-2px;">
        順位表・ランキング
      </button>
      <button id="tab-btn-schedule" onclick="showTab('schedule')"
              style="font-size:14px; font-weight:700; padding:8px 16px; border:none; background:none; cursor:pointer; color:#2f6fb3; border-bottom:3px solid #2f6fb3; margin-bottom:-2px;">
        日程・結果
      </button>
    </div>

    <div id="tab-standings" style="display:none;">
      {build_standings_leaders_html(leaders_data)}
    </div>

    <div id="tab-schedule">
      {build_timeline_html(fixtures_data["NWSL"], fixtures_data["WSL"])}
    </div>
  </div>

  {build_match_modal_html(all_match_details)}

  <script>
    function showTab(name) {{
      const tabs = {{ standings: document.getElementById('tab-standings'), schedule: document.getElementById('tab-schedule') }};
      const btns = {{ standings: document.getElementById('tab-btn-standings'), schedule: document.getElementById('tab-btn-schedule') }};
      for (const key in tabs) {{
        const active = key === name;
        tabs[key].style.display = active ? 'block' : 'none';
        btns[key].style.color = active ? '#2f6fb3' : '#888';
        btns[key].style.borderBottom = active ? '3px solid #2f6fb3' : '3px solid transparent';
      }}
    }}
  </script>
</body>
</html>'''

    os.makedirs("docs", exist_ok=True)
    with open("docs/index.html", "w", encoding="utf-8") as f:
        f.write(html)
    print("生成しました: docs/index.html")

    _save_state(fixtures_data)
    print(f"状態を保存しました: {STATE_FILE}")


if __name__ == "__main__":
    main()
