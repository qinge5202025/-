#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
⚽ 绿茵神算 · 综合赔率数据采集与分析引擎

数据源:
  1. 竞彩列表  → cp.nowscore.com          → 场次、让球、竞彩赔率
  2. 三合一     → live.nowscore.com/odds/match/  → 亚盘/欧盘/大小球(初盘+即时)
  3. 分析页     → live.nowscore.com/analysis/   → 球队数据

输出:
  - data/odds-latest.json    → 最新综合数据
  - data/odds-history.json   → 历史变动追踪
  - odds-analysis.md         → 结构化分析报告（Obsidian 可读）
  - 控制台: 每场比赛的亚盘/欧盘/大小球综合分析

用法:
  python scripts/fetch-odds.py              # 完整采集+分析
  python scripts/fetch-odds.py --json       # 仅输出JSON
"""

import re
import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path

# ======== 配置 ========
NOWSCORE_URL = "https://cp.nowscore.com/"
LIVE_BASE = "https://live.nowscore.com"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
SCRIPT_DIR = Path(__file__).parent.resolve()
PROJECT_DIR = SCRIPT_DIR.parent
DATA_DIR = PROJECT_DIR / "data"
ODDS_CHANGE_THRESHOLD = 0.05


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,*/*;q=0.9",
        "Accept-Language": "zh-CN,zh;q=0.9",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read().decode('utf-8', errors='replace')
    except Exception as e:
        return f"<error>{e}</error>"


def parse_match_list(html: str) -> list[dict]:
    """解析竞彩列表页"""
    matches = []
    pattern = r'(<tr[^>]*cansale="(?:true|false)"[^>]*gamename="世界杯"[^>]*>.*?</tr>)'
    for full_row in re.findall(pattern, html, re.DOTALL):
        m = {}
        # 排期ID（用于三合一等详情页，在 HomeTeam_XXXX 中）
        sid = re.search(r'id="HomeTeam_(\d+)"', full_row)
        if not sid:
            continue
        m['schedule_id'] = sid.group(1)
        m['match_id'] = sid.group(1)  # 兼容旧字段
        no = re.search(r'<td[^>]*><img[^>]*/>(\d+)</td>', full_row)
        if no: m['match_no'] = no.group(1)
        hcap = re.search(r'polygoal="([^"]+)"', full_row)
        if hcap: m['handicap_list'] = hcap.group(1)
        h = re.search(r'id="HomeTeam_\d+"[^>]*>([^<]+)', full_row)
        if h: m['home_team'] = h.group(1).strip()
        a = re.search(r'class="dz14"[^>]*id="GuestTeam_\d+"[^>]*>([^<]+)', full_row)
        if a: m['away_team'] = a.group(1).strip()
        odds = re.findall(r'id="sp_\d+_\d+"[^>]*>([\d.]+)</span>', full_row)
        if len(odds) >= 3:
            m['jingcai'] = {'home_win': float(odds[0]), 'draw': float(odds[1]), 'away_win': float(odds[2])}
        matches.append(m)
    return matches


def sh(html):
    """strip HTML tags"""
    return re.sub(r'<[^>]+>', '', html).strip()


def parse_3in1(html: str) -> dict:
    """解析三合一页面，提取亚盘/欧盘/大小球（初盘+即时）"""
    result = {'asian': {}, 'euro': {}, 'overunder': {}, 'companies': []}
    tbl = re.search(r'<TABLE[^>]*class=\'oddstablebox\'(.*?)</TABLE>', html, re.DOTALL)
    if not tbl:
        return result
    for row in re.findall(r'<TR[^>]*class=\'datatr\'>(.*?)</TR>', tbl.group(1), re.DOTALL):
        name_m = re.search(r"class='cpy'[^>]*>([^<]+)", row)
        if not name_m:
            continue
        tds = re.findall(r'<TD[^>]*>(.*?)</TD>', row, re.DOTALL)
        if len(tds) < 19:
            continue
        co = {'name': name_m.group(1).strip()}
        co['asian_initial'] = {'home': sh(tds[1]), 'handicap': sh(tds[2]), 'away': sh(tds[3])}
        co['asian_current'] = {'home': sh(tds[4]), 'handicap': sh(tds[5]), 'away': sh(tds[6])}
        co['euro_initial'] = {'home': sh(tds[7]), 'draw': sh(tds[8]), 'away': sh(tds[9])}
        co['euro_current'] = {'home': sh(tds[10]), 'draw': sh(tds[11]), 'away': sh(tds[12])}
        co['ou_initial'] = {'over': sh(tds[13]), 'line': sh(tds[14]), 'under': sh(tds[15])}
        co['ou_current'] = {'over': sh(tds[16]), 'line': sh(tds[17]), 'under': sh(tds[18])}
        result['companies'].append(co)

    for co in result['companies'][:8]:
        name = co['name']
        for kw in ['澳*', '36*', '皇G', '立B', '伟D', '威*']:
            if kw in name:
                for k in ['asian', 'euro', 'overunder']:
                    result[k] = {'initial': co[f'{k.replace("overunder","ou")}_initial'], 'current': co[f'{k.replace("overunder","ou")}_current'], 'company': name}
                break
        if result['asian']:
            break
    if not result['asian'] and result['companies']:
        c = result['companies'][0]
        for k in ['asian', 'euro', 'overunder']:
            result[k] = {'initial': c[f'{k.replace("overunder","ou")}_initial'], 'current': c[f'{k.replace("overunder","ou")}_current'], 'company': c['name']}
    return result


def conv_hcap(h: str) -> float:
    """让球文字→数值"""
    return {'平手': 0, '平/半': 0.25, '半球': 0.5, '半/一': 0.75, '一球': 1.0,
            '一/球半': 1.25, '球半': 1.5, '球半/两': 1.75, '两球': 2.0,
            '两/两半': 2.25, '两半': 2.5}.get(h, 0)


def hcap_desc(h: str, home: str, away: str) -> str:
    v = conv_hcap(h)
    if v == 0:
        return f"{home} 平手 {away}"
    return f"{home} 让{v:.2f} {away}" if v > 0 else f"{away} 让{abs(v):.2f} {home}"


def analyze_match(match: dict, d3: dict) -> dict:
    """综合亚盘/欧赔/大小球分析"""
    ar = {'asian_analysis': '', 'euro_analysis': '', 'ou_analysis': '', 'prediction_hints': [], 'overall_judgment': ''}
    home = match.get('home_team', '主队')
    away = match.get('away_team', '客队')
    jc = match.get('jingcai', {})
    hints = []

    # ---- 竞彩赔率 ----
    if jc:
        h, d, a = jc.get('home_win', 0), jc.get('draw', 0), jc.get('away_win', 0)
        if h < 1.5:
            hints.append(f"🔥 竞彩{home}胜赔{h:.2f}极低，庄家极度看好")
        elif a < 1.5:
            hints.append(f"🔥 竞彩{away}胜赔{a:.2f}极低，庄家极度看好")
        if d < 3.0 and d > 0:
            hints.append(f"🤝 竞彩平赔{d:.2f}偏低，需防范平局")
        if 8 < h / a < 0.125 or 8 < a / h < 0.125:
            hints.append("📊 赔率悬殊>8倍，强弱分明但警惕大热")

    # ---- 亚盘分析 ----
    asian = d3.get('asian', {})
    ac = asian.get('current', {})
    ai = asian.get('initial', {})
    if ac and ac.get('handicap'):
        hcap, ho, ao = ac['handicap'], ac.get('home', ''), ac.get('away', '')
        parts = [f"即时: {hcap_desc(hcap, home, away)} (主{ho}/客{ao})"]
        if ai.get('handicap') and ai['handicap'] != hcap:
            parts.append(f"初盘: {hcap_desc(ai['handicap'], home, away)}")
            iv = conv_hcap(ai['handicap'])
            cv = conv_hcap(hcap)
            if cv > iv:
                parts.append("📈升盘→看好让球方")
                hints.append("亚盘升盘，庄家对让球方信心增强")
            else:
                parts.append("📉降盘→让球方信心减弱")
                hints.append("亚盘降盘，让球方存疑")
        try:
            hof, aof = float(ho), float(ao)
            if hof < 0.85:
                parts.append(f"主队水位{hof}偏低→防范主队")
                hints.append(f"亚盘主队水位{hof}偏低，庄家防范主队打出")
            elif hof > 1.05:
                parts.append(f"主队水位{hof}偏高→阻盘")
            if aof < 0.85:
                parts.append(f"客队水位{aof}偏低→防范客队")
            elif aof > 1.05:
                parts.append(f"客队水位{aof}偏高→不看好客队")
        except:
            pass
        ar['asian_analysis'] = '; '.join(parts)

    # ---- 欧赔分析 ----
    euro = d3.get('euro', {})
    ec = euro.get('current', {})
    ei = euro.get('initial', {})
    if ec and ec.get('home'):
        parts = [f"即时: {ec['home']}/{ec.get('draw','')}/{ec['away']}"]
        if ei.get('home'):
            dh = float(ec['home']) - float(ei['home'])
            dd = float(ec.get('draw', 0)) - float(ei.get('draw', 0))
            da = float(ec['away']) - float(ei.get('away', 0))
            for label, delta in [('主胜', dh), ('平赔', dd), ('客胜', da)]:
                if abs(delta) >= ODDS_CHANGE_THRESHOLD:
                    parts.append(f"{label}{'↑' if delta>0 else '↓'}{delta:+.2f}")
        h, a = float(ec['home']), float(ec['away'])
        if h < a:
            hints.append(f"📊 欧赔热门: {home} (主胜{h:.2f})")
            if h < 1.5:
                hints.append(f"🔥 {home}胜赔<1.50，极度看好但需防过热")
        else:
            hints.append(f"📊 欧赔热门: {away} (客胜{a:.2f})")
            if a < 1.5:
                hints.append(f"🔥 {away}胜赔<1.50，极度看好但需防过热")
        ar['euro_analysis'] = '; '.join(parts)

    # ---- 大小球分析 ----
    ou = d3.get('overunder', {})
    oc = ou.get('current', {})
    if oc and oc.get('line'):
        parts = [f"即时: {oc['line']} (大{oc.get('over','')}/小{oc.get('under','')})"]
        oi = ou.get('initial', {})
        if oi.get('line') and oi['line'] != oc['line']:
            parts.append(f"初盘: {oi['line']}")
            try:
                nv = float(oc['line'].split('/')[0]) if '/' in oc['line'] else float(oc['line'])
                ov = float(oi['line'].split('/')[0]) if '/' in oi['line'] else float(oi['line'])
                if nv > ov:
                    parts.append("📈升盘→大球倾向")
                    hints.append("大小球升盘，庄家看好进球数多")
                else:
                    parts.append("📉降盘→小球倾向")
                    hints.append("大小球降盘，庄家看好进球数少")
            except:
                pass
        try:
            lv = float(oc['line'].split('/')[0]) if '/' in oc['line'] else float(oc['line'])
            if lv >= 3.0:
                hints.append(f"⚽ 大小球盘口{lv}偏大，预期进球较多")
            elif lv <= 2.0:
                hints.append(f"🛡️ 大小球盘口{lv}偏小，预期进球较少")
        except:
            pass
        ar['ou_analysis'] = '; '.join(parts)

    # ---- 综合 ----
    ar['prediction_hints'] = hints
    strong_agree = sum(1 for h in hints if '升盘' in h or '看好' in h or '防范' in h)
    if strong_agree >= 3:
        ar['overall_judgment'] = "✅ 多维度一致指向，判断可信度高"
    elif strong_agree >= 1:
        ar['overall_judgment'] = "📊 部分维度有指向，建议结合基本面"
    else:
        ar['overall_judgment'] = "⚠️ 各维度信号不一致/不明确，谨慎判断"
    return ar


def fetch_match_detail(match: dict) -> dict:
    """采集单场完整数据"""
    sid = match.get('schedule_id', match.get('match_id', ''))
    h3 = fetch(f"{LIVE_BASE}/odds/match/{sid}.htm")
    d3 = parse_3in1(h3) if not h3.startswith('<error>') else {}
    ar = analyze_match(match, d3)
    return {**match, 'odds_3in1': d3, 'analysis_result': ar}


def cw(s):
    """终端显示宽度"""
    return sum(2 if ord(c) > 127 else 1 for c in s)


def pad_to(s, target):
    """补空格到目标宽度"""
    return s + ' ' * max(0, target - cw(s))


def print_report(matches: list):
    """排版精美的赔率分析报告"""
    W = 94
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"\n╔{'═' * W}╗")
    print(f"║{pad_to('📊 世界杯赔率综合采集与分析', W-2)}║")
    print(f"║{pad_to(f'⏱ {now}  |  共 {len(matches)} 场', W-2)}║")
    print(f"╚{'═' * W}╝\n")
    
    for i, md in enumerate(matches):
        no = md.get('match_no','?')
        home, away = md.get('home_team','?'), md.get('away_team','?')
        jc = md.get('jingcai', {})
        ar = md.get('analysis_result', {})
        
        # 卡片头
        print(f"┌{'─' * W}┐")
        header = f"  {no}  {home} 🆚 {away}"
        print(f"│{pad_to(header, W-2)}│")
        print(f"├{'─' * W}┤")
        
        # 竞彩赔率
        if jc:
            odds_str = f"  竞彩  {jc.get('home_win',''):>5}  /  {jc.get('draw',''):>5}  /  {jc.get('away_win',''):>5}"
            print(f"│{pad_to(odds_str, W-2)}│")
        
        # 亚盘/欧赔/大小
        for label, key, icon in [('亚盘', 'asian_analysis', '📉'), ('欧赔', 'euro_analysis', '💰'), ('大小球', 'ou_analysis', '⚽')]:
            v = ar.get(key, '')
            if v:
                lines = v.split('; ')
                for ln_idx, ln in enumerate(lines):
                    prefix = f"  {icon} {label} " if ln_idx == 0 else "      "
                    print(f"│{pad_to(prefix + ln, W-2)}│")
        
        # 提示信息
        hints = ar.get('prediction_hints', [])
        if hints:
            for h in hints:
                print(f"│{pad_to('  ' + h, W-2)}│")
        
        # 综合判断
        judge = ar.get('overall_judgment', '')
        judge_icon = '✅' if '一致' in judge or '可信' in judge else ('📊' if '部分' in judge else '⚠️')
        print(f"│{pad_to(f'  {judge_icon}  {judge}', W-2)}│")
        print(f"└{'─' * W}┘\n")
    
    print(f"╔{'═' * W}╗")
    print(f"║{pad_to(f'✅ 采集完成  |  {len(matches)} 场世界杯比赛  |  {now}', W-2)}║")
    print(f"╚{'═' * W}╝\n")


def gen_markdown(matches: list) -> str:
    """生成 Obsidian 报告"""
    lines = [f"# ⚽ 世界杯赔率综合分析报告\n> 采集: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n---\n"]
    for md in matches:
        no, home, away = md.get('match_no','?'), md.get('home_team','?'), md.get('away_team','?')
        jc = md.get('jingcai', {})
        ar = md.get('analysis_result', {})
        lines.append(f"## {no}: {home} vs {away}\n")
        if jc:
            lines.append(f"- **竞彩**: {jc.get('home_win','')} / {jc.get('draw','')} / {jc.get('away_win','')}\n")
        for label, key in [('亚盘', 'asian_analysis'), ('欧赔', 'euro_analysis'), ('大小', 'ou_analysis')]:
            v = ar.get(key, '')
            if v:
                lines.append(f"### {label}\n> {v}\n")
        lines.append(f"### 综合判断\n**{ar.get('overall_judgment','')}**\n")
        hints = ar.get('prediction_hints', [])
        if hints:
            lines.append("#### 关键提示\n")
            for h in hints:
                lines.append(f"- {h}\n")
        lines.append("#### 预测框架\n```json\n" + json.dumps({
            "match": f"{home} vs {away}",
            "odds": {"home": jc.get('home_win'), "draw": jc.get('draw'), "away": jc.get('away_win')},
            "prediction": {"result": "", "score": "", "goals": "", "confidence": ""},
            "reasoning": hints,
        }, ensure_ascii=False, indent=2) + "\n```\n---\n")
    return ''.join(lines)


def main():
    print("⚽ 正在采集赔率数据...")
    html = fetch(NOWSCORE_URL)
    if html.startswith('<error>'):
        print(f"❌ 无法访问")
        sys.exit(1)
    mlist = parse_match_list(html)
    if not mlist:
        print("❌ 未找到世界杯比赛")
        sys.exit(1)
    print(f"📋 发现 {len(mlist)} 场\n")

    details = []
    for i, m in enumerate(mlist):
        no, home, away = m.get('match_no','?'), m.get('home_team','?'), m.get('away_team','?')
        print(f"  [{i+1}/{len(mlist)}] {no} {home} vs {away}...", end=' ', flush=True)
        details.append(fetch_match_detail(m))
        print("✅")

    print_report(details)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    record = {"fetch_time": datetime.now().strftime('%Y-%m-%d %H:%M:%S'), "matches": details}
    with open(DATA_DIR / "odds-latest.json", 'w', encoding='utf-8') as f:
        json.dump(record, f, ensure_ascii=False, indent=2)

    hist = []
    hf = DATA_DIR / "odds-history.json"
    if hf.exists():
        try:
            hist = json.load(open(hf, 'r', encoding='utf-8'))
        except:
            pass
    hist.append(record)
    json.dump(hist[-30:], open(hf, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    print(f"💾 已保存")

    with open(PROJECT_DIR / "odds-analysis.md", 'w', encoding='utf-8') as f:
        f.write(gen_markdown(details))
    print(f"📊 报告: {PROJECT_DIR / 'odds-analysis.md'}")

    if '--json' in sys.argv:
        print(json.dumps(details, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
