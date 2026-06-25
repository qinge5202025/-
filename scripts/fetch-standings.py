#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
⚽ 小组赛数据采集器
从 nowscore 的 JS 数据文件解析：
  - 小组积分榜 (standings)
  - 比赛赛果 (scores)
  - 对阵信息

用法:
  python scripts/fetch-standings.py           # 采集最新数据
  python scripts/fetch-standings.py --json    # 仅输出JSON
  python scripts/fetch-standings.py --update  # 同时更新TEAMS.md
  python scripts/fetch-standings.py --all     # 全流程
"""

import re
import json
import sys
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
PROJECT_DIR = SCRIPT_DIR.parent
DATA_DIR = PROJECT_DIR / "data"

# === 48 支参赛球队 ID->名称 映射 ===
TEAM_IDS = {
    640: "挪威", 641: "苏格兰", 644: "瑞典", 645: "比利时", 646: "荷兰",
    647: "奥地利", 648: "瑞士", 649: "法国", 650: "德国", 735: "埃及",
    744: "英格兰", 747: "捷克", 762: "土耳其", 765: "葡萄牙", 766: "阿根廷",
    767: "乌拉圭", 768: "克罗地亚", 772: "西班牙", 775: "哥伦比亚",
    776: "巴拉圭", 778: "巴西", 779: "厄瓜多尔", 782: "波黑",
    783: "伊朗", 790: "佛得角", 795: "加拿大", 797: "美国", 798: "巴拿马",
    803: "南非", 809: "科特迪瓦", 810: "加纳", 811: "刚果金",
    813: "摩洛哥", 815: "塞内加尔", 819: "墨西哥", 823: "突尼斯",
    874: "伊拉克", 875: "乌兹别克斯坦", 881: "约旦", 891: "沙特阿拉伯",
    898: "韩国", 903: "日本", 904: "卡塔尔", 909: "海地", 913: "澳大利亚",
    2363: "新西兰", 17976: "库拉索", 18406: "阿尔及利亚",
}

TEAM_EN = {
    640:"Norway", 641:"Scotland", 644:"Sweden", 645:"Belgium", 646:"Netherlands",
    647:"Austria", 648:"Switzerland", 649:"France", 650:"Germany", 735:"Egypt",
    744:"England", 747:"Czech Republic", 762:"Turkey", 765:"Portugal",
    766:"Argentina", 767:"Uruguay", 768:"Croatia", 772:"Spain", 775:"Colombia",
    776:"Paraguay", 778:"Brazil", 779:"Ecuador", 782:"Bosnia and Herzegovina",
    783:"Iran", 790:"Cape Verde", 795:"Canada", 797:"USA", 798:"Panama",
    803:"South Africa", 809:"Ivory Coast", 810:"Ghana", 811:"Democratic Rep Congo",
    813:"Morocco", 815:"Senegal", 819:"Mexico", 823:"Tunisia", 874:"Iraq",
    875:"Uzbekistan", 881:"Jordan", 891:"Saudi Arabia", 898:"South Korea",
    903:"Japan", 904:"Qatar", 909:"Haiti", 913:"Australia", 2363:"New Zealand",
    17976:"Curacao", 18406:"Algeria",
}

# === 工具函数 ===
def cw(s):
    return sum(2 if ord(c) > 127 else 1 for c in s)

def pad_to(s, target):
    return s + ' ' * max(0, target - cw(s))

def fetch_js_data():
    """从 nowscore 拉取赛事数据 JS 文件"""
    import urllib.request
    url = "https://info.nowscore.com/jsData/matchResult/2026/c75.js"
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read().decode('utf-8-sig', errors='replace')
    except Exception as e:
        print(f"Failed to fetch: {e}")
        return None


def parse_js_array(arr_text):
    """Parse a JS array string like [int, int, 'str', ...] into value list"""
    vals, cur = [], ''
    in_str = False
    for ch in arr_text:
        if ch == "'":
            in_str = not in_str
            if not in_str:
                vals.append(cur)
                cur = ''
            continue
        if ch == ',' and not in_str:
            cur = cur.strip()
            if cur:
                vals.append(cur)
            cur = ''
            continue
        if in_str or ch not in '[] ':
            cur += ch
    if cur.strip():
        vals.append(cur.strip())
    return vals


def extract_top_arrays(text):
    """Extract top-level [...] arrays from JS code"""
    arrays = []
    depth, cur = 0, ''
    for ch in text:
        if ch == '[':
            if depth == 0:
                cur = '['
            depth += 1
        elif ch == ']':
            depth -= 1
            cur += ch
            if depth == 0 and cur:
                arrays.append(cur)
                cur = ''
        elif depth > 0:
            cur += ch
    return arrays


def parse_standings(content):
    """Parse group standings"""
    result = {}
    for letter in 'ABCDEFGHIJKL':
        m = re.search(r'jh\["S27970' + letter + r'"\]\s*=\s*\[(.*?)\];', content, re.DOTALL)
        if not m:
            continue
        standings = []
        for entry in re.findall(r'\[(.*?)\]', m.group(1)):
            parts = [p.strip() for p in entry.split(',')]
            if len(parts) >= 11:
                try:
                    tid = int(parts[1])
                    if tid in TEAM_IDS:
                        standings.append({
                            'rank': int(parts[0]), 'team_id': tid,
                            'team_cn': TEAM_IDS[tid], 'team_en': TEAM_EN.get(tid, ''),
                            'mp': int(parts[2]), 'w': int(parts[3]), 'd': int(parts[4]),
                            'l': int(parts[5]), 'gf': int(parts[6]), 'ga': int(parts[7]),
                            'gd': int(parts[8]), 'pts': int(parts[9]),
                        })
                except ValueError:
                    continue
        result[letter] = standings
    return result


def parse_matches(content):
    """
    Parse match results from JS data.
    Array format: [matchId, cupId, round, 'date', homeId, awayId, 'score', 'half', ...]
    """
    result = {}
    for letter in 'ABCDEFGHIJKL':
        m = re.search(r'jh\["G27970' + letter + r'"\]\s*=\s*\[(.*?)\];', content, re.DOTALL)
        if not m:
            continue
        matches = []
        for arr in extract_top_arrays(m.group(1)):
            vals = parse_js_array(arr)
            if len(vals) >= 8:
                try:
                    vals[0] = vals[0]  # match_id (number as string)
                    rnd = int(vals[2])   # round: -1=小组第1轮, 0=第2轮
                    date = vals[3]
                    hid = int(vals[4])
                    aid = int(vals[5])
                    score = vals[6]
                    half = vals[7]
                    if hid in TEAM_IDS and aid in TEAM_IDS:
                        matches.append({
                            'match_id': int(vals[0]),
                            'date': date,
                            'home_team_cn': TEAM_IDS[hid],
                            'away_team_cn': TEAM_IDS[aid],
                            'home_id': hid, 'away_id': aid,
                            'full_time_score': score,
                            'half_time_score': half,
                            'round': rnd,
                        })
                except (ValueError, IndexError):
                    continue
        # Keep only group stage matches (round = -1 or 0)
        result[letter] = [ma for ma in matches if ma['round'] in (-1, 0)]
    return result


# === 打印函数 ===

def print_standings_table(standings):
    W = 88
    print(f"\n{'='*W}")
    print(f"  World Cup 2026 Group Standings (R1-2)")
    print(f"{'='*W}")
    for letter in 'ABCDEFGHIJKL':
        s = standings.get(letter, [])
        if not s:
            continue
        print(f"\n--- Group {letter} ---")
        print(f"  #  {'Team':<12s}  P  W  D  L  GF  GA  GD  Pts  Status")
        print(f"  {'-'*55}")
        for t in s:
            status = 'ADV' if t['rank'] <= 2 else ('FIGHT' if t['rank'] == 3 else 'DANGER')
            print(f"  {t['rank']:>2d}. {t['team_cn']:<10s}  {t['mp']:>2d}  {t['w']:>2d}  {t['d']:>2d}  {t['l']:>2d}  {t['gf']:>2d}  {t['ga']:>2d}  {t['gd']:+3d}  {t['pts']:>3d}  {status}")
    print()


def print_matches_table(matches):
    has_data = any(v for v in matches.values())
    if not has_data:
        return
    print(f"\n{'='*60}")
    print(f"  Match Results")
    print(f"{'='*60}")
    for letter in 'ABCDEFGHIJKL':
        mlist = matches.get(letter, [])
        if not mlist:
            continue
        played = [m for m in mlist if m['full_time_score'] and '-' in m['full_time_score']]
        if not played:
            continue
        print(f"\n--- Group {letter} ---")
        for m in played:
            d = m['date'][5:16] if m['date'] else 'TBD'
            s = m['full_time_score']
            hs = f" (HT {m['half_time_score']})" if m['half_time_score'] else ''
            print(f"  {d}  {m['home_team_cn']}  {s}  {m['away_team_cn']}{hs}")
    print()


# === Markdown 生成 ===

def gen_standings_md(standings):
    lines = ["## Group Standings\n"]
    for letter in 'ABCDEFGHIJKL':
        s = standings.get(letter, [])
        if not s:
            lines.append(f"### Group {letter}\nNo data yet\n")
            continue
        lines.append(f"### Group {letter}\n")
        lines.append("| # | Team | P | W | D | L | GF | GA | GD | Pts | Status |\n")
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|\n")
        for t in s:
            status = 'Advance' if t['rank'] <= 2 else ('Fight' if t['rank'] == 3 else 'Danger')
            lines.append(f"| {t['rank']} | {t['team_cn']} | {t['mp']} | {t['w']} | {t['d']} | {t['l']} | {t['gf']} | {t['ga']} | {t['gd']:+d} | **{t['pts']}** | {status} |\n")
        lines.append("\n")
    return ''.join(lines)


def gen_matches_md(matches):
    lines = ["## Full Match Results\n"]
    for letter in 'ABCDEFGHIJKL':
        mlist = matches.get(letter, [])
        if not mlist:
            continue
        lines.append(f"### Group {letter}\n")
        lines.append("| Date | Home | Score | Away | HT | Round |\n")
        lines.append("|---|---|---|---|---|---|\n")
        for m in mlist:
            d = m['date'][5:16] if m['date'] else 'TBD'
            s = m['full_time_score'] if m['full_time_score'] else 'vs'
            h = m['half_time_score'] if m['half_time_score'] else '-'
            r = 'R1' if m.get('round') == -1 else ('R2' if m.get('round') == 0 else 'R3')
            lines.append(f"| {d} | {m['home_team_cn']} | **{s}** | {m['away_team_cn']} | {h} | {r} |\n")
        lines.append("\n")
    return ''.join(lines)


def update_teams_md(standings_md, matches_md):
    tp = PROJECT_DIR / "TEAMS.md"
    if not tp.exists():
        return
    with open(tp, 'r', encoding='utf-8') as f:
        content = f.read()

    new_section = f"\n---\n{standings_md}\n{matches_md}\n"
    if "## Group Standings" in content:
        idx = content.index("## Group Standings")
        content = content[:idx] + new_section.strip()
    else:
        content += new_section

    with open(tp, 'w', encoding='utf-8') as f:
        f.write(content)
    print("TEAMS.md updated")


# === Main ===

def main():
    print("Fetching group stage data...")
    js = fetch_js_data()
    if not js:
        print("Using local cache...")
        cf = DATA_DIR / "group-standings.json"
        if cf.exists():
            with open(cf, 'r', encoding='utf-8') as f:
                data = json.load(f)
            print_standings_table(data.get('standings', {}))
            print_matches_table(data.get('matches', {}))
        else:
            print("No cache available")
        return

    print("Parsing standings...")
    standings = parse_standings(js)
    print(f"  {sum(len(v) for v in standings.values())} teams")

    print("Parsing match results...")
    matches = parse_matches(js)
    total = sum(len(v) for v in matches.values())
    played = sum(1 for v in matches.values() for m in v if m.get('full_time_score') and '-' in m.get('full_time_score', ''))
    print(f"  {total} matches ({played} played)")

    print_standings_table(standings)
    print_matches_table(matches)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        'tournament': '2026 FIFA World Cup',
        'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'standings': standings,
        'matches': matches,
    }
    with open(DATA_DIR / "group-standings.json", 'w', encoding='utf-8') as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
    print("Saved to group-standings.json")

    smd = gen_standings_md(standings)
    mmd = gen_matches_md(matches)

    if '--update' in sys.argv or '--all' in sys.argv:
        update_teams_md(smd, mmd)

    if '--json' in sys.argv:
        print(json.dumps(record, ensure_ascii=False, indent=2))

    print("Done")


if __name__ == "__main__":
    main()
