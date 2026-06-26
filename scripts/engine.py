#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
⚽ 绿茵神算 v4 · 统一核心引擎

============================================================
  架构: MatchContext 贯穿全流程
============================================================

  原始赔率数据
       │
       ▼
  ┌─────────────────────────────────────┐
  │  Layer 1: 特征提取                   │
  │  ├─ odds_features  (赔率隐含概率+变动) │
  │  ├─ asian_features (亚盘深度+水位)     │
  │  ├─ ou_features    (大小球)           │
  │  ├─ elo_features   (Elo+攻防分解)     │
  │  └─ motivation     (战意分析)         │
  └──────────────┬──────────────────────┘
                 │ 结构化数据 (不是字符串!)
                 ▼
  ┌─────────────────────────────────────┐
  │  Layer 2: 庄家动机检测               │
  │  ├─ trap_detected: bool             │
  │  ├─ trap_direction: 'home'/'away'   │
  │  ├─ trap_confidence: 0.0~1.0        │
  │  ├─ anti_trap_pick: 反着买的方向     │
  │  └─ signals: [结构化信号对象]         │  ← 不是文本!
  └──────────────┬──────────────────────┘
                 │ 结构化数据
                 ▼
  ┌─────────────────────────────────────┐
  │  Layer 3: 赛果预测 (用全部数据)       │
  │  ├─ 7因子集成投票                     │
  │  ├─ 庄家修正: 诱盘→反买              │
  │  └─ 输出: {value, confidence, votes} │
  └──────────────┬──────────────────────┘
                 │
                 ▼
  ┌─────────────────────────────────────┐
  │  Layer 4: 比分预测 (用全部数据!)      │
  │  ├─ 输入: 赛果 + 庄家意图 + 期望进球  │
  │  ├─ 庄家修正: 诱盘→冷门比分权重↑     │
  │  ├─ 双变量泊松 + Dixon-Coles         │
  │  └─ 输出: {most_likely, alternatives} │
  └──────────────┬──────────────────────┘
                 │
                 ▼
  ┌─────────────────────────────────────┐
  │  最终输出                            │
  │  包含: 赛果+比分+庄家+置信度          │
  └─────────────────────────────────────┘

============================================================
"""

import json
import math
import sys
import os
from datetime import datetime
from pathlib import Path
from math import exp, factorial

# ================================================================
# 路径配置
# ================================================================

SCRIPT_DIR = Path(__file__).parent.resolve()
PROJECT_DIR = SCRIPT_DIR.parent
DATA_DIR = PROJECT_DIR / "data"

# ================================================================
# 常量
# ================================================================

WORLD_CUP_AVG_GOALS = 2.8
HOME_ADVANTAGE = 1.10
DIXON_COLES_RHO = 0.10
BIVARIATE_COV = 0.08

STAGE_WEIGHTS = {
    '小组赛':  {'odds_implied': 0.35, 'odds_movement': 0.12, 'asian_handicap': 0.22, 'over_under': 0.08, 'motivation': 0.12, 'elo': 0.11},
    '16强':    {'odds_implied': 0.28, 'odds_movement': 0.08, 'asian_handicap': 0.17, 'over_under': 0.07, 'motivation': 0.10, 'elo': 0.30},
    '8强':     {'odds_implied': 0.22, 'odds_movement': 0.06, 'asian_handicap': 0.15, 'over_under': 0.06, 'motivation': 0.08, 'elo': 0.43},
    '半决赛':  {'odds_implied': 0.18, 'odds_movement': 0.04, 'asian_handicap': 0.12, 'over_under': 0.05, 'motivation': 0.05, 'elo': 0.56},
    '决赛':    {'odds_implied': 0.15, 'odds_movement': 0.03, 'asian_handicap': 0.12, 'over_under': 0.05, 'motivation': 0.05, 'elo': 0.60},
    'default': {'odds_implied': 0.32, 'odds_movement': 0.10, 'asian_handicap': 0.20, 'over_under': 0.08, 'motivation': 0.12, 'elo': 0.18},
}

# Elo初始值 (同predict.py)
INITIAL_ELO_BASE = {
    '阿根廷': 1790, '西班牙': 1750, '法国': 1780, '英格兰': 1760, '巴西': 1770,
    '德国': 1680, '葡萄牙': 1650, '荷兰': 1660, '乌拉圭': 1640,
    '克罗地亚': 1630, '摩洛哥': 1620, '哥伦比亚': 1610, '日本': 1600, '挪威': 1590,
    '美国': 1580, '墨西哥': 1590, '加拿大': 1550,
    '瑞士': 1570, '韩国': 1560, '比利时': 1580, '塞内加尔': 1550,
    '厄瓜多尔': 1540, '埃及': 1540, '澳大利亚': 1530, '苏格兰': 1520, '土耳其': 1520,
    '捷克': 1510, '波黑': 1500, '卡塔尔': 1480, '巴拉圭': 1510,
    '科特迪瓦': 1500, '突尼斯': 1490, '伊朗': 1500, '新西兰': 1470,
    '沙特阿拉伯': 1480, '阿尔及利亚': 1500, '加纳': 1490, '巴拿马': 1460,
    '伊拉克': 1450, '乌兹别克斯坦': 1460, '约旦': 1450, '南非': 1480,
    '海地': 1430, '库拉索': 1400, '佛得角': 1430, '刚果金': 1440,
    '奥地利': 1530, '瑞典': 1550,
    '阿尔及利': 1500, '乌兹别克': 1460,
}

TEAM_ALIASES = {'阿尔及利': '阿尔及利亚', '乌兹别克': '乌兹别克斯坦'}

# 球队风格映射 (进攻系数, 防守系数)
TEAM_STYLE = {
    '阿根廷': (1.12, 0.88), '巴西': (1.15, 0.85), '法国': (1.10, 0.90),
    '英格兰': (1.08, 0.92), '荷兰': (1.10, 0.90), '日本': (1.08, 0.92),
    '德国': (1.08, 0.92), '葡萄牙': (1.10, 0.90), '西班牙': (1.06, 0.94),
    '挪威': (1.12, 0.88), '乌拉圭': (1.05, 0.95), '厄瓜多尔': (1.07, 0.93),
    '摩洛哥': (0.90, 1.10), '美国': (0.92, 1.08), '克罗地亚': (0.88, 1.12),
    '伊朗': (0.85, 1.15), '比利时': (0.95, 1.05), '澳大利亚': (0.90, 1.10),
    '塞内加尔': (0.92, 1.08), '加拿大': (0.95, 1.05), '巴拉圭': (0.90, 1.10),
    '墨西哥': (0.95, 1.05), '韩国': (1.05, 0.95), '突尼斯': (0.88, 1.12),
    '瑞士': (0.85, 1.15), '瑞典': (0.92, 1.08),
}


# ================================================================
# MatchContext — 贯穿全流程的上下文对象
# ================================================================

class MatchContext:
    """
    一场比赛的全量上下文。
    数据从原始赔率开始，流经各分析层，层层累积。
    每个层都可以读取前面所有层的数据。
    """
    
    def __init__(self, match_data: dict):
        # ---- 原始输入 ----
        self.match_no = match_data.get('match_no', '')
        self.home_team = match_data.get('home_team', '')
        self.away_team = match_data.get('away_team', '')
        self.stage = match_data.get('stage', '小组赛')
        self.raw = match_data
        
        # ---- Layer 1: 特征 (初始化后由 extract_features 填充) ----
        self.odds_features = {}       # 赔率隐含概率 + 变动
        self.asian_features = {}      # 亚盘
        self.ou_features = {}         # 大小球
        self.elo = {}                 # Elo评分
        self.motivation = {}          # 战意
        
        # ---- Layer 2: 庄家动机 (结构化数据!) ----
        self.bookmaker = {
            'trap_detected': False,
            'trap_direction': None,      # 'home'=诱主(真看客), 'away'=诱客(真看主)
            'anti_trap_pick': None,      # 反着买的方向
            'trap_confidence': 0.0,      # 诱盘可信度 0-1
            'margin': 0.0,               # 庄家抽水率
            'signals': [],               # 结构化信号对象列表, 不是文本!
        }
        
        # ---- Layer 3: 赛果预测 ----
        self.result_prediction = {
            'value': None,               # 'home'/'draw'/'away'
            'confidence': 0.0,
            'votes': {'home': 0.0, 'draw': 0.0, 'away': 0.0},
            'factor_details': [],        # 每个因子的投票详情
            'corrected_by_trap': False,  # 是否被庄家修正
            'original_value': None,      # 修正前的方向
        }
        
        # ---- Layer 4: 比分预测 ----
        self.score_prediction = {
            'most_likely': None,
            'alternatives': [],
            'all_scores': [],
            'expected_home_goals': 0.0,
            'expected_away_goals': 0.0,
            'ht_prediction': None,
            'confidence': {'score': 0.0, 'level': 'LOW', 'stars': 1},
        }
        
        # ---- 最终输出 ----
        self.output = {}
    
    def __repr__(self):
        return f"<MatchContext #{self.match_no} {self.home_team} vs {self.away_team}>"


# ================================================================
# 工具函数
# ================================================================

def get_team_elo(team_name):
    """获取球队当前Elo"""
    name = TEAM_ALIASES.get(team_name, team_name)
    elo_file = DATA_DIR / 'elo-data.json'
    if elo_file.exists():
        try:
            with open(elo_file, 'r', encoding='utf-8') as f:
                elo_dict = json.load(f)
            return elo_dict.get(name, INITIAL_ELO_BASE.get(name, 1500))
        except:
            pass
    return INITIAL_ELO_BASE.get(name, 1500)


def conv_hcap(h):
    """让球文字→数值"""
    if not h:
        return 0
    is_receiving = h.startswith('受')
    key = h[1:] if is_receiving else h
    d = {'平手': 0, '平/半': 0.25, '半球': 0.5, '半/一': 0.75, '一球': 1.0,
         '一/球半': 1.25, '球半': 1.5, '球半/两': 1.75, '两球': 2.0,
         '两/两半': 2.25, '两半': 2.5, '两球半': 2.5, '两/两球半': 2.25,
         '两球半/三': 2.75, '三球': 3.0, '三/三半': 3.25, '三半': 3.5}
    val = d.get(key, 0)
    return -val if is_receiving else val


def expected_score(elo_a, elo_b):
    """Elo期望胜率"""
    return 1.0 / (1.0 + 10.0 ** ((elo_b - elo_a) / 400.0))


def implied_prob(h_odds, d_odds, a_odds):
    """赔率→隐含概率 (含抽水去除)"""
    total = 1/h_odds + 1/d_odds + 1/a_odds
    return {
        'home': (1/h_odds) / total,
        'draw': (1/d_odds) / total,
        'away': (1/a_odds) / total,
        'margin': total - 1,
    }


def poisson(k, lam):
    """泊松分布概率"""
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return (lam ** k) * exp(-lam) / factorial(k)


# ================================================================
# Layer 1: 特征提取
# ================================================================

def extract_features(ctx: MatchContext):
    """
    从predict.py的输出提取特征
    
    predictions-*.json 中没有原始赔率数据, 但有:
    - factor_analysis (文本分析)
    - elo (Elo评分)
    - result_prediction (原赛果预测)
    """
    import re
    
    # ---- Elo特征 (直接从数据中读取) ----
    elo_data = ctx.raw.get('elo', {})
    if isinstance(elo_data, dict) and 'home' in elo_data:
        elo_h = elo_data['home']
        elo_a = elo_data['away']
    else:
        elo_h = get_team_elo(ctx.home_team)
        elo_a = get_team_elo(ctx.away_team)
    
    exp_h = expected_score(elo_h, elo_a)
    att_h, def_h = TEAM_STYLE.get(ctx.home_team, (1.0, 1.0))
    att_a, def_a = TEAM_STYLE.get(ctx.away_team, (1.0, 1.0))
    
    ctx.elo = {
        'home': elo_h, 'away': elo_a,
        'diff': elo_h - elo_a,
        'expected': exp_h,
        'home_attack': att_h, 'home_defense': def_h,
        'away_attack': att_a, 'away_defense': def_a,
        'is_home_stronger': elo_h > elo_a,
        'big_gap': abs(elo_h - elo_a) > 80,
    }
    
    # ---- 从 factor_analysis 文本中提取赔率信息 ----
    # 虽然predict.py没有保留结构化赔率数据,
    # 但我们从它的文本输出中解析关键信息
    factor_analysis = ctx.raw.get('factor_analysis', [])
    
    # 从文本中提取方向信息
    odds_dir = None
    odds_conf = 0
    mov_dir = None
    mov_conf = 0
    
    for line in factor_analysis:
        # 赔率隐含概率方向
        if line.startswith('odds_implied:'):
            if '->home' in line:
                odds_dir = 'home'
            elif '->away' in line:
                odds_dir = 'away'
            elif '->draw' in line:
                odds_dir = 'draw'
            import re
            cf_m = re.search(r'cf=([\d.]+)', line)
            if cf_m:
                odds_conf = min(float(cf_m.group(1)), 0.85)
        
        # 赔率变动方向
        if line.startswith('odds_movement:'):
            if '->home' in line:
                mov_dir = 'home'
            elif '->away' in line:
                mov_dir = 'away'
            cf_m = re.search(r'cf=([\d.]+)', line)
            if cf_m:
                mov_conf = min(float(cf_m.group(1)), 0.75)
    
    ctx.odds_features = {
        'direction': odds_dir,
        'direction_conf': odds_conf,
        'movement_dir': mov_dir,
        'movement_conf': mov_conf,
        'has_odds_data': odds_dir is not None,
    }
    
    # ---- 从 factor_analysis 提取亚盘信息 ----
    asian_dir = None
    for line in factor_analysis:
        if line.startswith('asian_handicap:'):
            if '->home' in line:
                asian_dir = 'home'
            elif '->away' in line:
                asian_dir = 'away'
            break
    
    ctx.asian_features = {
        'favored': asian_dir,
    }
    
    # ---- Polymarket市场情绪因子 ----
    poly_file = DATA_DIR / 'polymarket-data.json'
    if poly_file.exists():
        try:
            with open(poly_file, 'r', encoding='utf-8') as f:
                poly_raw = json.load(f)
            poly_teams = poly_raw.get('teams', {})
            # 别名映射
            alias = {'阿尔及利': 'Algeria', '乌兹别克': 'Uzbekistan'}
            h_name = alias.get(ctx.home_team, ctx.home_team)
            a_name = alias.get(ctx.away_team, ctx.away_team)
            
            # 英文名→中文名映射
            en_to_cn = {
                'Argentina': '阿根廷', 'Spain': '西班牙', 'France': '法国',
                'England': '英格兰', 'Brazil': '巴西', 'Germany': '德国',
                'Portugal': '葡萄牙', 'Netherlands': '荷兰', 'Uruguay': '乌拉圭',
                'Croatia': '克罗地亚', 'Morocco': '摩洛哥', 'Colombia': '哥伦比亚',
                'Japan': '日本', 'Norway': '挪威', 'USA': '美国',
                'Mexico': '墨西哥', 'Canada': '加拿大', 'Switzerland': '瑞士',
                'South Korea': '韩国', 'Belgium': '比利时', 'Senegal': '塞内加尔',
                'Ecuador': '厄瓜多尔', 'Egypt': '埃及', 'Australia': '澳大利亚',
                'Scotland': '苏格兰', 'Turkey': '土耳其', 'Czechia': '捷克',
                'Bosnia-Herzegovina': '波黑', 'Qatar': '卡塔尔', 'Paraguay': '巴拉圭',
                'Ivory Coast': '科特迪瓦', 'Tunisia': '突尼斯', 'Iran': '伊朗',
                'New Zealand': '新西兰', 'Saudi Arabia': '沙特阿拉伯',
                'Algeria': '阿尔及利亚', 'Ghana': '加纳', 'Panama': '巴拿马',
                'Iraq': '伊拉克', 'Uzbekistan': '乌兹别克斯坦', 'Jordan': '约旦',
                'South Africa': '南非', 'Haiti': '海地', 'Cape Verde': '佛得角',
                'Congo DR': '刚果金', 'Austria': '奥地利', 'Sweden': '瑞典',
                'Curacao': '库拉索',
            }
            
            # 找对应的英文名
            h_en = None
            a_en = None
            for en, cn in en_to_cn.items():
                if cn == ctx.home_team:
                    h_en = en
                if cn == ctx.away_team:
                    a_en = en
            
            if h_en and a_en:
                h_poly = poly_teams.get(h_en, {}).get('prob', 0)
                a_poly = poly_teams.get(a_en, {}).get('prob', 0)
                if h_poly > 0 and a_poly > 0:
                    ratio = h_poly / a_poly
                    ctx.odds_features['polymarket_ratio'] = ratio
                    ctx.odds_features['polymarket_home'] = h_poly
                    ctx.odds_features['polymarket_away'] = a_poly
        except:
            pass
    
    # ---- 原predict.py的赛果预测 (用于后续对比) ----
    ctx.raw_result = ctx.raw.get('result_prediction', {})


# ================================================================
# Layer 2: 庄家动机检测 (结构化输出!)
# ================================================================

def analyze_bookmaker(ctx: MatchContext):
    """
    庄家动机深度分析 — 从predict.py的factor_analysis文本中提取结构化数据
    
    因为predict.py已经做了完整的庄家检测，但只输出文本。
    这里把文本解析成结构化数据，供后续模块使用。
    """
    import re
    signals = []
    trap_conf = 0.0
    trap_direction = None  # 'home'=诱主(实看客), 'away'=诱客(实看主)
    anti_pick = None
    margin = 0.0
    
    factor_analysis = ctx.raw.get('factor_analysis', [])
    
    for line in factor_analysis:
        # ---- 检测诱盘 ----
        if '诱盘' in line and '诱客' in line:
            trap_direction = 'away'
            anti_pick = 'home'
            trap_conf += 0.25
            signals.append({
                'type': 'trap_诱客',
                'detail': line,
                'strength': 0.25,
            })
        elif '诱盘' in line and '诱主' in line:
            trap_direction = 'home'
            anti_pick = 'away'
            trap_conf += 0.25
            signals.append({
                'type': 'trap_诱主',
                'detail': line,
                'strength': 0.25,
            })
        
        # ---- 检测反买信号 (最直接!) ----
        if '反买' in line:
            if '反买主' in line:
                trap_direction = 'away'
                anti_pick = 'home'
            elif '反买客' in line:
                trap_direction = 'home'
                anti_pick = 'away'
            trap_conf += 0.20
            # 提取修正值
            fix_m = re.search(r'修正([+-]?[\d.]+)', line)
            if fix_m:
                trap_conf += min(abs(float(fix_m.group(1))) * 0.5, 0.15)
            signals.append({
                'type': 'trap_反买',
                'detail': line,
                'strength': 0.20,
            })
        
        # ---- 阻盘检测 ----
        if '阻盘' in line and '阻客' in line:
            trap_direction = 'away'
            anti_pick = 'away'  # 阻客=真看客
            trap_conf += 0.15
            signals.append({'type': 'block_阻客', 'detail': line, 'strength': 0.15})
        elif '阻盘' in line and '阻主' in line:
            trap_direction = 'home'
            anti_pick = 'home'
            trap_conf += 0.15
            signals.append({'type': 'block_阻主', 'detail': line, 'strength': 0.15})
        elif '阻盘' in line and '庄家有信心' in line:
            # 阻盘说明庄家对反方向有信心
            if '阻客' in line:
                anti_pick = 'away'
            elif '阻主' in line:
                anti_pick = 'home'
            trap_conf += 0.12
            signals.append({'type': 'block_阻盘', 'detail': line, 'strength': 0.12})
        
        # ---- 高抽水 ----
        if '高抽水' in line or '抽水' in line:
            margin_m = re.search(r'([\d.]+)%', line)
            if margin_m:
                margin = float(margin_m.group(1)) / 100
            trap_conf += 0.05
            signals.append({'type': 'risk_高抽水', 'detail': line, 'strength': 0.05})
        
        # ---- 冷门检测 ----
        if '冷门检测' in line and '防冷' in line:
            trap_conf += 0.05
            signals.append({'type': 'caution_冷门预警', 'detail': line, 'strength': 0.05})
        
        # ---- 赔率稳定控盘 ----
        if '控盘' in line or '稳定' in line:
            signals.append({'type': 'control_控盘', 'detail': line, 'strength': 0.03})
    
    # ---- 综合判断 ----
    orig_value = ctx.raw.get('result_prediction', {}).get('value', None)
    
    if trap_direction == 'away' and anti_pick and orig_value:
        if anti_pick != orig_value:
            trap_conf += 0.10
    
    # ---- 新增: 战意vs赔率矛盾检测 (经典诱盘模式!) ----
    # 场景: 赔率高度看好A队, 但A队'保平出线'而B队'背水一战'
    # 庄家利用保平队的保守心态, 诱大众买热门方
    home_motive = ctx.raw.get('home_motive', '')
    away_motive = ctx.raw.get('away_motive', '')
    odds_dir = ctx.odds_features.get('direction', None)
    
    # 经典诱盘模式1: 赔率看主胜, 但主队保平 + 客队背水一战
    if odds_dir == 'home' and home_motive == '保平出线' and away_motive == '背水一战':
        strength = 0.30
        trap_conf += strength
        if trap_direction is None:
            trap_direction = 'away'
            anti_pick = 'away'  # 诱主(实看客胜或平局)
        signals.append({
            'type': 'trap_战意矛盾',
            'detail': f'赔率看主胜但主队保平出线+客队背水一战→诱主(实看客不败)',
            'strength': strength,
        })
    
    # 经典诱盘模式2: 赔率看客胜, 但客队已出线 + 主队背水一战
    if odds_dir == 'away' and away_motive == '已出线' and home_motive in ('背水一战', '绝境求生'):
        strength = 0.28
        trap_conf += strength
        if trap_direction is None:
            trap_direction = 'home'
            anti_pick = 'home'
        signals.append({
            'type': 'trap_战意矛盾',
            'detail': f'赔率看客胜但客队已出线可能留力+主队全力以赴→诱客(实看主不败)',
            'strength': strength,
        })
    
    # ---- 综合判断 ----
    trap_detected = trap_conf >= 0.20
    trap_conf = min(trap_conf, 0.70)
    
    if trap_detected:
        if anti_pick is None and trap_direction == 'away':
            anti_pick = 'home'
        elif anti_pick is None and trap_direction == 'home':
            anti_pick = 'away'
    
    ctx.bookmaker = {
        'trap_detected': trap_detected,
        'trap_direction': trap_direction,
        'anti_trap_pick': anti_pick,
        'trap_confidence': round(trap_conf, 3),
        'margin': round(margin, 4) if margin else 0,
        'signals': signals,
        'has_margin_warning': margin > 0.10 if margin else False,
    }


# ================================================================
# Layer 3: 赛果预测 (使用全部数据 + 庄家修正)
# ================================================================

def predict_result(ctx: MatchContext):
    """
    赛果预测 — 基于原predict.py的输出 + 庄家修正
    
    因为predict.py已经做了完整的7因子分析,
    这里我们:
      1. 读取原predict.py的result_prediction
      2. 叠加我们的庄家动机检测结果
      3. 如果诱盘信号足够强 → 反买修正
    """
    
    # ---- 读取原predict.py的结果 ----
    orig = ctx.raw.get('result_prediction', {})
    orig_val = orig.get('value', None)
    orig_conf = orig.get('confidence', 0)
    
    # ---- 庄家修正 ----
    bm = ctx.bookmaker
    corrected = False
    
    if bm['trap_detected'] and bm['trap_confidence'] >= 0.20 and bm['anti_trap_pick']:
        anti = bm['anti_trap_pick']
        
        if anti != orig_val:
            # 诱盘信号强烈且与原预测方向不同 → 反买!
            corrected = True
            result_val = anti
            # 置信度 = 原置信度 + 庄家信心, 不超过0.95
            result_conf = min(orig_conf + bm['trap_confidence'] * 0.3, 0.95)
        else:
            # 诱盘方向与原预测一致 → 加强原预测
            result_val = orig_val
            result_conf = min(orig_conf + bm['trap_confidence'] * 0.15, 0.95)
    else:
        # 无诱盘信号, 使用原预测
        result_val = orig_val
        result_conf = orig_conf
    
    if result_val is None:
        # 极端情况: 原预测也无结果
        result_val = 'draw'
        result_conf = 0.33
    
    ctx.result_prediction = {
        'value': result_val,
        'confidence': round(result_conf, 3),
        'original_value': orig_val,
        'original_confidence': orig_conf,
        'corrected_by_trap': corrected,
    }


# ================================================================
# Layer 4: 比分预测 (使用全部上下文!)
# ================================================================

def predict_score(ctx: MatchContext):
    """
    比分预测 — 使用 ctx 中所有可用的数据
    
    输入:
      - ctx.elo (攻防强度)
      - ctx.motivation (战意调整)
      - ctx.bookmaker (庄家意图!)
      - ctx.result_prediction (赛果方向)
      - ctx.odds_features (大小球倾向)
    
    流程:
      1. 从攻防分解计算期望进球
      2. 战意调整
      3. 庄家修正: 诱盘→冷门比分权重↑
      4. 双变量泊松 + Dixon-Coles
      5. 经验分布加权
      6. 输出
    """
    
    # ---- Step 1: 期望进球 (v2 — 融合Elo差距+战意) ----
    league_avg = WORLD_CUP_AVG_GOALS / 2  # ~1.4
    
    # 基础期望 (攻防分解)
    base_h = league_avg * ctx.elo.get('home_attack', 1.0) * ctx.elo.get('away_defense', 1.0) * HOME_ADVANTAGE
    base_a = league_avg * ctx.elo.get('away_attack', 1.0) * ctx.elo.get('home_defense', 1.0)
    
    # Elo差距加成: Elo每差100分, 强队进球+15%, 弱队-10%
    elo_diff = ctx.elo.get('diff', 0)
    if elo_diff > 50:
        factor = min(elo_diff / 100 * 0.15, 0.45)
        base_h *= (1.0 + factor)
        base_a *= (1.0 - factor * 0.7)
    elif elo_diff < -50:
        factor = min(abs(elo_diff) / 100 * 0.15, 0.45)
        base_a *= (1.0 + factor)
        base_h *= (1.0 - factor * 0.7)
    
    # Polymarket市场情绪加成:
    # 冠军赔率概率比 = 大众对两队的看好程度
    # 如果Polymarket概率差显著大于Elo差, 说明市场有额外信息
    poly_ratio = ctx.odds_features.get('polymarket_ratio', None)
    if poly_ratio and poly_ratio > 1.5:
        # 市场显著看好主队 (超出Elo预期)
        poly_h = ctx.odds_features.get('polymarket_home', 0)
        poly_a = ctx.odds_features.get('polymarket_away', 0)
        if poly_h > 0 and poly_a > 0:
            # 用Polymarket比值除以Elo期望比值, 得到"市场超额信心"
            elo_expected = ctx.elo.get('expected', 0.5)
            elo_ratio = elo_expected / max(1 - elo_expected, 0.01)
            excess_confidence = poly_ratio / max(elo_ratio, 0.1)
            if excess_confidence > 1.3:
                boost = min((excess_confidence - 1.0) * 0.08, 0.15)
                base_h *= (1.0 + boost)
                base_a *= (1.0 - boost * 0.5)
    elif poly_ratio and poly_ratio < 0.67:
        poly_h = ctx.odds_features.get('polymarket_home', 0)
        poly_a = ctx.odds_features.get('polymarket_away', 0)
        if poly_h > 0 and poly_a > 0:
            elo_expected = ctx.elo.get('expected', 0.5)
            elo_ratio = elo_expected / max(1 - elo_expected, 0.01)
            excess_confidence = (1/poly_ratio) / max(1/elo_ratio, 0.1) if elo_ratio > 0 else 1
            if excess_confidence > 1.3:
                boost = min((excess_confidence - 1.0) * 0.08, 0.15)
                base_a *= (1.0 + boost)
                base_h *= (1.0 - boost * 0.5)
    
    # 战意读取
    goal_mult = ctx.raw.get('goal_multiplier', 1.0)
    home_motive = ctx.raw.get('home_motive', '')
    away_motive = ctx.raw.get('away_motive', '')
    home_intensity = ctx.raw.get('home_intensity', 1.0)
    away_intensity = ctx.raw.get('away_intensity', 1.0)
    
    ctx.motivation = {
        'goal_multiplier': goal_mult,
        'home_motive': home_motive,
        'away_motive': away_motive,
        'home_intensity': home_intensity,
        'away_intensity': away_intensity,
    }
    
    # 战意修正v2: 直接根据动机类型调整期望进球
    # '已出线' → 进球期望大幅降低 (留力/轮换)
    # '背水一战' → 进球期望提升 (全力进攻)
    # '保平出线' → 进球期望降低 (保守)
    motive_adj_h = 1.0
    motive_adj_a = 1.0
    
    # 进攻调整: 动机影响进攻欲望
    if home_motive == '已出线':
        motive_adj_h = 0.70  # 已出线留力, 进攻-30%
    elif home_motive == '保平出线':
        motive_adj_h = 0.85  # 保平即可, 保守
    elif home_motive == '背水一战':
        motive_adj_h = 1.20  # 必须赢, 全力进攻
    elif home_motive == '绝境求生':
        motive_adj_h = 1.15
    elif home_motive == '荣誉之战':
        motive_adj_h = 1.10  # 无压力, 放开打
    elif home_motive == '主动进取':
        motive_adj_h = 1.10
    else:
        motive_adj_h = 1.0
    
    if away_motive == '已出线':
        motive_adj_a = 0.70  # 已出线留力, 进攻-30%
    elif away_motive == '保平出线':
        motive_adj_a = 0.85
    elif away_motive == '背水一战':
        motive_adj_a = 1.20
    elif away_motive == '绝境求生':
        motive_adj_a = 1.15
    elif away_motive == '荣誉之战':
        motive_adj_a = 1.10
    elif away_motive == '主动进取':
        motive_adj_a = 1.10
    else:
        motive_adj_a = 1.0
    
    # 防守松懈因子: 已出线队防守变弱 → 对手进球期望↑
    # 背水一战队防守更拼 → 对手进球期望↓
    def_adj_h = 1.0  # 主队防守对客队进球的影响
    def_adj_a = 1.0  # 客队防守对主队进球的影响
    
    if home_motive == '已出线':
        def_adj_a = 1.20  # 主队已出线: 防守松懈, 客队进球+20%
    elif home_motive == '背水一战':
        def_adj_a = 0.85  # 主队背水一战: 防守拼命, 客队进球-15%
    elif home_motive == '绝境求生':
        def_adj_a = 0.90
    elif home_motive == '保平出线':
        def_adj_a = 0.90  # 保平即出线: 防守为主
    
    if away_motive == '已出线':
        def_adj_h = 1.20  # 客队已出线: 防守松懈, 主队进球+20%
    elif away_motive == '背水一战':
        def_adj_h = 0.85
    elif away_motive == '绝境求生':
        def_adj_h = 0.90
    elif away_motive == '保平出线':
        def_adj_h = 0.90
    
    exp_h = base_h * goal_mult * home_intensity * motive_adj_h * def_adj_h
    exp_a = base_a * goal_mult * away_intensity * motive_adj_a * def_adj_a
    
    # 阶段调整
    if ctx.stage in ('16强', '8强'):
        exp_h *= 0.90
        exp_a *= 0.90
    elif ctx.stage in ('半决赛', '决赛'):
        exp_h *= 0.85
        exp_a *= 0.85
    
    exp_h = max(exp_h, 0.2)
    exp_a = max(exp_a, 0.2)
    
    # ---- Step 2: 庄家修正期望进球 ----
    # 诱盘 → 冷门方向期望进球↑
    if ctx.bookmaker['trap_detected'] and ctx.bookmaker['trap_confidence'] >= 0.2:
        trap_c = ctx.bookmaker['trap_confidence']
        if ctx.bookmaker['anti_trap_pick'] == 'home':
            exp_h *= (1.0 + trap_c * 0.2)  # 主队进球期望上升
            exp_a *= (1.0 - trap_c * 0.1)
        elif ctx.bookmaker['anti_trap_pick'] == 'away':
            exp_a *= (1.0 + trap_c * 0.2)
            exp_h *= (1.0 - trap_c * 0.1)
    
    # ---- Step 3: 双变量泊松 + Dixon-Coles ----
    scores = {}
    for hg in range(8):
        for ag in range(8):
            prob = bivariate_poisson(hg, ag, exp_h, exp_a, BIVARIATE_COV)
            dc_adj = dixon_coles_adj(hg, ag, exp_h, exp_a, DIXON_COLES_RHO)
            prob *= dc_adj
            if prob > 0.001:
                scores[(hg, ag)] = prob
    
    # 归一化
    total_p = sum(scores.values())
    if total_p > 0:
        scores = {k: v / total_p for k, v in scores.items()}
    
    # ---- Step 4: 赛果方向约束 ----
    result_val = ctx.result_prediction['value']
    if result_val:
        for key in list(scores.keys()):
            hg, ag = key
            if result_val == 'home' and hg < ag:
                scores[key] *= 0.1
            elif result_val == 'home' and hg == ag:
                scores[key] *= 0.6
            elif result_val == 'away' and hg > ag:
                scores[key] *= 0.1
            elif result_val == 'away' and hg == ag:
                scores[key] *= 0.6
            elif result_val == 'draw' and hg != ag:
                scores[key] *= 0.3
    
    total = sum(scores.values())
    if total > 0:
        scores = {k: v / total for k, v in scores.items()}
    
    # ---- Step 5: 排序 ----
    sorted_scores = sorted(scores.items(), key=lambda x: -x[1])
    
    top_n = []
    cumulative = 0
    for (hg, ag), prob in sorted_scores:
        top_n.append({
            'home_goals': hg,
            'away_goals': ag,
            'score': f"{ctx.home_team} {hg}-{ag} {ctx.away_team}",
            'probability': round(prob, 4),
        })
        cumulative += prob
        if len(top_n) >= 8 or cumulative > 0.90:
            break
    
    # ---- Step 6: 半场预测 ----
    ht_factor = 0.42
    ht_scores = []
    for hg in range(5):
        for ag in range(5):
            prob = bivariate_poisson(hg, ag, exp_h * ht_factor, exp_a * ht_factor, BIVARIATE_COV * 0.5)
            if prob > 0.02:
                ht_scores.append({
                    'home_goals': hg, 'away_goals': ag,
                    'score': f"{ctx.home_team} {hg}-{ag} {ctx.away_team}(HT)",
                    'probability': round(prob, 4),
                })
    ht_scores.sort(key=lambda x: -x['probability'])
    
    # ---- Step 7: 置信度 ----
    top_prob = top_n[0]['probability'] if top_n else 0
    second_prob = top_n[1]['probability'] if len(top_n) > 1 else 0
    prob_margin = top_prob - second_prob
    
    conf_score = min(top_prob * 2.5, 0.4) + min(prob_margin * 3.0, 0.3) + min(abs(exp_h - exp_a) / max(exp_h, exp_a, 0.01) * 0.15, 0.1)
    
    if ctx.bookmaker['trap_detected']:
        conf_score += 0.08  # 有明确庄家信号时提高置信
    
    conf_score = min(conf_score, 0.95)
    
    if conf_score >= 0.55:
        conf_level, stars = 'HIGH', 4
    elif conf_score >= 0.40:
        conf_level, stars = 'MED', 3
    elif conf_score >= 0.28:
        conf_level, stars = 'LOW', 2
    else:
        conf_level, stars = 'VERYLOW', 1
    
    ctx.score_prediction = {
        'most_likely': top_n[0] if top_n else None,
        'alternatives': [s for s in top_n[1:5]],
        'all_scores': top_n,
        'expected_home_goals': round(exp_h, 2),
        'expected_away_goals': round(exp_a, 2),
        'ht_prediction': ht_scores[0] if ht_scores else None,
        'ht_alternatives': [s for s in ht_scores[1:4]],
        'confidence': {
            'score': round(conf_score, 3),
            'level': conf_level,
            'stars': stars,
            'prob_margin': round(prob_margin, 3),
        },
    }


def bivariate_poisson(hg, ag, lam_h, lam_a, lam_c=BIVARIATE_COV):
    """双变量泊松"""
    if lam_h <= 0 or lam_a <= 0:
        return 0
    indep = poisson(hg, lam_h) * poisson(ag, lam_a)
    if lam_c > 0 and hg > 0 and ag > 0:
        cov = 0
        for k in range(min(hg, ag) + 1):
            try:
                cov += math.comb(hg, k) * math.comb(ag, k) * factorial(k) * ((lam_c / (lam_h * lam_a)) ** k)
            except:
                continue
        return exp(-lam_c) * indep * cov
    return indep


def dixon_coles_adj(hg, ag, lam_h, lam_a, rho=DIXON_COLES_RHO):
    """Dixon-Coles低比分修正"""
    if hg + ag <= 1 and lam_h > 0 and lam_a > 0:
        tau = 1.0 + rho * (hg - lam_h) * (ag - lam_a) / math.sqrt(lam_h * lam_a)
        return max(tau, 0.0)
    return 1.0


# ================================================================
# 主流程: 单场比赛全流程
# ================================================================

def analyze_match(match_data: dict) -> MatchContext:
    """
    单场比赛全流程分析。
    
    输入: 比赛数据 dict (from predictions-*.json)
    输出: MatchContext (内含所有层的数据)
    """
    ctx = MatchContext(match_data)
    
    # Layer 1: 特征提取
    extract_features(ctx)
    
    # Layer 2: 庄家动机检测
    analyze_bookmaker(ctx)
    
    # Layer 3: 赛果预测 (带庄家修正)
    predict_result(ctx)
    
    # Layer 4: 比分预测 (使用全部上下文)
    predict_score(ctx)
    
    return ctx


# ================================================================
# 批量处理
# ================================================================

def run_pipeline(input_file=None):
    """
    批量运行全流程分析
    
    输入: predictions-*.json 文件
    输出: 增强后的预测结果 (含所有层的结构化数据)
    """
    if input_file is None:
        input_file = DATA_DIR / 'predictions-latest.json'
    
    with open(input_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    matches = data.get('predictions', data if isinstance(data, list) else [])
    
    print(f"\n{'='*70}")
    print(f"  绿茵神算 v4 · 统一分析管道启动")
    print(f"  输入: {input_file}")
    print(f"  比赛数: {len(matches)}")
    print(f"{'='*70}")
    
    results = []
    trap_count = 0
    
    for i, m in enumerate(matches):
        home, away = m.get('home_team', '?'), m.get('away_team', '?')
        print(f"  [{i+1:02d}/{len(matches)}] {home} vs {away}...", end=' ', flush=True)
        
        ctx = analyze_match(m)
        results.append(ctx)
        
        if ctx.bookmaker['trap_detected']:
            trap_count += 1
            trap_mark = ' TRAP!' if ctx.result_prediction['corrected_by_trap'] else ' trap(signal)'
            print(f"OK {trap_mark}")
        else:
            print("OK")
    
    # 输出汇总
    print(f"\n{'='*70}")
    print(f"  分析完成: {len(results)} 场, 其中 {trap_count} 场检测到庄家诱盘信号")
    print(f"{'='*70}\n")
    
    for ctx in results:
        rp = ctx.result_prediction
        sp = ctx.score_prediction
        bm = ctx.bookmaker
        
        val_map = {'home': f'{ctx.home_team}胜', 'away': f'{ctx.away_team}胜', 'draw': '平局'}
        result_text = val_map.get(rp['value'], '?')
        
        trap_info = ''
        if bm['trap_detected']:
            td = bm['trap_direction']
            ap = bm['anti_trap_pick']
            td_text = f'诱{td}(实看{ap})' if td else ''
            trap_info = f'  [庄家: {td_text} 信{bm["trap_confidence"]:.0%}]'
            if rp['corrected_by_trap']:
                trap_info += ' ★已修正!'
        
        score_text = sp['most_likely']['score'] if sp['most_likely'] else '?-?'
        conf_text = f"{sp['confidence']['level']}({'*'*sp['confidence']['stars']})"
        
        print(f"  #{ctx.match_no:2s} {ctx.home_team:6s} vs {ctx.away_team:6s}")
        print(f"      赛果: {result_text} (信{rp['confidence']:.0%}){trap_info}")
        print(f"      比分: {score_text:25s} 置信:{conf_text}")
        print(f"      期望: {ctx.home_team}({sp['expected_home_goals']}) {ctx.away_team}({sp['expected_away_goals']})")
        
        if bm['signals']:
            for sig in bm['signals'][:2]:
                print(f"      信号: [{sig['type']}] {sig['detail']}")
    
    print(f"\n{'='*70}")
    
    # 保存结果
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output = {
        'generate_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'total_matches': len(results),
        'trap_detected': trap_count,
        'model': '绿茵神算v4-统一引擎',
        'predictions': [],
    }
    
    for ctx in results:
        output['predictions'].append({
            'match': f"{ctx.home_team} vs {ctx.away_team}",
            'match_no': ctx.match_no,
            'home_team': ctx.home_team,
            'away_team': ctx.away_team,
            'stage': ctx.stage,
            # 赛果 (含庄家修正信息)
            'result_prediction': ctx.result_prediction,
            # 比分 (含庄家修正)
            'score_prediction': ctx.score_prediction,
            # 庄家动机 (结构化数据!)
            'bookmaker_analysis': ctx.bookmaker,
            # Elo
            'elo': ctx.elo,
            # 原始赔率特征
            'odds_summary': {
                'home_prob': ctx.odds_features.get('home_prob', 0),
                'draw_prob': ctx.odds_features.get('draw_prob', 0),
                'away_prob': ctx.odds_features.get('away_prob', 0),
                'margin': ctx.odds_features.get('margin', 0),
                'handicap': ctx.asian_features.get('handicap_text', ''),
            },
        })
    
    output_file = DATA_DIR / f'v4-predictions-{timestamp}.json'
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    
    print(f"\n  已保存: {output_file}")
    return results, output_file


# ================================================================
# 命令行入口
# ================================================================

def main():
    if len(sys.argv) > 1 and sys.argv[1] == '--file':
        input_file = sys.argv[2] if len(sys.argv) > 2 else None
        run_pipeline(input_file)
    elif len(sys.argv) > 1 and sys.argv[1] == '--single':
        # 单场比赛测试
        home = sys.argv[2] if len(sys.argv) > 2 else '厄瓜多尔'
        away = sys.argv[3] if len(sys.argv) > 3 else '德国'
        # 构造模拟数据
        mock_data = {
            'home_team': home,
            'away_team': away,
            'match_no': 'test',
            'stage': '小组赛',
            'jingcai': {},  # 没有赔率数据时会用默认值
            'odds_3in1': {},
        }
        ctx = analyze_match(mock_data)
        
        print(f"\n{'='*60}")
        print(f"  单场分析: {home} vs {away}")
        print(f"{'='*60}")
        val_map = {'home': f'{home}胜', 'draw': '平局', 'away': f'{away}胜'}
        print(f"  赛果: {val_map.get(ctx.result_prediction['value'], '?')} (信{ctx.result_prediction['confidence']:.0%})")
        if ctx.result_prediction['corrected_by_trap']:
            print(f"  ★ 庄家修正: 原预测{val_map.get(ctx.result_prediction['original_value'], '?')} → 反买!")
        if ctx.score_prediction['most_likely']:
            print(f"  比分: {ctx.score_prediction['most_likely']['score']}")
        print(f"  期望进球: {home}({ctx.score_prediction['expected_home_goals']}) {away}({ctx.score_prediction['expected_away_goals']})")
        print(f"  Elo: {home}({ctx.elo.get('home','?')}) {away}({ctx.elo.get('away','?')})")
        if ctx.bookmaker['trap_detected']:
            print(f"  🚨 庄家诱盘检测: 诱{ctx.bookmaker['trap_direction']}→实看{ctx.bookmaker['anti_trap_pick']} (信{ctx.bookmaker['trap_confidence']:.0%})")
            for sig in ctx.bookmaker['signals']:
                print(f"     [{sig['type']}] {sig['detail']}")
    else:
        run_pipeline()


if __name__ == '__main__':
    main()
