#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
⚽ 绿茵神算 v3.0 · 智能预测引擎 (Elo+动态权重)

多因子集成预测模型，输出:
  - 赛果预测（主胜/平/客胜）及置信度
  - 比分预测（最可能比分+备选）
  - 大小球预测
  - 半全场预测（HT/FT）
  - 投注推荐（带星级评级）
  - 战意分析（小组形势、出线动机）
  - Elo评分融合（客观强度对比）

算法架构 (v3):
  因子1: 欧赔隐含概率 + 凯利指数 (动态权重)
  因子2: 赔率变动方向 & 幅度 (动态权重)
  因子3: 亚盘盘口 + 水位深度分析 (动态权重)
  因子4: 大小球盘口 + 变动趋势 (动态权重)
  因子5: 战意分析（小组排名/积分/出线动机）(动态权重)
  因子6: Elo评分系统（客观球队强度对比）(动态权重)

  权重随比赛阶段动态调整:
    小组赛: 赔率主导(70%) + 战意(15%) + Elo(5%)
    淘汰赛(16强): 赔率(60%) + Elo(22%) + 战意(10%)
    8强:        赔率(51%) + Elo(34%) + 战意(8%)
    半决赛/决赛: Elo主导(50-55%) + 赔率(40%) + 战意(5%)

用法:
  python scripts/predict.py                       # 预测最新
  python scripts/predict.py --file data/odds-latest.json
  python scripts/predict.py --historical
  python scripts/predict.py --stage 淘汰赛        # 指定比赛阶段
"""

# 导入argparse用于命令行参数
import argparse

import json
import sys
from datetime import datetime
from pathlib import Path
from math import exp, log, pi, sqrt

SCRIPT_DIR = Path(__file__).parent.resolve()
PROJECT_DIR = SCRIPT_DIR.parent
DATA_DIR = PROJECT_DIR / "data"


# ============ 工具函数 ============

def cw(s):
    w = 0
    for ch in s:
        w += 2 if ord(ch) > 127 else 1
    return w


def pad_to(s, target):
    return s + ' ' * max(0, target - cw(s))


def bar_full(cv, length=25):
    return '█' * int(cv * length) + '░' * (length - int(cv * length))


def stars(cn, max_s=5):
    filled = int(cn * max_s)
    return '⭐' * filled + ('☆' * (max_s - filled) if filled < max_s else '')


def conv_hcap(h):
    """Convert Chinese handicap text to numeric value (positive = home favored)"""
    if not h:
        return 0
    
    is_receiving = h.startswith('受')
    key = h[1:] if is_receiving else h
    
    d = {'平手': 0, '平/半': 0.25, '半球': 0.5, '半/一': 0.75, '一球': 1.0,
         '一/球半': 1.25, '球半': 1.5, '球半/两': 1.75, '两球': 2.0,
         '两/两半': 2.25, '两半': 2.5, '两球半': 2.5, '两/两球半': 2.25,
         '两球半/三': 2.75, '三球': 3.0, '三/三半': 3.25, '三半': 3.5}
    
    val = d.get(key, 0)
    if is_receiving:
        val = -val
    return val


# ============ Elo评分系统 ============

# 48支球队的初始Elo评级
# ============ 动态Elo评分系统 ============
# 初始Elo基准分
INITIAL_ELO_BASE = {
    # 夺冠热门 (基础1500 + 250)
    '阿根廷': 1790, '西班牙': 1750, '法国': 1780, '英格兰': 1760, '巴西': 1770,
    # 一线强队 (基础1500 + 150)
    '德国': 1680, '葡萄牙': 1650, '荷兰': 1660, '乌拉圭': 1640,
    '克罗地亚': 1630, '摩洛哥': 1620, '哥伦比亚': 1610, '日本': 1600, '挪威': 1590,
    # 二线/东道主 (基础1500 + 50)
    '美国': 1580, '墨西哥': 1590, '加拿大': 1550,
    '瑞士': 1570, '韩国': 1560, '比利时': 1580, '塞内加尔': 1550,
    '厄瓜多尔': 1540, '埃及': 1540, '澳大利亚': 1530, '苏格兰': 1520, '土耳其': 1520,
    # 中游/新军 (基础1500)
    '捷克': 1510, '波黑': 1500, '卡塔尔': 1480, '巴拉圭': 1510,
    '科特迪瓦': 1500, '突尼斯': 1490, '伊朗': 1500, '新西兰': 1470,
    '沙特阿拉伯': 1480, '阿尔及利亚': 1500, '加纳': 1490, '巴拿马': 1460,
    '伊拉克': 1450, '乌兹别克斯坦': 1460, '约旦': 1450, '南非': 1480,
    '海地': 1430, '库拉索': 1400, '佛得角': 1430, '刚果金': 1440,
    '奥地利': 1530, '瑞典': 1550,
    # 别名/简称映射（确保匹配赔率数据中的短名）
    '阿尔及利': 1500,   # 同阿尔及利亚
    '乌兹别克': 1460,   # 同乌兹别克斯坦
}

# 动态Elo存储文件
ELO_DATA_FILE = DATA_DIR / 'elo-data.json'


def load_elo_ratings():
    """加载动态Elo评分（如果有历史更新数据则使用，否则使用初始值）"""
    if ELO_DATA_FILE.exists():
        try:
            with open(ELO_DATA_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return dict(INITIAL_ELO_BASE)


def save_elo_ratings(elo_dict):
    """保存更新后的Elo评分"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(ELO_DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(elo_dict, f, ensure_ascii=False, indent=2)


def update_elo_from_results(actual_results):
    """根据实际赛果批量更新Elo评分
    
    Args:
        actual_results: list of dict, each with home_team, away_team, home_goals, away_goals
    """
    elo_dict = load_elo_ratings()
    updates_log = []
    k = 32  # 标准学习系数
    
    for r in actual_results:
        home = r.get('home_team', '')
        away = r.get('away_team', '')
        hg = r.get('home_goals', 0)
        ag = r.get('away_goals', 0)
        
        if not home or not away:
            continue
        
        # 归一化队名
        home = TEAM_ALIASES.get(home, home)
        away = TEAM_ALIASES.get(away, away)
        
        elo_h = elo_dict.get(home, 1500)
        elo_a = elo_dict.get(away, 1500)
        
        # 判断赛果
        if hg > ag:
            result = 1.0  # 主胜
        elif hg == ag:
            result = 0.5  # 平局
        else:
            result = 0.0  # 客胜
        
        # 计算期望值
        expected = expected_score(elo_h, elo_a)
        
        # 进球差调整K值（大比分胜利加大调整幅度）
        goal_diff = abs(hg - ag)
        adjusted_k = k * (1 + 0.15 * min(goal_diff, 5))
        
        # 冷门调整（如果预期胜率低但赢了，加大调整）
        upset_factor = 1.0
        if result == 1.0 and expected < 0.3:
            upset_factor = 1.5  # 大冷门
        elif result == 0.0 and expected > 0.7:
            upset_factor = 1.5
        adjusted_k *= upset_factor
        
        # 更新Elo
        new_elo_h = elo_h + adjusted_k * (result - expected)
        new_elo_a = elo_a + adjusted_k * ((1 - result) - (1 - expected))
        
        old_h, old_a = elo_dict.get(home, 1500), elo_dict.get(away, 1500)
        elo_dict[home] = round(new_elo_h, 0)
        elo_dict[away] = round(new_elo_a, 0)
        
        updates_log.append(f'{home} {old_h:.0f}→{new_elo_h:.0f} (+{new_elo_h-old_h:.0f}), {away} {old_a:.0f}→{new_elo_a:.0f} ({new_elo_a-old_a:+.0f})')
    
    save_elo_ratings(elo_dict)
    return updates_log


def expected_score(elo_a, elo_b):
    """计算A队对B队的期望胜率
    Args:
        elo_a: A队Elo评分
        elo_b: B队Elo评分
    Returns:
        float: A队期望胜率 (0-1)
    """
    return 1.0 / (1.0 + 10.0 ** ((elo_b - elo_a) / 400.0))


# 缓存当前Elo（避免频繁读文件）
_elo_cache = None

def get_elo(team_name):
    """获取球队当前Elo评分（支持别名，优先使用动态数据）"""
    global _elo_cache
    if _elo_cache is None:
        _elo_cache = load_elo_ratings()
    name = TEAM_ALIASES.get(team_name, team_name)
    return _elo_cache.get(name, 1500)


def refresh_elo_cache():
    """刷新Elo缓存（新赛果录入后调用）"""
    global _elo_cache
    _elo_cache = load_elo_ratings()

# 队伍名称别名映射（赔率数据常用简称→全称）
TEAM_ALIASES = {
    '阿尔及利': '阿尔及利亚',
    '乌兹别克': '乌兹别克斯坦',
}

# 中文名→英文名映射（用于K值调整）
TIER_MAP = {
    '夺冠热门': ['阿根廷', '西班牙', '法国', '英格兰', '巴西'],
    '一线强队': ['德国', '葡萄牙', '荷兰', '乌拉圭', '克罗地亚', '摩洛哥', '哥伦比亚', '日本', '挪威'],
    '二线': ['美国', '墨西哥', '加拿大', '瑞士', '韩国', '比利时', '塞内加尔', '厄瓜多尔', '埃及', '澳大利亚', '苏格兰', '土耳其', '奥地利', '瑞典'],
}


# ============ 动态权重系统 ============

STAGE_WEIGHTS = {
    # ★优化v2: 降低Elo权重至合理水平，提高赔率因子权重
    # 6月22日模型(准确率70%+)使用 odds_implied=0.35, elo=0.05 效果最佳
    # 后续版本elo提升到0.15反而不准，因为Elo是静态的
    '小组赛':  {'odds_implied': 0.35, 'odds_movement': 0.12, 'asian_handicap': 0.22, 'over_under': 0.08, 'motivation': 0.12, 'elo': 0.11},
    '16强':    {'odds_implied': 0.28, 'odds_movement': 0.08, 'asian_handicap': 0.17, 'over_under': 0.07, 'motivation': 0.10, 'elo': 0.30},
    '8强':     {'odds_implied': 0.22, 'odds_movement': 0.06, 'asian_handicap': 0.15, 'over_under': 0.06, 'motivation': 0.08, 'elo': 0.43},
    '半决赛':  {'odds_implied': 0.18, 'odds_movement': 0.04, 'asian_handicap': 0.12, 'over_under': 0.05, 'motivation': 0.05, 'elo': 0.56},
    '决赛':    {'odds_implied': 0.15, 'odds_movement': 0.03, 'asian_handicap': 0.12, 'over_under': 0.05, 'motivation': 0.05, 'elo': 0.60},
    'default': {'odds_implied': 0.32, 'odds_movement': 0.10, 'asian_handicap': 0.20, 'over_under': 0.08, 'motivation': 0.12, 'elo': 0.18},
}


def get_dynamic_weights(stage='小组赛', recent_form_h=0.5, recent_form_a=0.5):
    """
    根据比赛阶段和球队状态获取动态权重
    
    Args:
        stage: 比赛阶段 (小组赛/16强/8强/半决赛/决赛)
        recent_form_h: 主队近期状态 (0-1)
        recent_form_a: 客队近期状态 (0-1)
    Returns:
        dict: 各因子权重
    """
    weights = dict(STAGE_WEIGHTS.get(stage, STAGE_WEIGHTS['default']))
    
    # 状态调整：状态火热的球队，赔率因子权重微增
    avg_form = (recent_form_h + recent_form_a) / 2
    if avg_form > 0.7:
        weights['odds_implied'] = min(weights['odds_implied'] + 0.03, 0.50)
        weights['elo'] = max(weights['elo'] - 0.03, 0.05)
    elif avg_form < 0.3:
        weights['elo'] = min(weights['elo'] + 0.03, 0.60)
        weights['odds_implied'] = max(weights['odds_implied'] - 0.03, 0.10)
    
    # 归一化
    total = sum(weights.values())
    if abs(total - 1.0) > 0.001:
        for k in weights:
            weights[k] /= total
    
    return weights


# ============ 基本面数据加载 ============

def load_group_standings():
    cf = DATA_DIR / "group-standings.json"
    if not cf.exists():
        return {}
    with open(cf, 'r', encoding='utf-8') as f:
        data = json.load(f)
    standings = data.get('standings', {})
    team_map = {}
    for grp, teams in standings.items():
        for t in teams:
            team_map[t['team_cn']] = {**t, 'group': grp}
    return team_map


# ============ 赔率隐含概率 ============

def implied_prob(home_odds, draw_odds, away_odds):
    total = 1/home_odds + 1/draw_odds + 1/away_odds
    return {
        'home': (1/home_odds) / total,
        'draw': (1/draw_odds) / total,
        'away': (1/away_odds) / total,
    }


def kelly_index(probs, odds):
    """凯利指数: 衡量赔率价值"""
    return probs * odds - 1


# ============ 半全场预测 ============

def predict_htft(home_odds, draw_odds, away_odds, confidence_val, result_value):
    """
    半全场预测模型
    
    基于赔率和赛果预测，推断半场-全场组合:
      胜胜(3-3) 平平(1-1) 负负(0-0)    ← 半全场一致
      平胜(1-3) 平负(1-0)              ← 半场平局后一方发力
      胜负(3-0) 负胜(0-3)              ← 半场逆转（少见）
    
    策略:
      - 强队(odds<1.5): 多半 胜胜
      - 中赔(1.5-2.5): 可能 平胜/平负
      - 均势(2.5-3.5): 多半 平平
      - 高赔客队: 负负/平负
    """
    min_odds = min(home_odds, draw_odds, away_odds)
    is_home_fav = home_odds == min_odds and home_odds < 2.0
    is_away_fav = away_odds == min_odds and away_odds < 2.0
    is_even = abs(home_odds - away_odds) / max(home_odds, away_odds) < 0.3
    
    # HT/FT pattern probabilities based on odds
    htft_scores = {}
    
    if is_home_fav:
        if home_odds < 1.4:
            # 超级热门: 胜胜概率最高
            htft_scores = {'胜胜': 0.65, '平胜': 0.20, '负胜': 0.05, '平平': 0.05, '胜负': 0.03, '平负': 0.01, '负负': 0.01}
        elif home_odds < 1.7:
            htft_scores = {'胜胜': 0.45, '平胜': 0.25, '平平': 0.12, '胜负': 0.05, '负胜': 0.05, '平负': 0.05, '负负': 0.03}
        else:
            htft_scores = {'平胜': 0.30, '胜胜': 0.30, '平平': 0.15, '胜负': 0.08, '负胜': 0.07, '平负': 0.05, '负负': 0.05}
    elif is_away_fav:
        if away_odds < 1.4:
            htft_scores = {'负负': 0.60, '平负': 0.22, '胜负': 0.05, '平平': 0.05, '负胜': 0.03, '平胜': 0.03, '胜胜': 0.02}
        elif away_odds < 1.7:
            htft_scores = {'负负': 0.40, '平负': 0.28, '平平': 0.12, '胜负': 0.06, '负胜': 0.06, '平胜': 0.05, '胜胜': 0.03}
        else:
            htft_scores = {'平负': 0.32, '负负': 0.28, '平平': 0.15, '胜负': 0.08, '负胜': 0.07, '平胜': 0.05, '胜胜': 0.05}
    elif is_even:
        if draw_odds < 3.2:
            htft_scores = {'平平': 0.35, '平胜': 0.18, '平负': 0.18, '胜胜': 0.10, '负负': 0.10, '胜负': 0.05, '负胜': 0.04}
        else:
            htft_scores = {'平胜': 0.22, '平负': 0.22, '平平': 0.20, '胜胜': 0.12, '负负': 0.12, '胜负': 0.06, '负胜': 0.06}
    else:
        htft_scores = {'平平': 0.20, '平胜': 0.18, '平负': 0.18, '胜胜': 0.15, '负负': 0.15, '胜负': 0.07, '负胜': 0.07}
    
    # 根据赛果预测修正
    if result_value == 'home':
        htft_scores['胜胜'] = min(htft_scores.get('胜胜', 0) * 1.3, 0.8)
        htft_scores['平胜'] = min(htft_scores.get('平胜', 0) * 1.2, 0.6)
        htft_scores['负负'] *= 0.5
        htft_scores['平负'] *= 0.5
    elif result_value == 'away':
        htft_scores['负负'] = min(htft_scores.get('负负', 0) * 1.3, 0.8)
        htft_scores['平负'] = min(htft_scores.get('平负', 0) * 1.2, 0.6)
        htft_scores['胜胜'] *= 0.5
        htft_scores['平胜'] *= 0.5
    
    # 归一化
    total = sum(htft_scores.values())
    if total > 0:
        htft_scores = {k: v/total for k, v in htft_scores.items()}
    
    sorted_pairs = sorted(htft_scores.items(), key=lambda x: -x[1])
    primary = sorted_pairs[0] if sorted_pairs else ('不确定', 0)
    secondary = sorted_pairs[1] if len(sorted_pairs) > 1 else ('不确定', 0)
    tertiary = sorted_pairs[2] if len(sorted_pairs) > 2 else ('不确定', 0)
    
    # HT/FT score estimation
    htft_to_score = {
        '胜胜': ('主队领先→保持', '主队半场胜全场胜'),
        '平胜': ('半场胶着一方爆发', '主队半场平全场胜'),
        '负负': ('客队全程压制', '客队半场胜全场胜'),
        '平负': ('半场胶着一方爆发', '客队半场平全场胜'),
        '平平': ('全程僵持', '半场平全场平'),
        '胜负': ('冷门逆转', '主队半场胜全场负'),
        '负胜': ('冷门逆转', '客队半场胜全场胜'),
    }
    
    htft_desc = htft_to_score.get(primary[0], ('未知', ''))
    
    return {
        'most_likely': primary[0],
        'probability': round(primary[1], 2),
        'alternatives': [secondary[0], tertiary[0]],
        'description': htft_desc[0],
        'narrative': htft_desc[1],
    }


# ============ 比分预测（增强版） ============

def predict_score(home_odds, away_odds, home_team, away_team, hcap_val=0, line=2.5, ou_pred=None, result_value=None):
    """基于赔率+盘口的比分预测"""
    ratio = home_odds / away_odds if away_odds > 0 else 999
    
    abs_hcap = abs(hcap_val)
    
    # 计算期望进球 - 基于盘口和赔率差异
    if hcap_val >= 1.5:
        lam_h = min(2.5 + (hcap_val - 1.5) * 0.6, 5.0)
        lam_a = max(0.3 + hcap_val * 0.05, 0.2)
    elif hcap_val >= 1.0:
        lam_h = 2.0 + (hcap_val - 1.0) * 1.0
        lam_a = 0.4 + hcap_val * 0.05
    elif hcap_val >= 0.5:
        lam_h = 1.6 + (hcap_val - 0.5) * 1.2
        lam_a = 0.6 + hcap_val * 0.1
    elif hcap_val > 0:
        lam_h = 1.3 + hcap_val * 0.6
        lam_a = 0.8 + hcap_val * 0.1
    elif hcap_val == 0:
        if ratio < 0.7:  # 主队赔率明显低
            lam_h, lam_a = 1.5, 0.9
        elif ratio > 1.3:  # 客队赔率明显低
            lam_h, lam_a = 0.9, 1.5
        else:  # 均势
            lam_h, lam_a = 1.2, 1.2
    elif hcap_val > -0.5:
        lam_h = 0.9 + abs(hcap_val) * 0.2
        lam_a = 1.6 + abs(hcap_val) * 0.6
    elif hcap_val > -1.0:
        lam_h = 0.6 + abs(hcap_val) * 0.15
        lam_a = 1.8 + abs(hcap_val) * 0.5
    elif hcap_val > -1.5:
        lam_h = 0.4 + abs(hcap_val) * 0.1
        lam_a = 2.2 + abs(hcap_val) * 0.4
    else:
        lam_h = 0.2 + abs(hcap_val) * 0.05
        lam_a = 2.5 + (abs(hcap_val) - 1.5) * 0.5
    
    # 确保主队得分合理
    lam_h = max(lam_h, 0.1)
    lam_a = max(lam_a, 0.1)
    
    # 大小球修正
    if line >= 3.0:
        lam_h *= 1.12
        lam_a *= 1.12
    elif line <= 2.0:
        lam_h *= 0.85
        lam_a *= 0.85
    
    if ou_pred == 'over':
        lam_h *= 1.08
        lam_a *= 1.08
    elif ou_pred == 'under':
        lam_h *= 0.92
        lam_a *= 0.92
    
    # 根据赛果结果强制调整，确保比分与结果一致
    if result_value == 'home' and lam_h - lam_a < 0.5:
        diff = lam_h - lam_a
        shift = (1.0 - diff) / 2
        lam_h += shift
        lam_a = max(lam_a - shift, 0.1)
    elif result_value == 'away' and lam_a - lam_h < 0.5:
        diff = lam_a - lam_h
        shift = (1.0 - diff) / 2
        lam_a += shift
        lam_h = max(lam_h - shift, 0.1)
    elif result_value == 'draw':
        avg = (lam_h + lam_a) / 2
        lam_h = avg
        lam_a = avg
    
    # 泊松分布
    from math import factorial as fact
    def poisson(k, lam):
        return (lam ** k) * exp(-lam) / fact(k) if k > 0 else exp(-lam)
    
    scores = []
    for i in range(7):
        for j in range(7):
            p = poisson(i, lam_h) * poisson(j, lam_a)
            if p > 0.008:
                # 比分文本
                if i > j and home_team:
                    result = f"{home_team} {i}-{j} {away_team}"
                elif i < j:
                    result = f"{home_team} {i}-{j} {away_team}"
                else:
                    result = f"{home_team} {i}-{j} {away_team}"
                scores.append((result, p, i, j))
    
    scores.sort(key=lambda x: -x[1])
    top = scores[:5] if scores else [(f"{home_team} ?-? {away_team}", 0, 0, 0)]
    
    # HT score estimate
    # 基于半场进球通常为全场1/3-1/2
    ht_lam_h = lam_h * 0.42
    ht_lam_a = lam_a * 0.42
    
    ht_scores = []
    for i in range(4):
        for j in range(4):
            p = poisson(i, ht_lam_h) * poisson(j, ht_lam_a)
            if p > 0.02:
                ht_scores.append((f"{home_team} {i}-{j} {away_team}(HT)", p, i, j))
    
    ht_scores.sort(key=lambda x: -x[1])
    
    return {
        'most_likely': top[0][0],
        'home_goals': top[0][2],
        'away_goals': top[0][3],
        'alternatives': [t[0] for t in top[1:4]] if len(top) > 1 else [],
        'expected_home_goals': round(lam_h, 2),
        'expected_away_goals': round(lam_a, 2),
        'ht_prediction': ht_scores[0][0] if ht_scores else f"{home_team} ?-? {away_team}(HT)",
        'ht_home_goals': ht_scores[0][2] if ht_scores else 0,
        'ht_away_goals': ht_scores[0][3] if ht_scores else 0,
    }


# ============ 基本面动机分析 ============

# ============ 战意分析（第3轮关键因子）============

def classify_motivation(team):
    """
    根据小组排名&积分判断球队战意状态
    
    返回: (战意等级, 战意描述, 预期进球调整系数, 防守强度调整)
    """
    pts = team.get('pts', 0)
    mp = max(team.get('mp', 1), 1)
    rank = team.get('rank', 4)
    ppg = pts / mp
    
    # ---- 第3轮战意模型 ----
    # 已出线(>=6分): 轮换主力,压制节奏,避免受伤
    if pts >= 6:
        return ('已出线', '已锁定出线，可能轮换主力，节奏放缓', 0.75, 1.15)
    
    # 保平即出线(前2名且>=4分): 保守战术,防守优先
    if rank <= 2 and pts >= 4:
        if ppg >= 2.0:
            return ('保平出线', '保平即可出线，战术保守，小比分倾向', 0.80, 1.20)
        else:
            return ('巩固位置', '赢球基本锁定，可接受平局', 0.85, 1.10)
    
    # 前2名但分不高: 需要赢球彻底锁定
    if rank <= 2:
        return ('主动进取', '赢球即出线，全力争胜', 1.15, 0.95)
    
    # 第3名有积分(1-3分): 背水一战
    if rank == 3 and pts >= 3:
        return ('背水一战', f'第3名{pts}分，必须赢才有机会，全力进攻', 1.30, 0.85)
    
    # 第3名低分或第4名有积分: 必须赢+看别人脸色
    if rank == 3 or pts > 0:
        return ('绝境求生', '必须赢且需要其他场次配合，奋力一搏', 1.20, 0.90)
    
    # 0分垫底: 荣誉之战
    return ('荣誉之战', '已确定出局，为荣誉而战，压力小', 1.05, 1.00)


def factor_motivation(home_team, away_team, team_stats):
    """
    战意因子: 分析双方在第3轮的比赛动机 ★优化v2
    
    优化点:
      1. 更精细的出线形势分析（考虑净胜球、相互战绩）
      2. 去除重复计算（压力系数不再在factor_motivation中返回）
      3. 平局可能性更准确的评估
      4. 添加"争小组第一"的动机分析
    
    返回:
      - 胜负倾向 (谁更想要赢)
      - 进球调整 (预期总进球乘数)
      - 信心调整
      - 分析文本
    """
    hs = team_stats.get(home_team, {})
    ha = team_stats.get(away_team, {})
    
    if not hs or not ha:
        return {'result': None, 'confidence': 0, 'analysis': [],
                'goal_multiplier': 1.0, 'home_intensity': 1.0, 'away_intensity': 1.0,
                'home_motive': '', 'away_motive': '',
                'draw_tendency': 0, 'motivation_score': {'home': 0, 'away': 0}}
    
    hm, hd, hg, hd_def = classify_motivation(hs)
    am, ad, ag, ad_def = classify_motivation(ha)
    
    analysis = []
    analysis.append(f"{home_team}: {hd}")
    analysis.append(f"{away_team}: {ad}")
    
    # ---- 详细出线形势分析 ----
    home_pts = hs.get('pts', 0)
    away_pts = ha.get('pts', 0)
    home_gd = hs.get('gd', 0) or (hs.get('gf', 0) - hs.get('ga', 0))
    away_gd = ha.get('gd', 0) or (ha.get('gf', 0) - ha.get('ga', 0))
    home_rank = hs.get('rank', 4)
    away_rank = ha.get('rank', 4)
    
    # 判断是否有"争小组第一"动机
    home_needs_first = (home_pts >= 3 and home_rank <= 2 and 
                       any(k == home_team for k in ['阿根廷','巴西','法国','英格兰','德国','西班牙','葡萄牙','荷兰']))
    away_needs_first = (away_pts >= 3 and away_rank <= 2 and
                       any(k == away_team for k in ['阿根廷','巴西','法国','英格兰','德国','西班牙','葡萄牙','荷兰']))
    
    # ---- 胜负倾向分析 ----
    motive_rank = {
        '背水一战': 5, '主动进取': 4, '绝境求生': 3,
        '保平出线': 2, '巩固位置': 2, '荣誉之战': 1, '已出线': 0
    }
    h_mr = motive_rank.get(hm, 1)
    a_mr = motive_rank.get(am, 1)
    
    # 如果争小组第一，战意+1级
    if home_needs_first:
        h_mr += 1
        analysis.append(f"{home_team}需争小组第一避强敌，战意提升")
    if away_needs_first:
        a_mr += 1
        analysis.append(f"{away_team}需争小组第一避强敌，战意提升")
    
    result = None
    conf = 0
    draw_tendency = 0  # 平局倾向度 0-1
    
    # 战意差距越大，越倾向战意高的一方
    motive_diff = h_mr - a_mr
    if motive_diff >= 2:
        result = 'home'
        conf = min(abs(motive_diff) * 0.08, 0.25)
        analysis.append(f"{home_team}战意({hm})远强于{away_team}({am}), 主队更饥渴")
    elif motive_diff <= -2:
        result = 'away'
        conf = min(abs(motive_diff) * 0.08, 0.25)
        analysis.append(f"{away_team}战意({am})远强于{home_team}({hm}), 客队更饥渴")
    elif motive_diff >= 1:
        result = 'home'
        conf = 0.05
        analysis.append(f"{home_team}战意略强")
    elif motive_diff <= -1:
        result = 'away'
        conf = 0.05
        analysis.append(f"{away_team}战意略强")
    else:
        analysis.append(f"双方战意相当({hm} vs {am})")
    
    # ---- 平局可能性分析 ----
    # 双方保平即出线 -> 平局概率大增
    if hm == '保平出线' and am == '保平出线':
        draw_tendency = 0.6
        analysis.append("双方保平即出线→默契平局概率极高")
    elif hm == '保平出线' and am in ('荣誉之战', '已出线'):
        draw_tendency = 0.3
        analysis.append(f"{home_team}保平即可，{away_team}无压力→平局可能")
    elif am == '保平出线' and hm in ('荣誉之战', '已出线'):
        draw_tendency = 0.3
    elif hm == '保平出线' and am == '背水一战':
        draw_tendency = 0.1  # 一方要保平，另一方搏命→分胜负
        analysis.append(f"{home_team}保平出线，{away_team}背水一战→大概率分胜负")
    elif am == '保平出线' and hm == '背水一战':
        draw_tendency = 0.1
    
    # 净胜球分析：如果平局即可凭借净胜球优势出线，平局倾向增加
    if hm == '保平出线' and home_gd > 0:
        draw_tendency += 0.05
    if am == '保平出线' and away_gd > 0:
        draw_tendency += 0.05
    
    # ---- 进球调整 ----
    goal_multiplier = (hg + ag) / 2
    
    # 特殊场景: 双方都保平 -> 小球
    if hm == '保平出线' and am == '保平出线':
        goal_multiplier *= 0.70
        analysis.append("双方都保平即出线→进球少")
    
    # 一方保平 vs 一方搏命 -> 进球偏向搏命方
    if hm == '保平出线' and am == '背水一战':
        goal_multiplier *= 0.90
        away_intensity_boost = 1.15
    else:
        away_intensity_boost = 1.0
    if am == '保平出线' and hm == '背水一战':
        goal_multiplier *= 0.90
        home_intensity_boost = 1.15
    else:
        home_intensity_boost = 1.0
    
    # 已出线队 vs 需要赢球队 -> 冷门可能
    if hm == '已出线' and am in ('背水一战', '绝境求生'):
        goal_multiplier *= 0.80
        analysis.append(f"{home_team}已出线可能留力，{away_team}搏命")
    if am == '已出线' and hm in ('背水一战', '绝境求生'):
        goal_multiplier *= 0.80
        analysis.append(f"{away_team}已出线可能留力")
    
    # 双方无压力 -> 开放比赛
    if hm in ('已出线', '荣誉之战') and am in ('已出线', '荣誉之战'):
        goal_multiplier *= 1.15
        analysis.append("双方无压力→可能打出开放比赛")
    
    # 双方都背水一战 -> 激烈但可能效率低
    if hm == '背水一战' and am == '背水一战':
        goal_multiplier *= 1.05
        analysis.append("双方都背水一战→激烈对攻")
    
    # ---- 防守强度调整 ----
    hi_val = (hd_def + 1) / 2
    ai_val = (ad_def + 1) / 2
    
    home_intensity = hi_val * home_intensity_boost
    away_intensity = ai_val * away_intensity_boost
    
    return {
        'result': result,
        'confidence': min(conf, 0.30),
        'analysis': analysis,
        'goal_multiplier': min(max(goal_multiplier, 0.55), 1.4),
        'home_intensity': min(max(home_intensity, 0.6), 1.3),
        'away_intensity': min(max(away_intensity, 0.6), 1.3),
        'home_motive': hm,
        'away_motive': am,
        'draw_tendency': min(draw_tendency, 0.8),
        'motivation_score': {'home': h_mr, 'away': a_mr},
    }


# ============ 因子分析 ============

def factor_odds_implied(match):
    jc = match.get('jingcai', {})
    if not all(k in jc for k in ['home_win', 'draw', 'away_win']):
        return {'result': None, 'confidence': 0, 'detail': 'No odds data'}
    
    probs = implied_prob(jc['home_win'], jc['draw'], jc['away_win'])
    
    # 凯利指数: 判断赔率是否有价值
    k_h = kelly_index(probs['home'], jc['home_win'])
    k_d = kelly_index(probs['draw'], jc['draw'])
    k_a = kelly_index(probs['away'], jc['away_win'])
    
    result, conf = None, 0.0
    
    # 多阈值判断
    home_dominance = probs['home'] / max(probs['away'], 0.01)
    away_dominance = probs['away'] / max(probs['home'], 0.01)
    
    if probs['home'] > 0.50:
        conf_base = min((probs['home'] - 0.40) * 3, 0.85)
        result = 'home'
        conf = conf_base
        # 凯利修正
        if k_h > 0.1:
            conf = min(conf + 0.1, 1.0)
        elif k_h < -0.05:
            conf *= 0.85
    elif probs['away'] > 0.50:
        conf_base = min((probs['away'] - 0.40) * 3, 0.85)
        result = 'away'
        conf = conf_base
        if k_a > 0.1:
            conf = min(conf + 0.1, 1.0)
        elif k_a < -0.05:
            conf *= 0.85
    elif probs['draw'] > 0.38:
        result = 'draw'
        conf = min((probs['draw'] - 0.35) * 3, 0.6)
    
    # 赔率绝对值修正
    min_odds = min(jc['home_win'], jc['draw'], jc['away_win'])
    if min_odds < 1.3 and result != 'draw':
        conf = min(conf + 0.2, 1.0)
    
    # ★优化v2: 当竞彩平赔异常偏低(<2.8)时，提高平局概率
    if jc['draw'] < 2.8 and probs.get('draw', 0) > 0.30:
        # 平赔异常低 -> 庄家防范平局
        if result != 'draw':
            # 降低其他方向置信度
            conf *= 0.8
    
    return {'result': result, 'confidence': conf, 'probs': probs,
            'kelly': {'home': round(k_h, 3), 'draw': round(k_d, 3), 'away': round(k_a, 3)}}


def factor_odds_movement(match):
    d3 = match.get('odds_3in1', {})
    euro = d3.get('euro', {})
    ec = euro.get('current', {})
    ei = euro.get('initial', {})
    
    if not ec or not ei:
        return {'result': None, 'confidence': 0, 'detail': 'No movement data'}
    
    try:
        ch = float(ec.get('home', 0)) - float(ei.get('home', 0))
        cd = float(ec.get('draw', 0)) - float(ei.get('draw', 0))
        ca = float(ec.get('away', 0)) - float(ei.get('away', 0))
    except (ValueError, TypeError):
        return {'result': None, 'confidence': 0, 'detail': 'Parse error'}
    
    conf = 0
    signals = []
    
    # 赔率变动降噪处理（三级阈值）
    if ch < -0.15:                      # 大幅度变动 → 强信号
        conf += min(abs(ch) * 2.0, 0.40)
        signals.append(f"主胜↓{abs(ch):.2f}(强信号)")
    elif ch < -0.10:                    # 中等变动 → 正常权重
        conf += min(abs(ch) * 1.5, 0.30)
        signals.append(f"主胜↓{abs(ch):.2f}")
    elif ch < -0.05:                    # 弱变动 → 降权50%
        conf += min(abs(ch) * 0.75, 0.15)
        signals.append(f"主胜↓{abs(ch):.2f}(弱信号)")
    
    if ca < -0.15:
        conf += min(abs(ca) * 2.0, 0.40)
        signals.append(f"客胜↓{abs(ca):.2f}(强信号)")
    elif ca < -0.10:
        conf += min(abs(ca) * 1.5, 0.30)
        signals.append(f"客胜↓{abs(ca):.2f}")
    elif ca < -0.05:
        conf += min(abs(ca) * 0.75, 0.15)
        signals.append(f"客胜↓{abs(ca):.2f}(弱信号)")
    
    # 平赔变动
    if cd > 0.1:
        signals.append(f"平赔↑{cd:.2f}(排除平局)")
        conf += 0.1
    elif cd < -0.08:
        signals.append(f"平赔↓{abs(cd):.2f}(防平)")
        conf += 0.08
    
    # 谁变动大就指向谁（阈值提高至0.05）
    threshold = 0.05
    if ch < ca and ch < -threshold:
        result = 'home'
    elif ca < ch and ca < -threshold:
        result = 'away'
    else:
        result = None
    
    return {'result': result, 'confidence': min(conf, 0.75), 'signals': signals,
            'delta': {'home': round(ch, 3), 'draw': round(cd, 3), 'away': round(ca, 3)}}


def factor_asian_handicap(match):
    d3 = match.get('odds_3in1', {})
    asian = d3.get('asian', {})
    ac = asian.get('current', {})
    ai = asian.get('initial', {})
    
    if not ac or not ac.get('handicap'):
        return {'result': None, 'confidence': 0, 'detail': 'No Asian data'}
    
    hcap = ac.get('handicap', '')
    try:
        ho = float(ac.get('home', '0'))
        ao = float(ac.get('away', '0'))
    except (ValueError, TypeError):
        ho, ao = 0, 0
    
    hcap_val = conv_hcap(hcap)
    signals = []
    conf = 0
    
    # 深盘分析
    if hcap_val >= 1.5:
        conf += 0.35
        signals.append(f"深盘让{hcap_val}球")
    elif hcap_val >= 1.0:
        conf += 0.25
        signals.append(f"让{hcap_val}球")
    elif hcap_val >= 0.5:
        conf += 0.15
        signals.append(f"让{hcap_val}")
    elif hcap_val <= -1.5:
        conf += 0.35
        signals.append(f"受让{abs(hcap_val)}(客队深盘)")
    elif hcap_val <= -0.5:
        conf += 0.20
        signals.append(f"受让{abs(hcap_val)}(客队优势)")
    
    # 水位深度分析
    if ho < 0.80 and hcap_val > 0:
        conf = min(conf + 0.25, 0.7)
        signals.append(f"低水{ho}强力防范主队")
    elif ho < 0.85 and hcap_val > 0:
        conf += 0.15
        signals.append(f"低水{ho}倾向主队")
    elif ho > 1.05 and hcap_val > 0:
        conf *= 0.7
        signals.append(f"高水{ho}阻盘")
    elif ho > 1.10 and hcap_val > 0:
        conf *= 0.5
        signals.append(f"超高水{ho}信心不足")
    
    if ao < 0.80 and hcap_val < 0:
        conf = min(conf + 0.25, 0.7)
        signals.append(f"客低水{ao}强力防范")
    elif ao < 0.85 and hcap_val < 0:
        conf += 0.15
        signals.append(f"客低水{ao}")
    
    # 盘口变动
    if ai.get('handicap') and ai['handicap'] != hcap:
        iv = conv_hcap(ai['handicap'])
        cv = hcap_val
        diff = abs(cv - iv)
        direction = "升盘" if abs(cv) > abs(iv) else "降盘"
        if diff > 0.25:
            conf += min(diff * 0.15, 0.15)
            signals.append(f"{direction}({iv}→{cv})")
        else:
            signals.append(f"{direction}({iv}→{cv})")
    
    result = 'home' if hcap_val > 0 else ('away' if hcap_val < 0 else 'draw')
    
    return {'result': result, 'confidence': min(conf, 0.75), 'signals': signals,
            'handicap': hcap_val, 'home_water': ho, 'away_water': ao,
            'handicap_text': hcap}


def factor_over_under(match):
    d3 = match.get('odds_3in1', {})
    ou = d3.get('overunder', {})
    oc = ou.get('current', {})
    oi = ou.get('initial', {})
    
    if not oc or not oc.get('line'):
        return {'prediction': None, 'confidence': 0, 'detail': 'No OU data'}
    
    try:
        line_parts = oc['line'].split('/')
        line = float(line_parts[0])
    except (ValueError, IndexError):
        return {'prediction': None, 'confidence': 0, 'detail': 'Parse error'}
    
    signals = []
    conf = 0
    
    # 盘口大小
    if line >= 3.5:
        conf += 0.40
        signals.append(f"大盘{line}")
    elif line >= 3.0:
        conf += 0.25
        signals.append(f"中大盘{line}")
    elif line <= 2.0:
        conf += 0.30
        signals.append(f"小盘{line}")
    elif line <= 2.25:
        conf += 0.15
        signals.append(f"偏小盘{line}")
    
    # 水位
    try:
        ovf = float(oc.get('over', '0'))
        unf = float(oc.get('under', '0'))
        if ovf < 0.80 and line >= 2.5:
            conf = min(conf + 0.20, 0.85)
            signals.append(f"大球低水{ovf}看好大球")
        elif ovf < 0.85 and line >= 2.5:
            conf += 0.10
            signals.append(f"大球低水{ovf}")
        elif unf < 0.80 and line <= 2.5:
            conf = min(conf + 0.20, 0.85)
            signals.append(f"小球低水{unf}看好小球")
        elif unf < 0.85 and line <= 2.5:
            conf += 0.10
            signals.append(f"小球低水{unf}")
    except:
        pass
    
    # 盘口变动
    if oi.get('line') and oi['line'] != oc['line']:
        try:
            ol_parts = oi['line'].split('/')
            ol = float(ol_parts[0])
            if ol != line:
                diff = abs(line - ol)
                dir_text = "升盘" if line > ol else "降盘"
                if diff >= 0.5:
                    conf = min(conf + 0.20, 0.85)
                    signals.append(f"{dir_text}↑{diff}(强烈信号)")
                else:
                    conf += 0.10
                    signals.append(f"{dir_text}↑{diff}")
        except:
            pass
    
    # 判断大/小
    if line >= 2.75 and conf > 0.2:
        prediction = 'over'
    elif line >= 2.5 and conf > 0.35:
        prediction = 'over'
    elif line <= 2.0 and conf > 0.2:
        prediction = 'under'
    elif line <= 2.25 and conf > 0.25:
        prediction = 'under'
    else:
        prediction = None
        # 中间盘口
        if line == 2.5:
            if ovf < unf:
                prediction = 'over'
                conf *= 0.6
            elif unf < ovf:
                prediction = 'under'
                conf *= 0.6
    
    return {'prediction': prediction, 'confidence': min(conf, 0.85), 'signals': signals,
            'line': line, 'over_water': oc.get('over', '0'), 'under_water': oc.get('under', '0')}


# ============ 庄家动机分析（诱盘/阻盘检测）============

def factor_bookmaker_intent(match):
    """
    庄家动机因子: 检测诱盘、阻盘、异常赔率结构
    
    核心逻辑:
      1. 欧赔vs亚盘背离 → 欧赔看好A队但亚盘浅开 → 诱盘信号
      2. 赔率异常稳定 → 该变动时不变动 → 庄家控盘
      3. 深盘高水 → 阻盘（庄家不想你买热门）
      4. 浅盘低水 → 诱盘（庄家引诱你买热门）
      5. 凯利指数异常 → 某项赔率价值异常
    
    Returns:
      dict with 'trap_detected', 'direction', 'confidence', 'signals'
    """
    d3 = match.get('odds_3in1', {})
    jc = match.get('jingcai', {})
    asian = d3.get('asian', {})
    euro = d3.get('euro', {})
    ac = asian.get('current', {})
    ai = asian.get('initial', {})
    ec = euro.get('current', {})
    ei = euro.get('initial', {})
    
    signals = []
    trap_score = 0  # 正=诱盘方向, 负=阻盘方向
    trap_conf = 0   # 诱盘信号强度
    trap_detected = False
    trap_direction = None  # 'home'=诱主, 'away'=诱客
    
    # ---- 检测1: 欧赔vs亚盘背离 ----
    if ec and ac and jc:
        try:
            # 欧赔隐含概率
            home_odds = float(ec.get('home', 0) or jc.get('home_win', 0))
            away_odds = float(ec.get('away', 0) or jc.get('away_win', 0))
            draw_odds = float(ec.get('draw', 0) or jc.get('draw', 0))
            
            if home_odds > 0 and away_odds > 0:
                total = 1/home_odds + 1/draw_odds + 1/away_odds
                home_prob = (1/home_odds) / total if total > 0 else 0
                away_prob = (1/away_odds) / total if total > 0 else 0
                
                hcap = ac.get('handicap', '平手')
                hcap_val = conv_hcap(hcap)
                
                # 欧赔看好主队(主胜概率>50%) 但亚盘浅开(让球<0.5) → 诱主
                if home_prob > 0.50 and hcap_val < 0.5:
                    trap_score += 1
                    trap_conf += 0.15
                    trap_detected = True
                    signals.append(f"诱盘⚡: 欧赔主胜{home_prob:.0%}但亚盘仅让{abs(hcap_val):.1f}球→浅开诱主")
                
                # 欧赔看好客队(客胜概率>50%) 但亚盘浅开(受让<0.5) → 诱客
                if away_prob > 0.50 and hcap_val > -0.5:
                    trap_score -= 1
                    trap_conf += 0.15
                    trap_detected = True
                    signals.append(f"诱盘⚡: 欧赔客胜{away_prob:.0%}但亚盘仅受让{abs(hcap_val):.1f}球→浅开诱客")
        except:
            pass
    
    # ---- 检测2: 赔率变动vs亚盘背离 ----
    if ai and ac and ei and ec:
        try:
            hcap_cur = conv_hcap(ac.get('handicap', '平手'))
            hcap_init = conv_hcap(ai.get('handicap', '平手'))
            ho = float(ac.get('home', 0.95))
            ao = float(ac.get('away', 0.95))
            
            # 欧赔主胜下降(看好主队) 但 亚盘不动或降盘 → 背离
            euro_home_init = float(ei.get('home', 0))
            euro_home_cur = float(ec.get('home', 0))
            euro_away_init = float(ei.get('away', 0))
            euro_away_cur = float(ec.get('away', 0))
            
            # 检测1: 欧赔降主胜 + 亚盘不升反降 → 诱主
            if (euro_home_cur < euro_home_init * 0.95) and (hcap_cur <= hcap_init):
                trap_score += 1
                trap_conf += 0.18
                trap_detected = True
                signals.append(f"诱盘⚡: 欧赔降主胜{abs(euro_home_cur-euro_home_init):.2f}但亚盘未升→诱主")
            
            # 检测2: 欧赔降客胜 + 亚盘不升(或降)受让 → 诱客
            if (euro_away_cur < euro_away_init * 0.95) and (hcap_cur >= hcap_init):
                trap_score -= 1
                trap_conf += 0.18
                trap_detected = True
                signals.append(f"诱盘⚡: 欧赔降客胜{abs(euro_away_cur-euro_away_init):.2f}但亚盘未动→诱客")
            
            # ---- 深盘高水(阻盘) vs 深盘低水(真实信心) ----
            # 深盘(>=1球) + 高水(>1.05) → 阻盘！庄家不想你买热门
            if hcap_cur >= 1.0 and ho > 1.05:
                trap_conf += 0.20
                trap_detected = True
                signals.append(f"阻盘🛡️: 深盘{abs(hcap_cur):.1f}球但主水{ho:.2f}高水阻盘→庄家防冷")
            
            if hcap_cur <= -1.0 and ao > 1.05:
                trap_conf += 0.20
                trap_detected = True
                signals.append(f"阻盘🛡️: 客让{abs(hcap_cur):.1f}球但客水{ao:.2f}高水阻盘→庄家防冷")
            
            # 盘口剧烈变动: 初盘vs即时盘差距>0.5球
            if abs(hcap_cur - hcap_init) >= 0.5:
                if hcap_cur > hcap_init:
                    # 大幅升盘(主队更看好)但主水未降 → 阻盘
                    if ho > 0.95:
                        trap_conf += 0.12
                        trap_detected = True
                        signals.append(f"阻盘🛡️: 盘口{hcap_init}→{hcap_cur}大幅升盘但主水{ho:.2f}未降→阻盘")
                elif hcap_cur < hcap_init:
                    # 大幅降盘(主队看衰)但客水未降 → 阻客
                    if ao > 0.95:
                        trap_conf += 0.12
                        trap_detected = True
                        signals.append(f"阻盘🛡️: 盘口{hcap_init}→{hcap_cur}大幅降盘但客水{ao:.2f}未降→阻客")
        except:
            pass
    
    # ---- 检测3: 欧赔vs亚盘静态背离（浅盘低水诱盘）----
    if ac and jc:
        try:
            hcap_val = conv_hcap(ac.get('handicap', '平手'))
            ho = float(ac.get('home', 0.95))
            ao = float(ac.get('away', 0.95))
            
            # 浅盘(平手/平半) + 低水(<0.85) → 诱盘！庄家引诱买热门
            if 0 < hcap_val <= 0.25 and ho < 0.85:
                trap_conf += 0.15
                trap_detected = True
                signals.append(f"诱盘🎣: 浅让{hcap_val:.1f}球但主水{ho:.2f}低水→庄家诱导")
            
            if -0.25 <= hcap_val < 0 and ao < 0.85:
                trap_conf += 0.15
                trap_detected = True
                signals.append(f"诱盘🎣: 浅受让{abs(hcap_val):.1f}球但客水{ao:.2f}低水→庄家诱导")
        except:
            pass
    
    # ---- 检测3: 赔率异常稳定（该变动时不变动）----
    if ei and ec:
        try:
            changes = 0
            for key in ['home', 'draw', 'away']:
                init_val = float(ei.get(key, 0))
                curr_val = float(ec.get(key, 0))
                if init_val > 0 and curr_val > 0:
                    change_pct = abs(curr_val - init_val) / init_val
                    if change_pct > 0.03:
                        changes += 1
            
            # 如果所有赔率几乎没变但比赛临近 → 庄家控盘信号
            if changes == 0:
                trap_conf += 0.05
                signals.append("赔率异常稳定→庄家高度控盘")
        except:
            pass
    
    # ---- 检测4: 凯利指数异常 ----
    if jc:
        try:
            h = float(jc.get('home_win', 0))
            d = float(jc.get('draw', 0))
            a = float(jc.get('away_win', 0))
            if h > 0 and d > 0 and a > 0:
                total_p = 1/h + 1/d + 1/a
                if total_p > 1:
                    margin = (total_p - 1) * 100  # 庄家抽水%
                    if margin > 8:
                        signals.append(f"高抽水{margin:.1f}%→庄家风险规避，信心不足")
                        trap_conf += 0.08
                    elif margin < 3:
                        signals.append(f"低抽水{margin:.1f}%→庄家让利，真实看好")
        except:
            pass
    
    # ---- 综合判断 ----
    if trap_score > 0:
        trap_direction = 'home'  # 诱主（实际看好客队）
    elif trap_score < 0:
        trap_direction = 'away'  # 诱客（实际看好主队）
    
    trap_conf = min(trap_conf, 0.50)
    
    return {
        'trap_detected': trap_detected,
        'trap_direction': trap_direction,  # 诱盘方向（反着买）
        'confidence': trap_conf,
        'signals': signals,
        'trap_score': trap_score,
    }


# ============ 综合预测引擎 ============

def ensemble_predict(match, team_stats=None, stage='小组赛'):
    """多因子集成预测（v3 — 动态权重 + Elo融合）
    
    Args:
        match: 比赛数据
        team_stats: 小组积分榜数据
        stage: 比赛阶段 (小组赛/16强/8强/半决赛/决赛)
    """
    
    home = match.get('home_team', 'Home')
    away = match.get('away_team', 'Away')
    jc = match.get('jingcai', {})
    
    # ---- 1. 六重因子 ----
    factors = {
        'odds_implied': factor_odds_implied(match),
        'odds_movement': factor_odds_movement(match),
        'asian_handicap': factor_asian_handicap(match),
        'over_under': factor_over_under(match),
        'motivation': factor_motivation(home, away, team_stats or {}),
        'bookmaker_intent': factor_bookmaker_intent(match),
    }
    
    # ---- 2. 动态权重（根据阶段自动调整）----
    # 从积分榜获取状态信息
    hs = team_stats.get(home, {}) if team_stats else {}
    ha = team_stats.get(away, {}) if team_stats else {}
    h_form = min((hs.get('pts', 3) / max(hs.get('mp', 1), 1)) / 3.0, 1.0) if hs else 0.5
    a_form = min((ha.get('pts', 3) / max(ha.get('mp', 1), 1)) / 3.0, 1.0) if ha else 0.5
    
    dyn_weights = get_dynamic_weights(stage, h_form, a_form)
    
    votes = {'home': 0.0, 'draw': 0.0, 'away': 0.0}
    used_factors = []
    detail_lines = []
    
    for f_name, f_data in factors.items():
        if f_name == 'over_under':
            continue  # OU单独处理
        result = f_data.get('result')
        conf = f_data.get('confidence', 0)
        weight = dyn_weights.get(f_name, 0.1)
        if result and conf > 0.01:
            votes[result] += conf * weight
            used_factors.append(f_name)
            detail_lines.append(f"{f_name}: ->{result} (cf={conf:.2f}, w={weight:.2f})")
    
    # ---- 2b. Elo因子（进入投票系统）----
    elo_h = get_elo(home)
    elo_a = get_elo(away)
    elo_exp = expected_score(elo_h, elo_a)
    elo_weight = dyn_weights.get('elo', 0.05)
    used_factors.append('elo')
    
    if elo_exp > 0.6:
        votes['home'] += elo_weight * (elo_exp - 0.3)
        detail_lines.append(f"elo: {home}({elo_h}) vs {away}({elo_a}) ->home (exp={elo_exp:.2f}, w={elo_weight:.2f})")
    elif elo_exp < 0.4:
        votes['away'] += elo_weight * (0.7 - elo_exp)
        detail_lines.append(f"elo: {home}({elo_h}) vs {away}({elo_a}) ->away (exp={elo_exp:.2f}, w={elo_weight:.2f})")
    else:
        detail_lines.append(f"elo: {home}({elo_h}) vs {away}({elo_a}) ->drawish (exp={elo_exp:.2f}, w={elo_weight:.2f})")
    
    # ---- 3. 战意详情 & 进球乘数 ----
    mot = factors.get('motivation', {})
    goal_multiplier = mot.get('goal_multiplier', 1.0)
    hi = mot.get('home_intensity', 1.0)
    ai = mot.get('away_intensity', 1.0)
    mot_analysis = mot.get('analysis', [])
    
    # ★优化v2: 战意数据已统一在factor_motivation中处理，此处不再重复计算
    # 直接使用factor_motivation返回的平局倾向度和战意评分
    hm = mot.get('home_motive', '')
    am = mot.get('away_motive', '')
    draw_tendency = mot.get('draw_tendency', 0)
    mot_score = mot.get('motivation_score', {'home': 0, 'away': 0})
    
    # 平局倾向投票修正
    if draw_tendency > 0.3:
        votes['draw'] += draw_tendency * 0.12
        detail_lines.append(f"motivation: draw_tendency={draw_tendency:.2f}, 平局修正")
    
    # 战意主导：当一方战意远强于另一方时修正投票
    mot_diff = mot_score.get('home', 0) - mot_score.get('away', 0)
    if abs(mot_diff) >= 3:
        favored = 'home' if mot_diff > 0 else 'away'
        votes[favored] += 0.06
        detail_lines.append(f"motivation: 战意差距{mot_diff}→ favore {favored}(+0.06)")
    
    # ---- 庄家动机因子 - 诱盘/阻盘检测 ★v3 ----
    bmi = factors.get('bookmaker_intent', {})
    if bmi.get('trap_detected') or bmi.get('signals'):
        trap_dir = bmi.get('trap_direction')
        trap_conf = bmi.get('confidence', 0)
        trap_score_val = bmi.get('trap_score', 0)
        trap_signals = bmi.get('signals', [])
        has_阻盘 = any('阻盘' in s for s in trap_signals)
        has_诱盘 = any('诱盘' in s for s in trap_signals)
        
        for sig in trap_signals:
            detail_lines.append(f"庄家动机: {sig}")
        
        # ---- 诱盘处理：方向相反 ----
        if has_诱盘 and trap_dir == 'home':
            # 庄家诱导买主队 → 实际看好客队
            votes['home'] = max(votes['home'] - trap_conf * 0.5, 0)
            votes['away'] = min(votes['away'] + trap_conf * 0.4, 1.0)
            detail_lines.append(f"庄家动机: 🎣诱主→反买客, 修正{trap_conf:.2f}")
        elif has_诱盘 and trap_dir == 'away':
            # 庄家诱导买客队 → 实际看好主队
            votes['away'] = max(votes['away'] - trap_conf * 0.5, 0)
            votes['home'] = min(votes['home'] + trap_conf * 0.4, 1.0)
            detail_lines.append(f"庄家动机: 🎣诱客→反买主, 修正{trap_conf:.2f}")
        
        # ---- 阻盘处理：庄家防范 = 真实看好 ----
        for sig in trap_signals:
            if '阻盘' in sig:
                # 阻盘 = 庄家不想你买这一方 = 实际看好这一方
                if '主' in sig:
                    # 阻主 = 庄家看好主队
                    votes['home'] = min(votes['home'] + 0.08, 1.0)
                    detail_lines.append(f"庄家动机: 🛡️阻主→庄家有信心(+0.08)")
                elif '客' in sig:
                    # 阻客 = 庄家看好客队
                    votes['away'] = min(votes['away'] + 0.08, 1.0)
                    detail_lines.append(f"庄家动机: 🛡️阻客→庄家有信心(+0.08)")
                break  # 只处理第一个阻盘信号
        
        # 高抽水 = 庄家规避风险
        high_margin = any('高抽水' in s for s in trap_signals)
        if high_margin and not has_阻盘 and not has_诱盘:
            # 仅有高抽水信号 -> 轻微降低所有自信度
            for k in votes:
                votes[k] *= 0.95
            detail_lines.append(f"庄家动机: 高抽水-整体降信5%")
        
        used_factors.append('bookmaker_intent')
    
    # ---- smart冷门检测 ★优化v2 ----
    # 当Elo差距巨大但弱队战意极强时，降低热门置信度
    elo_gap = elo_h - elo_a
    hm_type = mot.get('home_motive', '')
    am_type = mot.get('away_motive', '')
    
    # Elo落后方有强烈战意 -> 可能爆冷
    if elo_gap > 80 and am_type in ('背水一战', '绝境求生', '主动进取'):
        # 客队Elo低但战意强
        votes['away'] = min(votes['away'] + 0.04, 1.0)
        detail_lines.append(f"冷门检测: 客队Elo低{elo_gap:.0f}分但战意({am_type})强→防冷(+0.04)")
    if elo_gap < -80 and hm_type in ('背水一战', '绝境求生', '主动进取'):
        votes['home'] = min(votes['home'] + 0.04, 1.0)
        detail_lines.append(f"冷门检测: 主队Elo低{abs(elo_gap):.0f}分但战意({hm_type})强→防冷(+0.04)")
    
    # Elo领先方已出线/荣誉之战 -> 可能松懈
    if elo_gap > 100 and hm_type in ('已出线', '荣誉之战'):
        votes['home'] = max(votes['home'] - 0.05, 0)
        votes['away'] = min(votes['away'] + 0.03, 1.0)
        detail_lines.append(f"冷门检测: 主队领先Elo{elo_gap:.0f}分但已无欲无求→防冷(-0.05)")
    if elo_gap < -100 and am_type in ('已出线', '荣誉之战'):
        votes['away'] = max(votes['away'] - 0.05, 0)
        votes['home'] = min(votes['home'] + 0.03, 1.0)
        detail_lines.append(f"冷门检测: 客队领先Elo{abs(elo_gap):.0f}分但已无欲无求→防冷(-0.05)")
    
    # ---- 4. 最终赛果判定 ----
    final_result = max(votes, key=votes.get) if max(votes.values()) > 0 else None
    max_vote = max(votes.values())
    second_vote = sorted(votes.values())[-2] if len(votes) > 2 else 0
    sorted_vals = sorted(votes.values(), reverse=True)
    second_vote = sorted_vals[1] if len(sorted_vals) > 1 else 0
    
    if max_vote > 0:
        confidence = min(max_vote / (max_vote + second_vote) * 0.9, 0.92) if max_vote + second_vote > 0 else 0.5
        # 如果只有一个因子发声则降权
        if len(used_factors) <= 1 and confidence > 0.5:
            confidence = max(0.1, confidence * 0.6)
            detail_lines.append("(only 1 factor, confidence reduced)")
    else:
        confidence = 0.0
    
    result_text = {'home': f'{home}胜', 'draw': '平局', 'away': f'{away}胜'}.get(final_result, '不确定')
    
    # ---- 5. 比分预测 ----
    hcap_data = factors['asian_handicap']
    hcap_val = hcap_data.get('handicap', 0)
    ou_data = factors['over_under']
    line = ou_data.get('line', 2.5)
    ou_pred = ou_data.get('prediction', None)
    
    # 检查是否有竞彩赔率
    home_odds = jc.get('home_win', 2.0)
    away_odds = jc.get('away_win', 2.0)
    draw_odds = jc.get('draw', 2.0)
    
    # 战意进球乘数
    adjusted_line = line
    if goal_multiplier < 0.85:
        # 战意压制进球 -> 大小球降档
        adjusted_line = line + 0.25  # 盘口变相升高=更看好小球
    elif goal_multiplier > 1.15:
        adjusted_line = max(line - 0.25, 1.5)  # 盘口降低=更看好大球
    
    sm = predict_score(home_odds, away_odds, home, away, hcap_val, adjusted_line, ou_pred, final_result)
    
    # 战意修正期望进球
    sm_adj = dict(sm)
    sm_adj['expected_home_goals'] = round(sm['expected_home_goals'] * hi, 2)
    sm_adj['expected_away_goals'] = round(sm['expected_away_goals'] * ai, 2)
    sm = sm_adj
    
    # ---- 6. 大小球预测（战意修正） ----
    ou_text = '不确定'
    ou_conf = ou_data.get('confidence', 0)
    if ou_pred == 'over':
        ou_text = f"大球(>{line})"
    elif ou_pred == 'under':
        ou_text = f"小球(<{line})"
    
    # ---- 7. 半全场预测 ----
    htft = predict_htft(home_odds, draw_odds, away_odds, confidence, final_result)
    
    # ---- 8. 推荐 ----
    recommendations = []
    if confidence > 0.65:
        rec_stars = min(int(confidence * 5), 5)
        recommendations.append({
            'type': '胜负', 'pick': result_text,
            'confidence': f"{confidence:.0%}", 'stars': rec_stars,
            'note': ''
        })
    
    if ou_conf > 0.55 and ou_pred:
        ou_stars = min(int(ou_conf * 5), 5)
        recommendations.append({
            'type': '大小球', 'pick': ou_text,
            'confidence': f"{ou_conf:.0%}", 'stars': ou_stars,
            'note': ''
        })
    
    # HT/FT推荐
    if htft['probability'] > 0.4:
        recommendations.append({
            'type': '半全场', 'pick': htft['most_likely'],
            'confidence': f"{htft['probability']:.0%}",
            'stars': min(int(htft['probability'] * 5), 5),
            'note': htft['description']
        })
    
    # 比分推荐（仅高置信度时）
    if confidence > 0.55:
        recommendations.append({
            'type': '比分', 'pick': sm['most_likely'],
            'confidence': f"{confidence:.0%}",
            'stars': min(int(confidence * 3), 3),
            'note': ''
        })
    
    return {
        'match': f"{home} vs {away}",
        'match_no': match.get('match_no', ''),
        'home_team': home, 'away_team': away,
        'schedule_id': match.get('schedule_id', ''),
        
        'result_prediction': {
            'prediction': result_text,
            'value': final_result,
            'confidence': round(confidence, 2),
            'votes': {k: round(v, 3) for k, v in votes.items()},
        },
        'score_prediction': {
            'most_likely': sm['most_likely'],
            'home_goals': sm['home_goals'],
            'away_goals': sm['away_goals'],
            'alternatives': sm['alternatives'],
            'expected_home_goals': sm['expected_home_goals'],
            'expected_away_goals': sm['expected_away_goals'],
        },
        'ht_prediction': {
            'score': sm['ht_prediction'],
            'home_goals': sm['ht_home_goals'],
            'away_goals': sm['ht_away_goals'],
        },
        'htft_prediction': {
            'most_likely': htft['most_likely'],
            'probability': htft['probability'],
            'alternatives': htft['alternatives'],
            'description': htft['description'],
            'narrative': htft['narrative'],
        },
        'over_under_prediction': {
            'prediction': ou_text,
            'value': ou_pred,
            'confidence': round(ou_conf, 2),
            'line': line,
        },
        'recommendations': recommendations,
        'factors_detail': used_factors,
        'factor_analysis': detail_lines,
        'motivation_analysis': mot_analysis,
        'goal_multiplier': round(goal_multiplier, 2),
        'home_intensity': round(hi, 2),
        'away_intensity': round(ai, 2),
        'home_motive': hm,
        'away_motive': am,
        'elo': {'home': elo_h, 'away': elo_a, 'expected': round(elo_exp, 3)},
        'dynamic_weights': {k: round(v, 3) for k, v in dyn_weights.items()},
        'stage': stage,
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }


def predict_all(matches, team_stats=None, stage='小组赛'):
    predictions = []
    for m in matches:
        try:
            pred = ensemble_predict(m, team_stats, stage)
            predictions.append(pred)
        except Exception as e:
            print(f"  Fail {m.get('match_no', '?')}: {e}")
    return predictions


# ============ 保存 ============

def save_predictions(predictions):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    record = {
        'predict_time': now.strftime('%Y-%m-%d %H:%M:%S'),
        'predict_date': now.strftime('%Y-%m-%d'),
        'stage': 'Group Stage Round 3',
        'total_matches': len(predictions),
        'predictions': predictions,
    }
    fp = DATA_DIR / f"predictions-{now.strftime('%Y%m%d')}.json"
    with open(fp, 'w', encoding='utf-8') as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
    with open(DATA_DIR / "predictions-latest.json", 'w', encoding='utf-8') as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
    return fp


def load_matches(filepath=None):
    if filepath:
        fp = Path(filepath)
    else:
        fp = DATA_DIR / "odds-latest.json"
    if not fp.exists():
        print(f"No data file: {fp}")
        print("Run python scripts/fetch-odds.py first")
        return None
    with open(fp, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return data.get('matches', [])


# ============ 精美输出 ============

def print_summary(predictions, team_stats=None):
    W = 94
    total = len(predictions)
    
    high_p = [p for p in predictions if p['result_prediction']['confidence'] >= 0.70]
    mid_p = [p for p in predictions if 0.40 <= p['result_prediction']['confidence'] < 0.70]
    low_p = [p for p in predictions if p['result_prediction']['confidence'] < 0.40]
    
    home_wins = sum(1 for p in predictions if p['result_prediction'].get('value') == 'home')
    away_wins = sum(1 for p in predictions if p['result_prediction'].get('value') == 'away')
    draws = sum(1 for p in predictions if p['result_prediction'].get('value') == 'draw')
    unsure = sum(1 for p in predictions if p['result_prediction'].get('value') is None)
    
    ts = predictions[0]['timestamp'] if predictions else 'N/A'
    
    print(f"\n╔{'═'*W}╗")
    print(f"║{pad_to('🏆  绿 茵 神 算  v2 · 2026 世 界 杯 第 3 轮 精 准 预 测', W)}║")
    print(f"║{pad_to(f'📅 {ts}  |  共 {total} 场  |  小组赛第3轮', W)}║")
    print(f"╚{'═'*W}╝")
    
    # ---- 高置信度 ----
    if high_p:
        print(f"\n┌{'─'*W}┐")
        print(f"│{pad_to('🔥🔥 高置信度推荐（≥70%）', W-4)} {len(high_p)} 场 │")
        print(f"├{'─'*W}┤")
        for i, p in enumerate(high_p):
            rp = p['result_prediction']
            sp = p['score_prediction']
            op = p['over_under_prediction']
            hp = p['htft_prediction']
            ht = p['ht_prediction']
            no = p.get('match_no', '??')
            cv = rp['confidence']
            ov = op.get('confidence', 0)
            htv = hp['probability']
            
            # 比分
            sc = sp['most_likely']
            
            print(f"│{pad_to(f'│ {no}  {p["home_team"]} 🆚 {p["away_team"]}', W-2)} │")
            print(f"│{pad_to(f'  🏆  预测: {rp["prediction"]}', W-2)} │")
            print(f"│{pad_to(f'  ⚽  比分: {sc}  |  半场: {ht["score"]}', W-2)} │")
            print(f"│{pad_to(f'  🔄  半全场: {hp["most_likely"]}({hp["probability"]:.0%})  |  {hp["description"]}', W-2)} │")
            
            # 置信度条
            print(f"│{pad_to(f'      置信度 ', W-2)}│")
            print(f"│{pad_to(f'      {"赛果 " + bar_full(cv, 20) + f" {cv:.0%}":<60s}', W-2)}│")
            if ov > 0.4:
                print(f"│{pad_to(f'      {"大小球 " + bar_full(ov, 20) + f" {ov:.0%}":<60s}', W-2)}│")
            if htv > 0.3:
                print(f"│{pad_to(f'      {"半全场 " + bar_full(htv, 20) + f" {htv:.0%}":<60s}', W-2)}│")
            
            # 推荐
            recs = p.get('recommendations', [])
            if recs:
                print(f"│{pad_to(f'  {"":8s}{"💡 推荐:"}', W-2)}│")
                for r in recs[:3]:
                    print(f"│{pad_to(f'  {"":10s}{stars(r.get("stars", 3)/5)}  {r["type"]}: {r["pick"]}  (可信{r["confidence"]})', W-2)}│")
            
            # 基本面
            print(f"│{pad_to(f'  {"":8s}📊 赔率深度解析', W-2)}│")
            
            if i < len(high_p) - 1:
                print(f"├{'─'*W}┤")
        print(f"└{'─'*W}┘")
    
    # ---- 中等置信度 ----
    if mid_p:
        print(f"\n┌{'─'*W}┐")
        print(f"│{pad_to('⚡⚡ 参考预测（40-70%）', W-4)} {len(mid_p)} 场 │")
        print(f"├{'─'*W}┤")
        for i, p in enumerate(mid_p):
            rp = p['result_prediction']
            sp = p['score_prediction']
            hp = p['htft_prediction']
            no = p.get('match_no', '??')
            cv = rp['confidence']
            sc = sp['most_likely']
            htft_s = f'| HT/FT: {hp["most_likely"]}' if hp['probability'] > 0.3 else ''
            
            print(f"│{pad_to(f'{no}  {p["home_team"]} 🆚 {p["away_team"]}', W-2)} │")
            print(f"│{pad_to(f'  🏆 {rp["prediction"]}  {bar_full(cv,15)} {cv:.0%}  ⚽ {sc}  {htft_s}', W-2)}│")
            
            recs = p.get('recommendations', [])
            if recs:
                rs = '  |  '.join([f'{r["type"]}:{r["pick"]}' for r in recs[:2]])
                print(f"│{pad_to(f'  💡 {rs}', W-2)}│")
            
            if i < len(mid_p) - 1:
                print(f"├{'─'*W}┤")
        print(f"└{'─'*W}┘")
    
    # ---- 低置信度 ----
    if low_p:
        print(f"\n┌{'─'*W}┐")
        print(f"│{pad_to('💤 低置信度观察（<40%）', W-4)} {len(low_p)} 场 │")
        print(f"├{'─'*W}┤")
        # 2列
        items = [f"{p['match_no']} {p['home_team']}vs{p['away_team']}  {p['result_prediction']['prediction']}({p['result_prediction']['confidence']:.0%})" for p in low_p]
        for j in range(0, len(items), 2):
            chunk = items[j:j+2]
            print(f"│{pad_to('  ' + '  ||  '.join(chunk), W-2)} │")
        print(f"└{'─'*W}┘")
    
    # ---- 统计面板 ----
    print(f"\n┌{'─'*W}┐")
    print(f"│{pad_to('📊 预测总览', W-2)}│")
    print(f"├{'─'*W}┤")
    
    # 分布条
    dist = {'主胜': home_wins, '平局': draws, '客胜': away_wins, '不确定': unsure}
    dist_bar = '  '.join([f'{k}: {v:>2d}' for k, v in dist.items()])
    print(f"│{pad_to(f'  🏆  {dist_bar}', W-2)}│")
    
    # 置信度分布图
    conf_parts = [
        ('高(≥70%)', len(high_p), '🟢'),
        ('中(40-70%)', len(mid_p), '🟡'),
        ('低(<40%)', len(low_p), '🔴'),
    ]
    total_p = total if total > 0 else 1
    bar_len = 40
    bar_parts = []
    for label, count, emoji in conf_parts:
        cnt = int(count / total_p * bar_len)
        if cnt > 0:
            bar_parts.append(f"{emoji}{'█'*cnt}")
    if bar_parts:
        print(f"│{pad_to(f'  📈  置信度: {"".join(bar_parts)}{"░"*max(0, bar_len-sum(c//total_p*bar_len for _,c,_ in conf_parts))}', W-2)}│")
    print(f"│{pad_to(f'      {"    ".join([f"{e}{l}: {c}场" for l,c,e in conf_parts])}', W-2)}│")
    
    # 赛事统计
    over_games = sum(1 for p in predictions if p['over_under_prediction'].get('value') == 'over')
    under_games = sum(1 for p in predictions if p['over_under_prediction'].get('value') == 'under')
    htft_stats = {}
    for p in predictions:
        h = p['htft_prediction']['most_likely']
        htft_stats[h] = htft_stats.get(h, 0) + 1
    
    print(f"│{pad_to(f'  ⚽  大小球: 大球{over_games}场  |  小球{under_games}场  |  待定{total-over_games-under_games}场', W-2)}│")
    htft_summary = '  '.join([f'{k}:{v}' for k, v in sorted(htft_stats.items(), key=lambda x:-x[1])[:5]])
    if htft_summary:
        print(f"│{pad_to(f'  🔄  半全场趋势: {htft_summary}', W-2)}│")
    
    print(f"├{'─'*W}┤")
    print(f"│{pad_to('💡 预测说明: 基于欧赔/亚盘/大小球多因子集成+小组赛基本面分析', W-2)}│")
    print(f"│{pad_to('   半全场预测使用历史统计模式匹配，仅供参考', W-2)}│")
    print(f"│{pad_to('   足球比赛不确定性大，建议结合更多信息综合判断', W-2)}│")
    print(f"└{'─'*W}┘")
    print()


def print_detailed_card(p):
    """单场比赛详细分析"""
    W = 94
    rp = p['result_prediction']
    sp = p['score_prediction']
    op = p['over_under_prediction']
    hp = p['htft_prediction']
    ht = p['ht_prediction']
    no = p.get('match_no', '??')
    
    print(f"\n┌{'─'*W}┐")
    print(f"│{pad_to(f'📋 {no}  {p["home_team"]} 🆚 {p["away_team"]}  |  详细分析', W-2)}│")
    print(f"├{'─'*W}┤")
    
    # 赛果
    cv = rp['confidence']
    print(f"│{pad_to(f'  🏆 赛果预测: {rp["prediction"]}  {"█"*int(cv*25)+"░"*(25-int(cv*25))} {cv:.0%}', W-2)}│")
    
    # 比分
    print(f"│{pad_to(f'  ⚽ 比分预测: {sp["most_likely"]}', W-2)}│")
    if sp['alternatives']:
        alts = '  |  '.join(sp['alternatives'][:3])
        print(f"│{pad_to(f'     备选: {alts}', W-2)}│")
    
    # 半场
    print(f"│{pad_to(f'  ⏱ 半场预测: {ht["score"]}', W-2)}│")
    
    # 半全场
    htv = hp['probability']
    print(f"│{pad_to(f'  🔄 半全场: {hp["most_likely"]}({hp["probability"]:.0%})  |  {hp["description"]}  |  备选: {",".join(hp["alternatives"])}', W-2)}│")
    
    # 大小球
    ov = op.get('confidence', 0)
    if ov > 0:
        print(f"│{pad_to(f'  📐 大小球: {op["prediction"]}  {"█"*int(ov*20)+"░"*(20-int(ov*20))} {ov:.0%}', W-2)}│")
    
    print(f"├{'─'*W}┤")
    print(f"│{pad_to('  ⭐ 推荐评级', W-2)}│")
    for r in p.get('recommendations', []):
        print(f"│{pad_to(f'    {stars(r["stars"]/5, 5)}  {r["type"]}: {r["pick"]}  (可信度{r["confidence"]})  {r.get("note","")}', W-2)}│")
    
    # Elo评分
    elo = p.get('elo', {})
    if elo:
        print(f"├{'─'*W}┤")
        elo_h = elo.get('home', 0)
        elo_a = elo.get('away', 0)
        elo_e = elo.get('expected', 0.5)
        elo_adv = f"{p['home_team']}胜率{elo_e:.0%}" if elo_e > 0.6 else (f"{p['away_team']}胜率{1-elo_e:.0%}" if elo_e < 0.4 else "实力接近")
        print(f"│{pad_to(f'  📊 Elo: {p["home_team"]}({elo_h}) vs {p["away_team"]}({elo_a})  |  {elo_adv}', W-2)}│")
    
    # 因子详情
    fd = p.get('factor_analysis', [])
    if fd:
        print(f"│{pad_to(f'  🔬 因子详情', W-2)}│")
        for dl in fd[:5]:
            print(f"│{pad_to(f'    {dl}', W-2)}│")
    
    print(f"└{'─'*W}┘")


# ============ 主流程 ============

def main():
    parser = argparse.ArgumentParser(description='绿茵神算 预测引擎 v3')
    parser.add_argument('--file', help='从指定JSON文件加载赔率数据')
    parser.add_argument('--stage', default='小组赛', 
                        choices=['小组赛', '16强', '8强', '半决赛', '决赛'],
                        help='比赛阶段（影响权重分配）')
    args = parser.parse_args()
    
    matches = load_matches(args.file)
    if not matches:
        return
    
    print(f"📊 加载 {len(matches)} 场比赛数据")
    print(f"🏟️  比赛阶段: {args.stage}")
    print("📡 加载小组赛基本面...")
    team_stats = load_group_standings()
    if team_stats:
        print(f"📋 {len(team_stats)} 支球队数据加载成功")
    
    print("🧠 多因子模型预测中...")
    predictions = predict_all(matches, team_stats, args.stage)
    fp = save_predictions(predictions)
    
    print(f"💾 已保存至 {fp.name}")
    
    # 输出
    print_summary(predictions, team_stats)
    
    # 高置信度比赛详细卡
    high_p = [p for p in predictions if p['result_prediction']['confidence'] >= 0.70]
    for p in high_p:
        print_detailed_card(p)
    
    # 中等置信度详细卡
    mid_p = [p for p in predictions if 0.40 <= p['result_prediction']['confidence'] < 0.70]
    for p in mid_p:
        print_detailed_card(p)


if __name__ == "__main__":
    main()
