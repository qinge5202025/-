#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
⚽ 芬超联赛 赔率+数据综合预测引擎

数据源:
  1. 竞彩列表页  →  match list + odds
  2. 三合一页面   → 亚盘/欧赔/大小球初盘+即时
  3. 分析页面     → 联赛盘路、赛季统计、近期战绩、积分榜

因子模型:
  - odds_factor: 欧赔隐含概率 + 赔率变动
  - asian_factor: 亚盘盘口 + 水位 + 盘路走势
  - form_factor: 近10场战绩 + 连胜/连败
  - ou_factor: 大小球盘口 + 大球率
  - league_factor: 联赛排名 + 积分差距
  - h2h_factor: 历史交锋

输出: 每一场的完整预测（赛果、比分、大小球、半全场）
"""

import re
import json
import sys
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
PROJECT_DIR = SCRIPT_DIR.parent
DATA_DIR = PROJECT_DIR / "data"

NOWSCORE_URL = "https://cp.nowscore.com/"
LIVE_BASE = "https://live.nowscore.com"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

ODDS_CHANGE_THRESHOLD = 0.05

# ===== 芬超Elo评分系统（基于联赛排名+历史实力）=====
FINNISH_ELO = {
    # 传统豪门
    '赫尔辛基': 1650, '国际图尔': 1580, '塞伊奈': 1560,
    # 中上游
    '库奥皮奥': 1570, '埃尔维斯': 1550, '坦山猫': 1550,
    '拉赫蒂': 1530, '瓦萨': 1520, 'VPS瓦萨': 1520,
    '玛丽港': 1500, 'AC奥卢': 1500, 'AC奥卢': 1510,
    # 中下游
    '赫尔火花': 1480, '雅罗': 1470, 'TPS图尔库': 1460,
    'TPS图尔': 1460, '科特卡': 1450, '埃尔维斯': 1480,
    # 别名映射
    '坦山猫': 1550,  # 与埃尔维斯同一球队
    'TPS图尔': 1460,  # TPS图尔库简称
}

TEAM_ALIASES_FIN = {
    '坦山猫': '埃尔维斯',
    'VPS瓦萨': '瓦萨',
    'TPS图尔': 'TPS图尔库',
    'AC奥卢': 'AC奥卢',
}

def get_elo_fin(team_name):
    name = TEAM_ALIASES_FIN.get(team_name, team_name)
    return FINNISH_ELO.get(name, 1500)

def expected_score_fin(elo_a, elo_b):
    return 1.0 / (1.0 + 10.0 ** ((elo_b - elo_a) / 400.0))

def calc_elo_factor(home_team, away_team):
    elo_h = get_elo_fin(home_team)
    elo_a = get_elo_fin(away_team)
    exp_h = expected_score_fin(elo_h, elo_a)
    score = {'home': 0, 'draw': 0, 'away': 0}
    if exp_h > 0.6:
        score['home'] = exp_h - 0.3
    elif exp_h < 0.4:
        score['away'] = 0.7 - exp_h
    else:
        score['draw'] = 0.05
    return score, elo_h, elo_a, exp_h

# ===== 压力系数 =====
def calc_pressure_factor(home_elo, away_elo):
    """
    芬超联赛中，弱队背水一战时易崩盘；
    当Elo差>80且低Elo方战意过强时可能适得其反
    """
    score = {'home': 0, 'draw': 0, 'away': 0}
    elo_diff = home_elo - away_elo
    # 主队明显强于客队但客队近期状态好 → 防冷
    if elo_diff > 80:
        score['away'] += 0.03
    elif elo_diff < -80:
        score['home'] += 0.03
    return score

# 默认因子权重（加入Elo，降低变动因子权重）
PREDICTION_WEIGHTS = {
    'odds_composite': 0.35,   # 赔率综合信号（隐含概率+变动方向+一致性验证）
    'hcap_odds': 0.15,        # 让球盘赔率（新增！模型之前完全没使用）
    'asian_handicap': 0.10,   # 亚盘文字描述（降权，因为hcap_odds更精确）
    'form_factor': 0.10,
    'league_factor': 0.05,
    'elo': 0.15,
    'over_under': 0.10,
}


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


def sh(s):
    """strip html tags"""
    return re.sub(r'<[^>]+>', '', s).strip()


# ============================================================
# 第一步: 解析竞彩列表页，获取所有非世界杯比赛
# ============================================================
def parse_all_matches(html: str, league: str = "芬超") -> list[dict]:
    """从竞彩列表页解指定联赛的比赛"""
    matches = []
    pattern = r'(<tr[^>]*cansale="true"[^>]*gamename="' + league + r'"[^>]*>.*?</tr>)'
    for full_row in re.findall(pattern, html, re.DOTALL):
        m = {}
        sid = re.search(r'id="HomeTeam_(\d+)"', full_row)
        if not sid:
            continue
        m['schedule_id'] = sid.group(1)
        m['match_id'] = sid.group(1)
        
        # Match number (竞彩编号)
        no = re.search(r'<td[^>]*><img[^>]*/>(\d+)</td>', full_row)
        if no:
            m['match_no'] = f"{league[:1]}{no.group(1)}"
        
        # Handicap
        hcap = re.search(r'polygoal="([^"]+)"', full_row)
        if hcap:
            m['polygoal'] = hcap.group(1)
        
        # 让球值（竞彩让球）
        hcap_val = re.search(r'<td[^>]*><b><font[^>]*>([+-]?\d+)</font></b></td>', full_row)
        if hcap_val:
            m['jingcai_handicap'] = int(hcap_val.group(1))
        
        # Team names
        h = re.search(r'id="HomeTeam_\d+"[^>]*>([^<]+)', full_row)
        if h:
            m['home_team'] = h.group(1).strip()
        a = re.search(r'class="dz14"[^>]*id="GuestTeam_\d+"[^>]*>([^<]+)', full_row)
        if a:
            m['away_team'] = a.group(1).strip()
        
        # 竞彩赔率
        odds = re.findall(r'id="sp_\d+_\d+"[^>]*>([\d.]+)</span>', full_row)
        if len(odds) >= 3:
            m['jingcai'] = {'home_win': float(odds[0]), 'draw': float(odds[1]), 'away_win': float(odds[2])}
        
        # 开赛时间
        time_m = re.search(r'开赛时间[：:]([^"<]+)', full_row)
        if time_m:
            m['match_time'] = time_m.group(1).strip()
        
        matches.append(m)
    return matches


# ============================================================
# 第二步: 解析三合一页面（亚盘/欧赔/大小球）
# ============================================================
def parse_3in1(html: str) -> dict:
    """解析三合一页面，返回多家公司的亚盘/欧赔/大小球"""
    result = {'asian': {}, 'euro': {}, 'overunder': {}, 'all_companies': []}
    
    # Find all company rows
    rows = re.findall(r'<TR[^>]*class=[\'"]datatr[\'"][^>]*>(.*?)</TR>', html, re.DOTALL)
    if not rows:
        rows = re.findall(r'<TR[^>]*class=\\\'datatr\\\'[^>]*>(.*?)</TR>', html, re.DOTALL)
    
    for row in rows:
        name_m = re.search(r'class="[^"]*cpy"[^>]*>([^<]+)', row)
        if not name_m:
            name_m = re.search(r"class='[^']*cpy'[^>]*>([^<]+)", row)
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
        result['all_companies'].append(co)
    
    # Pick the best reference company (first major one found)
    for co in result['all_companies'][:8]:
        name = co['name']
        if any(kw in name for kw in ['36*', 'Bet365', '伟D', '立B', '澳*', '皇G', '威*']):
            for k in ['asian', 'euro', 'overunder']:
                key = {'asian': 'asian', 'euro': 'euro', 'overunder': 'ou'}[k]
                result[k] = {
                    'initial': co[f'{key}_initial'],
                    'current': co[f'{key}_current'],
                    'company': name
                }
            break
    
    if not result['asian'] and result['all_companies']:
        c = result['all_companies'][0]
        for k in ['asian', 'euro', 'overunder']:
            key = {'asian': 'asian', 'euro': 'euro', 'overunder': 'ou'}[k]
            result[k] = {
                'initial': c[f'{key}_initial'],
                'current': c[f'{key}_current'],
                'company': c['name']
            }
    
    return result


# ============================================================
# 第三步: 解析分析页面（球队数据）
# ============================================================
def parse_analysis(html: str, home_team: str, away_team: str) -> dict:
    """解析赛前分析页面，提取各种数据"""
    result = {
        'form': {},          # 近期状态
        'league_table': {},  # 联赛积分榜
        'stats': {},         # 赛季数据统计
        'handicap_stats': {},  # 盘路走势
        'media_analysis': [],  # 媒体分析
        'h2h': [],           # 历史交锋
    }
    
    # --- 1. 赛季数据统计 (Table 15) ---
    stats_tbl = re.search(r'<table class="ComTable"[^>]*id="porlet_15"[^>]*>(.*?)</table>', html, re.DOTALL)
    if stats_tbl:
        stats_text = re.sub(r'<[^>]+>', '', stats_tbl.group(1))
        result['stats']['raw'] = stats_text.strip()
        
        # Parse home team stats
        home_stats = {}
        h_pat = re.findall(r'主队战绩统计.*?胜\s*(\d+)[%\s]\[?(\d+)?', stats_text)
        if h_pat:
            home_stats['win_pct'] = h_pat[0][0]
        
        # Goals
        hg = re.search(r'主队.*?场均进球[：:]\s*([\d.]+)', stats_text)
        if hg:
            home_stats['avg_goals_for'] = float(hg.group(1))
        hga = re.search(r'主队.*?场均失球[：:]\s*([\d.]+)', stats_text)
        if hga:
            home_stats['avg_goals_against'] = float(hga.group(1))
        
        # Same for away
        away_stats = {}
        ag = re.search(r'客队.*?场均进球[：:]\s*([\d.]+)', stats_text)
        if ag:
            away_stats['avg_goals_for'] = float(ag.group(1))
        aga = re.search(r'客队.*?场均失球[：:]\s*([\d.]+)', stats_text)
        if aga:
            away_stats['avg_goals_against'] = float(aga.group(1))
        
        result['stats']['home'] = home_stats
        result['stats']['away'] = away_stats
    
    # --- 2. 联赛盘路走势 (Table 6) ---
    handicap_tbl = re.search(r'<table class="ComTable"[^>]*id="porlet_6"[^>]*>(.*?)</table>', html, re.DOTALL)
    if handicap_tbl:
        hcap_text = re.sub(r'<[^>]+>', '', handicap_tbl.group(1))
        result['handicap_stats']['raw'] = hcap_text.strip()
        
        # Parse win/cover/over rates for each team
        # Format: 总 11 赢4 走1 输6 让胜率36.4% 大球率45.5% 小球率54.5%
        teams_data = {}
        for team_name in [home_team, away_team]:
            idx = hcap_text.find(team_name)
            if idx >= 0:
                section = hcap_text[idx:idx+300]
                wl = re.search(r'赢(\d+)走(\d+)输(\d+)', section)
                if wl:
                    teams_data[team_name] = {
                        'wins': wl.group(1), 'draws': wl.group(2), 'losses': wl.group(3)
                    }
                win_rate = re.search(r'让胜率([\d.]+)%', section)
                if win_rate:
                    if team_name not in teams_data:
                        teams_data[team_name] = {}
                    teams_data[team_name]['cover_rate'] = float(win_rate.group(1))
                over_rate = re.search(r'大球率([\d.]+)%', section)
                if over_rate:
                    if team_name not in teams_data:
                        teams_data[team_name] = {}
                    teams_data[team_name]['over_rate'] = float(over_rate.group(1))
        
        result['handicap_stats']['teams'] = teams_data
    
    # --- 3. 媒体分析 (Table 12) ---
    media_tbl = re.search(r'<table class="ComTable"[^>]*id="porlet_12"[^>]*>(.*?)</table>', html, re.DOTALL)
    if media_tbl:
        media_text = re.sub(r'<[^>]+>', '', media_tbl.group(1))
        
        # Recent form patterns
        form_patterns = re.findall(r'近况走势[-\s]*([LWD\d\s]+)', media_text)
        if form_patterns:
            teams_form = form_patterns[:2]
            result['media_analysis'].append(f"近况走势: {teams_form}")
        
        # 盘路
        panlu = re.findall(r'盘路[-\s]*([LWD\s]+)', media_text)
        if panlu:
            result['media_analysis'].append(f"盘路: {panlu[:2]}")
        
        # 信心指数
        confidence = re.search(r'信心指数[-\s]*([^<>\s]+)', media_text)
        if confidence:
            result['media_analysis'].append(f"信心指数: {confidence.group(1)}")
        
        # 对赛成绩
        h2h = re.search(r'对赛成绩[-\s]*([^<>\s]+)', media_text)
        if h2h:
            result['media_analysis'].append(f"对赛成绩: {h2h.group(1)}")
    
    # --- 4. 联赛积分榜 (Table 14) ---
    league_tbl = re.search(r'<table class="ComTable"[^>]*id="porlet_14"[^>]*>(.*?)</table>', html, re.DOTALL)
    if league_tbl:
        league_text = re.sub(r'<[^>]+>', '', league_tbl.group(1))
        result['league_table']['raw'] = league_text.strip()
        
        # Try to find team rankings
        for team_name in [home_team, away_team]:
            idx = league_text.find(team_name)
            if idx >= 0:
                # The ranking should be before the team name
                before = league_text[max(0,idx-50):idx]
                ranks = re.findall(r'(\d+)', before)
                if ranks:
                    if team_name == home_team:
                        result['league_table']['home_rank'] = int(ranks[-1])
                    else:
                        result['league_table']['away_rank'] = int(ranks[-1])
    
    return result


# ============================================================
# 第四步: 因子计算
# ============================================================
def calc_hcap_value(h: str) -> float:
    """让球文字→数值, 正=主让, 负=客让"""
    mapping = {
        '平手': 0, '平/半': 0.25, '半球': 0.5, '半/一': 0.75,
        '一球': 1.0, '一/球半': 1.25, '球半': 1.5, '球半/两': 1.75,
        '两球': 2.0, '两/两半': 2.25, '两半': 2.5,
        '受平/半': -0.25, '受半球': -0.5, '受半/一': -0.75,
        '受一球': -1.0, '受一/球半': -1.25, '受球半': -1.5,
        '受球半/两': -1.75, '受两球': -2.0, '受两/两半': -2.25,
    }
    return mapping.get(h, 0)


def calc_hcap_odds_factor(handicap, hcap_odds_str):
    """
    让球盘赔率因子（v6 — 使用竞彩让球胜平负赔率）
    
    竞彩让球赔率(1.78/3.53/4.31) 反映让球后的胜平负概率：
    - 如果让球后主胜赔率低(<2.0) → 主队能赢下让球盘
    - 如果让球后客胜赔率低(<2.0) → 客队能赢下让球盘
    
    标准胜平负的让球盘(3.16/3.30/1.96)则相反：
    - 让球客胜1.96 = 客队赢盘概率最高
    """
    score = {'home': 0, 'draw': 0, 'away': 0}
    if not hcap_odds_str:
        return score
    
    try:
        parts = hcap_odds_str.split('/')
        if len(parts) < 3:
            return score
        ho, hd, ha = float(parts[0]), float(parts[1]), float(parts[2])
    except (ValueError, IndexError):
        return score
    
    if ho <= 0 or ha <= 0:
        return score
    
    # 区分两种赔率类型:
    # 类型A: 竞彩让球赔率(1.78/3.53/4.31) - 赔率范围1.5~5.0
    # 类型B: AH标准盘(3.16/3.30/1.96) - 某个方向偏低
    
    min_val = min(ho, hd, ha)
    is_ah_style = min_val < 2.2 and max(ho, hd, ha) > 2.8
    
    if is_ah_style:
        # AH标准盘: 最低赔率的方向 = 赢盘概率最高的方向
        # 拉赫蒂(-1) AH客胜1.96 → 客队赢盘(平局或客胜)
        if ha == min_val:  # AH客胜最低
            strength = max(0, (2.5 - ha) / 2.5)
            if handicap and handicap < 0:  # 主让球+客赢盘 = 客不败
                score['away'] += strength * 0.5
                score['draw'] += strength * 0.3
            else:
                score['away'] += strength * 0.4
        elif ho == min_val:  # AH主胜最低
            strength = max(0, (2.5 - ho) / 2.5)
            if handicap and handicap > 0:  # 主受让+主赢盘 = 主不败
                score['home'] += strength * 0.5
                score['draw'] += strength * 0.3
            else:
                score['home'] += strength * 0.4
        else:  # AH平最低 = 1球差距
            strength = max(0, (3.0 - hd) / 3.0)
            score['draw'] += strength * 0.4
            if handicap and handicap < 0:
                score['away'] += 0.03
            elif handicap and handicap > 0:
                score['home'] += 0.03
    else:
        # 竞彩让球赔率: 赔率越低代表该方向在让球后最可能
        # 如拉赫蒂(-1): 1.78(主)/3.53(平)/4.31(客)
        # 让球主胜1.78 = 主队赢2球+(覆盖让球)
        # 让球平3.53 = 主队赢1球
        # 让球客胜4.31 = 客队不败(不覆盖让球)
        ho_prob = 1.0 / ho
        hd_prob = 1.0 / hd
        ha_prob = 1.0 / ha
        total = ho_prob + hd_prob + ha_prob
        
        # 让球后主胜概率高 → 主队能覆盖让球(赢2+) → 真实主胜
        if ho_prob / total > 0.45:
            score['home'] += (ho_prob/total - 0.40) * 0.5
        
        # 让球后客胜概率高 → 客队覆盖让球(客不败或赢) → 真实客胜/平
        if ha_prob / total > 0.40:
            score['away'] += (ha_prob/total - 0.35) * 0.4
            score['draw'] += (ha_prob/total - 0.35) * 0.2
        
        # 让球后平局概率高 → 1球差距
        if hd_prob / total > 0.35:
            score['draw'] += (hd_prob/total - 0.30) * 0.4
    
    return score


def calc_odds_composite(euro: dict, jingcai: dict = None) -> dict:
    """
    赔率综合因子（v6 — 基于隐含概率变化）
    
    核心改进: 从"赔率数值变动"改为"隐含概率变动"
    - 赔率从1.66→1.78: 隐含概率从60.2%→56.2%, 下降4个百分点
    - 赔率从1.38→1.58: 隐含概率从72.5%→63.3%, 下降9.2个百分点
    - 这样变动信号与隐含概率在同一量纲, 可以直接比较
    """
    ec = euro.get('current', {})
    ei = euro.get('initial', {})
    
    try:
        h_cur, d_cur, a_cur = float(ec.get('home',0)), float(ec.get('draw',0)), float(ec.get('away',0))
        h_ini = float(ei.get('home',0)) if ei.get('home') else 0
        d_ini = float(ei.get('draw',0)) if ei.get('draw') else 0
        a_ini = float(ei.get('away',0)) if ei.get('away') else 0
    except (ValueError, TypeError):
        return {'home': 0, 'draw': 0, 'away': 0}
    
    if h_cur <= 0 or a_cur <= 0:
        return {'home': 0, 'draw': 0, 'away': 0}
    
    # 当前隐含概率（原始未归一化）
    # 使用原始概率(不含庄家抽水)来比较变化
    raw_h = 1.0 / h_cur
    raw_d = 1.0 / d_cur
    raw_a = 1.0 / a_cur
    
    # 归一化版本（用于最终输出）
    r_total = raw_h + raw_d + raw_a
    imp_h = raw_h / r_total
    imp_d = raw_d / r_total
    imp_a = raw_a / r_total
    
    # 初始赔率不存在时, 直接用当前概率
    if not (h_ini > 0 and a_ini > 0):
        return {'home': imp_h, 'draw': imp_d, 'away': imp_a}
    
    # 初始隐含概率（原始未归一化）
    raw_h_i = 1.0 / h_ini
    raw_d_i = 1.0 / d_ini
    raw_a_i = 1.0 / a_ini
    
    # 使用原始概率变化（不含归一化压缩！）
    delta_h = raw_h - raw_h_i  # 负=概率下降=不看好
    delta_d = raw_d - raw_d_i
    delta_a = raw_a - raw_a_i
    
    # 变动强度: 原始概率变化绝对值 >2% 才算有效信号
    has_movement = any(abs(d) > 0.02 for d in [delta_h, delta_d, delta_a])
    
    # 判断赔率变动支持的方向
    # delta > 0 意味着该方向的隐含概率上升 = 更被看好
    move_direction = None
    if delta_h > 0.02 and delta_h > delta_a:
        move_direction = 'home'
    elif delta_a > 0.02 and delta_a > delta_h:
        move_direction = 'away'
    elif delta_d > 0.02:
        move_direction = 'draw'
    
    # 当前赔率支持的方向
    cur_direction = 'home' if imp_h > imp_a and imp_h > imp_d else ('away' if imp_a > imp_h and imp_a > imp_d else 'draw')
    
    consistent = (cur_direction == move_direction) if move_direction else True
    
    # === 综合评分 ===
    score = {'home': imp_h, 'draw': imp_d, 'away': imp_a}
    
    if has_movement and move_direction:
        # 变动幅度
        move_strength = max(abs(delta_h), abs(delta_d), abs(delta_a))
        
        if consistent:
            # 一致: 增强
            boost = min(move_strength * 2.0, 0.15)
            if cur_direction == 'home':
                score['home'] = min(imp_h + boost, 0.85)
                score['draw'] *= 0.9
                score['away'] *= 0.9
            elif cur_direction == 'away':
                score['away'] = min(imp_a + boost, 0.85)
                score['home'] *= 0.9
                score['draw'] *= 0.9
            else:
                score['draw'] = min(imp_d + boost, 0.7)
                score['home'] *= 0.9
                score['away'] *= 0.9
        else:
            # 矛盾: 变动信号权重 = 变动幅度 × 2, 直接覆盖概率
            # 例如: 主队概率降4pp → 主胜得分减8pp, 客队得分加8pp
            adjustment = move_strength * 2.0  # 最高约0.18
            
            if delta_h < -0.02:  # 主胜概率下降
                penalty = min(abs(delta_h) * 2.0, 0.25)
                score['home'] = max(0.05, imp_h - penalty)
                # 将扣分按6:4分配给客队和平局
                away_share = penalty * 0.6
                draw_share = penalty * 0.4
                # 但客队和平局的概率不能同时都加, 优先加到变动支持的方向
                if delta_a > 0:  # 客胜概率上升
                    score['away'] = min(imp_a + away_share, 0.7)
                else:
                    score['away'] += away_share * 0.5
                if delta_d > 0:
                    score['draw'] = min(imp_d + draw_share, 0.5)
                else:
                    score['draw'] += draw_share * 0.5
            
            if delta_a < -0.02:  # 客胜概率下降
                penalty = min(abs(delta_a) * 2.0, 0.25)
                score['away'] = max(0.05, imp_a - penalty)
                if delta_h > 0:
                    score['home'] = min(imp_h + penalty * 0.6, 0.7)
                else:
                    score['home'] += penalty * 0.4
                score['draw'] += penalty * 0.3
            
            if delta_d < -0.02:  # 平局概率下降
                score['draw'] = max(0.05, imp_d - min(abs(delta_d) * 2.0, 0.2))
            elif delta_d > 0.02:  # 平局概率上升(防平)
                score['draw'] = min(imp_d + min(delta_d * 2.0, 0.15), 0.5)
    
    # 归一化
    s_total = sum(score.values())
    if s_total > 0:
        for k in score:
            score[k] = round(score[k] / s_total, 4)
    
    return score


def calc_asian_factor(asian: dict, handicap_stats: dict, home_team: str, away_team: str) -> dict:
    """
    亚盘因子: 盘口深度 + 水位 + 盘路走势
    """
    ac = asian.get('current', {})
    ai = asian.get('initial', {})
    
    try:
        hcap_val = calc_hcap_value(ac.get('handicap', ''))
        home_water = float(ac.get('home', 0))
        away_water = float(ac.get('away', 0))
    except (ValueError, TypeError):
        return {'home': 0, 'draw': 0, 'away': 0}
    
    if home_water <= 0 or away_water <= 0:
        return {'home': 0, 'draw': 0, 'away': 0}
    
    score = {'home': 0, 'draw': 0, 'away': 0}
    
    # 盘口深度信号
    if hcap_val > 0:
        score['home'] += hcap_val * 0.10
    elif hcap_val < 0:
        score['away'] += abs(hcap_val) * 0.10
    
    # 水位信号 (低水=防范)
    if home_water < 0.85 and hcap_val > 0:
        score['home'] += 0.05
    elif away_water < 0.85 and hcap_val < 0:
        score['away'] += 0.05
    elif home_water > 1.05 and hcap_val > 0:
        score['home'] -= 0.03
    elif away_water > 1.05 and hcap_val < 0:
        score['away'] -= 0.03
    
    # 升盘/降盘
    if ai.get('handicap'):
        old_val = calc_hcap_value(ai['handicap'])
        if hcap_val > old_val:
            score['home'] += 0.08
        elif hcap_val < old_val:
            score['away'] += 0.08
    
    # 盘路走势（让胜率）
    teams_data = handicap_stats.get('teams', {})
    if home_team in teams_data:
        cr = teams_data[home_team].get('cover_rate', 50)
        if cr > 55:
            score['home'] += 0.05
        elif cr < 40:
            score['home'] -= 0.05
    
    if away_team in teams_data:
        cr = teams_data[away_team].get('cover_rate', 50)
        if cr > 55:
            score['away'] += 0.05
        elif cr < 40:
            score['away'] -= 0.05
    
    return score


def calc_form_factor(analysis: dict, home_team: str, away_team: str) -> dict:
    """
    状态因子: 媒体分析的近况走势、赛季胜率
    """
    score = {'home': 0, 'draw': 0, 'away': 0}
    
    # 从媒体分析提取状态
    for line in analysis.get('media_analysis', []):
        if '近况走势' in line:
            # Extract team form strings
            forms = re.findall(r'([LWD]+)', line)
            for i, f in enumerate(forms):
                if len(f) >= 3:
                    # Calculate points from last N matches
                    wins = f.count('W')
                    draws = f.count('D')
                    losses = f.count('L')
                    total = len(f)
                    pts = wins * 3 + draws
                    pts_per_game = pts / (total * 3)  # normalize to 0-1
                    
                    if i == 0:  # home team
                        score['home'] += pts_per_game * 0.10
                    elif i == 1:  # away team
                        score['away'] += pts_per_game * 0.10
    
    # 从赛季数据统计提取胜率
    stats = analysis.get('stats', {})
    stats_text = stats.get('raw', '')
    
    home_wins = re.search(r'主队.*?胜\s*(\d+)%\s*\[(\d+)\]', stats_text)
    if home_wins:
        win_pct = float(home_wins.group(1)) / 100
        score['home'] += win_pct * 0.08
    
    away_wins = re.search(r'客队.*?胜\s*(\d+)%\s*\[(\d+)\]', stats_text)
    if away_wins:
        win_pct = float(away_wins.group(1)) / 100
        score['away'] += win_pct * 0.08
    
    # 信心指数
    for line in analysis.get('media_analysis', []):
        if '信心指数' in line:
            if '主队' in line or '主胜' in line:
                score['home'] += 0.05
            elif '客队' in line or '客胜' in line:
                score['away'] += 0.05
            elif '和局' in line or '平局' in line:
                score['draw'] += 0.05
    
    return score


def calc_league_factor(analysis: dict) -> dict:
    """
    联赛排名因子
    """
    score = {'home': 0, 'draw': 0, 'away': 0}
    lt = analysis.get('league_table', {})
    home_rank = lt.get('home_rank', 999)
    away_rank = lt.get('away_rank', 999)
    
    if home_rank < 999 and away_rank < 999:
        diff = away_rank - home_rank
        if diff > 3:
            score['home'] += 0.08
        elif diff < -3:
            score['away'] += 0.08
        elif abs(diff) <= 2:
            score['draw'] += 0.03
    
    return score


def calc_ou_factor(overunder: dict, handicap_stats: dict, home_team: str, away_team: str) -> dict:
    """
    大小球因子
    """
    oc = overunder.get('current', {})
    oi = overunder.get('initial', {})
    
    try:
        line_str = oc.get('line', '0')
        if '/' in line_str:
            line_val = float(line_str.split('/')[0])
        else:
            line_val = float(line_str)
    except (ValueError, TypeError):
        return {'over': 0, 'under': 0}
    
    score = {'over': 0, 'under': 0}
    
    # 盘口深度
    if line_val >= 3.0:
        score['over'] += 0.10
    elif line_val <= 2.25:
        score['under'] += 0.10
    
    # 升盘/降盘
    try:
        old_line = oi.get('line', '0')
        if '/' in old_line:
            old_val = float(old_line.split('/')[0])
        else:
            old_val = float(old_line)
        
        if line_val > old_val:
            score['over'] += 0.08
        elif line_val < old_val:
            score['under'] += 0.08
    except:
        pass
    
    # 水位信号
    try:
        over_water = float(oc.get('over', 0.50))
        under_water = float(oc.get('under', 0.50))
        if over_water < 0.85:
            score['over'] += 0.05
        if under_water < 0.85:
            score['under'] += 0.05
    except:
        pass
    
    # 盘路大球率
    teams_data = handicap_stats.get('teams', {})
    for team in [home_team, away_team]:
        if team in teams_data:
            orate = teams_data[team].get('over_rate', 50)
            if orate > 55:
                score['over'] += 0.03
            elif orate < 40:
                score['under'] += 0.03
    
    return score


# ============================================================
# 第五步: 综合预测
# ============================================================
def combine_predictions(factors: dict, weights: dict = None,
                          elo_tuple=None, pressure_scores=None,
                          hcap_odds_score=None) -> dict:
    """
    综合所有因子进行最终预测（v5 — 赔率综合+让球盘+一致性）
    """
    if weights is None:
        weights = PREDICTION_WEIGHTS
    
    # 赛果得分
    result_scores = {'home': 0, 'draw': 0, 'away': 0}
    ou_scores = {'over': 0, 'under': 0}
    
    # --- 赔率综合因子（合并隐含概率+变动）---
    odds_w = weights.get('odds_composite', 0.35)
    if 'odds_composite' in factors:
        data = factors['odds_composite']
        for k in result_scores:
            result_scores[k] += data.get(k, 0) * odds_w
    
    # --- 让球盘赔率因子（新增！）---
    hcap_w = weights.get('hcap_odds', 0.15)
    if hcap_odds_score:
        for k in result_scores:
            result_scores[k] += hcap_odds_score.get(k, 0) * hcap_w
    
    # --- 亚盘水位 ---
    asian_w = weights.get('asian_handicap', 0.10)
    if 'asian_handicap' in factors:
        data = factors['asian_handicap']
        for k in result_scores:
            result_scores[k] += data.get(k, 0) * asian_w
    
    # --- 状态因子 ---
    form_w = weights.get('form_factor', 0.10)
    if 'form_factor' in factors:
        data = factors['form_factor']
        for k in result_scores:
            result_scores[k] += data.get(k, 0) * form_w
    
    # --- 联赛排名因子 ---
    league_w = weights.get('league_factor', 0.05)
    if 'league_factor' in factors:
        data = factors['league_factor']
        for k in result_scores:
            result_scores[k] += data.get(k, 0) * league_w
    
    # --- Elo因子 ---
    elo_w = weights.get('elo', 0.15)
    if elo_tuple:
        elo_score, elo_h, elo_a, exp_h = elo_tuple
        for k in result_scores:
            result_scores[k] += elo_score.get(k, 0) * elo_w
    
    # --- 压力系数 ---
    if pressure_scores:
        for k in result_scores:
            result_scores[k] += pressure_scores.get(k, 0)
    
    # --- 大小球 ---
    ou_w = weights.get('over_under', 0.10)
    if 'over_under' in factors:
        ou_data = factors['over_under']
        ou_scores['over'] = ou_data.get('over', 0) * ou_w
        ou_scores['under'] = ou_data.get('under', 0) * ou_w
    
    # 归一化
    r_total = sum(result_scores.values())
    if r_total > 0:
        for k in result_scores:
            result_scores[k] = round(result_scores[k] / r_total, 4)
    
    # 确定赛果
    if result_scores['home'] > result_scores['away'] and result_scores['home'] > result_scores['draw']:
        result = 'home'
        result_text = '主胜'
        confidence = result_scores['home']
    elif result_scores['away'] > result_scores['home'] and result_scores['away'] > result_scores['draw']:
        result = 'away'
        result_text = '客胜'
        confidence = result_scores['away']
    else:
        result = 'draw'
        result_text = '平局'
        confidence = result_scores['draw']
    
    # 大小球预测
    ou_total = ou_scores['over'] + ou_scores['under']
    if ou_total > 0:
        over_pct = ou_scores['over'] / ou_total
        under_pct = ou_scores['under'] / ou_total
        ou_pred = '大球' if over_pct >= under_pct else '小球'
        ou_conf = max(over_pct, under_pct)
    else:
        ou_pred = '未知'
        ou_conf = 0
    
    # 比分预测（泊松分布）
    score_hints = predict_score_poisson(result, result_scores, ou_pred)
    
    # 半全场预测
    htft = predict_htft(result, result_scores)
    
    return {
        'result_prediction': {
            'value': result,
            'prediction': result_text,
            'confidence': round(confidence, 4),
            'scores': result_scores,
        },
        'ou_prediction': {
            'value': ou_pred,
            'prediction': ou_pred,
            'confidence': round(ou_conf, 4),
        },
        'score_prediction': score_hints,
        'htft_prediction': htft,
        'all_factors': {k: v for k, v in factors.items()},
        'elo': {'home': elo_tuple[1] if elo_tuple else 1500,
                'away': elo_tuple[2] if elo_tuple else 1500,
                'expected': round(elo_tuple[3], 3) if elo_tuple else 0.5},
    }


def predict_score_poisson(result: str, scores: dict, ou_pred: str = None) -> dict:
    """
    泊松分布比分预测（v2 — 替代硬编码if/else链）
    基于归一化胜率分推导预期进球
    """
    import math
    
    home_score = scores.get('home', 0.5)
    away_score = scores.get('away', 0.5)
    
    # 从胜率分推导预期进球
    ratio = home_score / away_score if away_score > 0 else 999
    if ratio > 2.5:
        lam_h = 2.2 + (ratio - 2.5) * 0.3
        lam_a = 0.5
    elif ratio > 1.5:
        lam_h = 1.6 + (ratio - 1.5) * 0.6
        lam_a = 0.7
    elif ratio > 0.67:
        # 均势区间
        lam_h = 1.3
        lam_a = 1.3
    elif ratio > 0.4:
        lam_h = 0.8
        lam_a = 1.8
    else:
        lam_h = 0.5
        lam_a = 2.5
    
    # 大小球修正
    if ou_pred == '大球':
        lam_h *= 1.15
        lam_a *= 1.15
    elif ou_pred == '小球':
        lam_h *= 0.85
        lam_a *= 0.85
    
    lam_h = max(lam_h, 0.3)
    lam_a = max(lam_a, 0.3)
    
    def poisson(k, lam):
        return (lam ** k) * math.exp(-lam) / math.factorial(k) if k > 0 else math.exp(-lam)
    
    # 计算所有比分概率
    score_probs = {}
    for hg in range(7):
        for ag in range(7):
            p = poisson(hg, lam_h) * poisson(ag, lam_a)
            if p > 0.005:
                score_probs[f"{hg}-{ag}"] = p
    
    sorted_scores = sorted(score_probs.items(), key=lambda x: -x[1])
    
    most_likely = sorted_scores[0][0] if sorted_scores else '0-0'
    alternatives = [s[0] for s in sorted_scores[1:5]] if len(sorted_scores) > 1 else []
    
    return {
        'most_likely': most_likely,
        'alternatives': alternatives,
        'expected_home_goals': round(lam_h, 2),
        'expected_away_goals': round(lam_a, 2),
    }


def predict_htft(result: str, scores: dict) -> dict:
    """半全场预测（v2 — 细化分级）"""
    home_score = scores.get('home', 0.5)
    away_score = scores.get('away', 0.5)
    draw_score = scores.get('draw', 0.3)
    ratio = home_score / away_score if away_score > 0 else 999
    
    if result == 'home':
        if ratio > 3.0:
            htft = '胜胜'
            conf = '高'
        elif ratio > 1.5:
            htft = '胜胜'
            conf = '中'
        elif ratio > 0.67:
            htft = '平胜'
            conf = '中'
        else:
            htft = '平胜'
            conf = '低'
    elif result == 'away':
        away_ratio = 1/ratio if ratio > 0 else 999
        if away_ratio > 3.0:
            htft = '负负'
            conf = '高'
        elif away_ratio > 1.5:
            htft = '负负'
            conf = '中'
        elif away_ratio > 0.67:
            htft = '平负'
            conf = '中'
        else:
            htft = '平负'
            conf = '低'
    else:
        if draw_score > 0.35:
            htft = '平平'
            conf = '高'
        else:
            htft = '平平'
            conf = '中'
    
    return {'value': htft, 'confidence': conf}


# ============================================================
# 主流程
# ============================================================
def main():
    league = sys.argv[1] if len(sys.argv) > 1 else '芬超'
    
    print(f"⚽ 正在采集 {league} 数据...")
    
    # Step 1: Get match list from cp.nowscore.com
    html = fetch(NOWSCORE_URL)
    if html.startswith('<error>'):
        print(f"❌ 无法访问 cp.nowscore.com: {html}")
        return
    
    matches = parse_all_matches(html, league)
    if not matches:
        print(f"❌ 未找到 {league} 比赛")
        return
    
    print(f"📋 发现 {len(matches)} 场 {league} 比赛\n")
    
    # Step 2: For each match, fetch detailed data
    predictions = []
    for i, m in enumerate(matches):
        sid = m['schedule_id']
        home, away = m.get('home_team', '?'), m.get('away_team', '?')
        print(f"  [{i+1}/{len(matches)}] {home} vs {away}...", end=' ', flush=True)
        
        # Fetch 3-in-1
        h3 = fetch(f"{LIVE_BASE}/odds/match/{sid}.htm")
        d3 = parse_3in1(h3) if not h3.startswith('<error>') else {}
        
        # Fetch analysis page
        ha = fetch(f"{LIVE_BASE}/analysis/{sid}cn.html")
        analysis_data = parse_analysis(ha, home, away) if not ha.startswith('<error>') else {}
        
        # Calculate factors
        factors = {}
        
        # === v5 赔率综合因子（合并隐含概率+赔率变动+一致性验证）===
        # 替代旧版的 calc_odds_factor（静态隐含概率）和 calc_odds_factor（动态变动）分别调用
        factors['odds_composite'] = calc_odds_composite(
            d3.get('euro', {}),
            m.get('jingcai', {})
        )
        
        # === v5 让球盘赔率因子（使用竞彩让球赔率）===
        # 竞彩赔率就是让球胜平负赔率
        jc = m.get('jingcai', {})
        handicap_val = m.get('polygoal', '')
        handicap_int = m.get('jingcai_handicap', 0)
        
        # 从竞彩赔率构建让球盘信号：竞彩赔率已包含让球因素
        # 例如拉赫蒂(-1): 竞彩赔率1.78/3.53/4.31是让一球后的胜平负
        hcap_odds_str = None
        if jc and jc.get('home_win'):
            hcap_odds_str = f"{jc['home_win']}/{jc['draw']}/{jc['away_win']}"
        
        hcap_odds_score = calc_hcap_odds_factor(handicap_val or handicap_int, hcap_odds_str)
        
        # Asian handicap (水位分析)
        factors['asian_handicap'] = calc_asian_factor(
            d3.get('asian', {}), analysis_data.get('handicap_stats', {}), home, away
        )
        
        # Form factor
        factors['form_factor'] = calc_form_factor(analysis_data, home, away)
        
        # League factor
        factors['league_factor'] = calc_league_factor(analysis_data)
        
        # Over/under
        factors['over_under'] = calc_ou_factor(
            d3.get('overunder', {}), analysis_data.get('handicap_stats', {}), home, away
        )
        
        # Elo因子
        elo_tuple = calc_elo_factor(home, away)
        
        # 压力系数
        pressure_scores = calc_pressure_factor(get_elo_fin(home), get_elo_fin(away))
        
        # Combine (v5 — 赔率综合+让球盘+一致性验证)
        pred = combine_predictions(factors, elo_tuple=elo_tuple,
                                    pressure_scores=pressure_scores,
                                    hcap_odds_score=hcap_odds_score)
        
        # Store result (v2 with Elo)
        prediction = {
            'match_no': m.get('match_no', '?'),
            'match_time': m.get('match_time', ''),
            'home_team': home,
            'away_team': away,
            'league': league,
            'jingcai_odds': m.get('jingcai', {}),
            'jingcai_handicap': m.get('jingcai_handicap', 0),
            'euro_odds': d3.get('euro', {}).get('current', {}),
            'asian_hcap': d3.get('asian', {}).get('current', {}).get('handicap', ''),
            'ou_line': d3.get('overunder', {}).get('current', {}).get('line', ''),
            'result_prediction': pred['result_prediction'],
            'score_prediction': pred['score_prediction'],
            'ou_prediction': pred['ou_prediction'],
            'htft_prediction': pred['htft_prediction'],
            'factors': pred['all_factors'],
            'elo': pred.get('elo', {}),
        }
        predictions.append(prediction)
        
        print("✅")
    
    # Output
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    output = {
        'fetch_time': now,
        'league': league,
        'match_count': len(predictions),
        'predictions': predictions,
    }
    
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    output_file = DATA_DIR / f"predictions-fin-{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    
    # Also save as latest
    with open(DATA_DIR / 'predictions-fin-latest.json', 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    
    print(f"\n💾 已保存: {output_file}")
    
    # Print summary
    print(f"\n{'='*70}")
    print(f"  📊 {league} 预测结果")
    print(f"{'='*70}")
    for p in predictions:
        rp = p['result_prediction']
        sp = p['score_prediction']
        op = p['ou_prediction']
        hp = p['htft_prediction']
        
        team_line = f"{p['home_team']} vs {p['away_team']}"
        print(f"\n  {'─'*50}")
        print(f"  📌 {team_line} ({p['match_time']})")
        print(f"  赛果: {rp['prediction']} (信{rp['confidence']:.0%})")
        print(f"  比分: {sp['most_likely']} (备选: {'/'.join(sp['alternatives'])})")
        print(f"  大小: {op['prediction']} | 半全场: {hp['value']}")
        print(f"  欧赔: {p.get('euro_odds',{}).get('home','?')}/{p.get('euro_odds',{}).get('draw','?')}/{p.get('euro_odds',{}).get('away','?')}")
        print(f"  亚盘: {p.get('asian_hcap','?')} | 大小: {p.get('ou_line','?')}")
        elo = p.get('elo', {})
        if elo:
            print(f"  Elo: {p['home_team']}({elo.get('home','?')}) vs {p['away_team']}({elo.get('away','?')})")
    
    print(f"\n{'='*70}")
    print(f"  ✅ 完成: {len(predictions)} 场")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
