#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🎯 绿茵神算 v3.5 · 增强比分预测引擎

核心改进（相比 predict.py 中的 predict_score）:
  1. Elo分解攻防: Elo评分 → Attack/Defense 独立参数
  2. 双变量泊松 (Bivariate Poisson): 加入协方差项，提高平局比分准确率
  3. Dixon-Coles 低比分修正: 解决足球0-0/1-1过多的分布偏差
  4. 经验比分分布表: 从历史World Cup数据校准的比分概率
  5. 加权多模型融合: 泊松(60%) + 经验(25%) + Elo(15%)

用法:
  python scripts/score-predictor-enhanced.py                          # 单场测试
  python scripts/score-predictor-enhanced.py --batch                  # 批量预测全部剩余比赛
  
依赖: 读取 predict.py 中的 data/predictions-*.json 作为输入
"""

import json
import math
import sys
import os
from datetime import datetime
from pathlib import Path
from math import exp, factorial

SCRIPT_DIR = Path(__file__).parent.resolve()
PROJECT_DIR = SCRIPT_DIR.parent
DATA_DIR = PROJECT_DIR / "data"

# ================================================================
# 1. 世界杯历史参数
# ================================================================

# 2026世界杯截至目前的场均进球 (动态更新)
WORLD_CUP_AVG_GOALS = 2.8  # 2022卡塔尔=2.69, 2018俄罗斯=2.64, 2026目前~2.8

# 主场优势系数 (世界杯中立场比赛较多，但仍有名义主队)
HOME_ADVANTAGE = 1.10  # 主队进球+10%

# Dixon-Coles 低比分修正参数 (ρ)
# ρ > 0: 0-0, 1-0, 0-1, 1-1 的概率增加
# ρ < 0: 这些比分概率减少
# 通常 ρ ≈ 0.05~0.15
DIXON_COLES_RHO = 0.10

# 双变量泊松协方差参数 (λ_c)
# 控制主客进球的相关性
BIVARIATE_COV = 0.08

# ================================================================
# 2. Elo → 攻防强度分解
# ================================================================

def elo_to_strength(elo, avg_elo=1550):
    """
    将Elo评分分解为进攻强度和防守强度
    
    原理:
      Elo反映整体实力，但两个Elo相近的队可能风格迥异
      - 进攻强防守弱 (如日本: 能进也能丢)
      - 防守强进攻弱 (如摩洛哥: 防反专家)
      
    这里我们做基础分解，后续可用真实进球数据校准
    """
    diff = elo - avg_elo
    # 基础强度: Elo每高100分 ≈ 强度高14%
    base_strength = 1.0 + (diff / 400)
    
    # 默认攻防均衡 (后续可从历史数据校准)
    # attack_ratio > 1 = 偏进攻, < 1 = 偏防守
    attack_ratio = 1.0
    defense_ratio = 1.0
    
    # 根据球队风格微调 (硬编码已知球队特征)
    STYLE_MAP = {
        # 进攻型 (攻击强于防守)
        '阿根廷': (1.12, 0.88),
        '巴西': (1.15, 0.85),
        '法国': (1.10, 0.90),
        '英格兰': (1.08, 0.92),
        '荷兰': (1.10, 0.90),
        '日本': (1.08, 0.92),
        '德国': (1.08, 0.92),
        '葡萄牙': (1.10, 0.90),
        '西班牙': (1.06, 0.94),
        '挪威': (1.12, 0.88),
        '乌拉圭': (1.05, 0.95),
        '厄瓜多尔': (1.07, 0.93),
        # 防守型 (防守强于进攻)
        '摩洛哥': (0.90, 1.10),
        '美国': (0.92, 1.08),
        '克罗地亚': (0.88, 1.12),
        '伊朗': (0.85, 1.15),
        '比利时': (0.95, 1.05),
        '澳大利亚': (0.90, 1.10),
        '塞内加尔': (0.92, 1.08),
        '加拿大': (0.95, 1.05),
        '巴拉圭': (0.90, 1.10),
        '墨西哥': (0.95, 1.05),
        '韩国': (1.05, 0.95),
        '突尼斯': (0.88, 1.12),
        '瑞士': (0.85, 1.15),
        '丹麦': (0.88, 1.12),
        '瑞典': (0.92, 1.08),
    }
    
    if elo in STYLE_MAP:
        attack_ratio, defense_ratio = STYLE_MAP[elo]
    
    attack = base_strength * attack_ratio
    defense = base_strength * defense_ratio
    
    return attack, defense


def get_team_elo(team_name):
    """从Elo数据文件获取球队Elo (复用predict.py的数据)"""
    elo_file = DATA_DIR / 'elo-data.json'
    if elo_file.exists():
        try:
            with open(elo_file, 'r', encoding='utf-8') as f:
                elo_dict = json.load(f)
            # 别名映射
            alias_map = {
                '阿尔及利': '阿尔及利亚',
                '乌兹别克': '乌兹别克斯坦',
            }
            name = alias_map.get(team_name, team_name)
            return elo_dict.get(name, 1500)
        except:
            pass
    return 1500


def get_league_avg_from_matches(matches_data):
    """
    从已有比赛结果计算实际场均进球
    如果数据不足则返回默认值
    """
    if not matches_data:
        return WORLD_CUP_AVG_GOALS
    
    total_goals = 0
    match_count = 0
    for m in matches_data:
        sp = m.get('score_prediction', {})
        hg = sp.get('home_goals', None)
        ag = sp.get('away_goals', None)
        # 从预测数据中提取的是预测值，不是实际值
        # 需要实际赛果
    
    return WORLD_CUP_AVG_GOALS


# ================================================================
# 3. 期望进球计算 (攻防分解法)
# ================================================================

def calc_expected_goals(home_team, away_team, stage='小组赛', 
                        home_motive='', away_motive='',
                        goal_multiplier=1.0,
                        home_intensity=1.0, away_intensity=1.0):
    """
    从攻防强度计算期望进球 (替代原来的if/else盘口链条)
    
    Args:
        home_team, away_team: 球队名称
        stage: 比赛阶段
        home_motive, away_motive: 战意
        goal_multiplier: 进球乘数 (来自战意分析)
        home_intensity, away_intensity: 攻防强度调整
    Returns:
        (expected_home_goals, expected_away_goals)
    """
    # 获取Elo
    elo_h = get_team_elo(home_team)
    elo_a = get_team_elo(away_team)
    
    # 分解攻防
    home_att, home_def = elo_to_strength(elo_h)
    away_att, away_def = elo_to_strength(elo_a)
    
    # 联赛平均进球 (世界杯中立场的预期总进球)
    league_avg = WORLD_CUP_AVG_GOALS / 2  # 每队半场平均
    
    # 期望进球公式:
    # 主队期望 = 联赛平均 × 主队进攻 × 客队防守 × 主场优势 × 战意调整
    # 客队期望 = 联赛平均 × 客队进攻 × 主队防守 × 战意调整
    exp_h = league_avg * home_att * away_def * HOME_ADVANTAGE
    exp_a = league_avg * away_att * home_def
    
    # 战意调整
    exp_h *= goal_multiplier * home_intensity
    exp_a *= goal_multiplier * away_intensity
    
    # 阶段调整 (淘汰赛更保守)
    if stage in ('16强', '8强'):
        exp_h *= 0.90
        exp_a *= 0.90
    elif stage in ('半决赛', '决赛'):
        exp_h *= 0.85
        exp_a *= 0.85
    
    # 确保最小期望不为零
    exp_h = max(exp_h, 0.2)
    exp_a = max(exp_a, 0.2)
    
    return exp_h, exp_a, elo_h, elo_a


# ================================================================
# 4. 双变量泊松分布 (Bivariate Poisson)
# ================================================================

def bivariate_poisson(hg, ag, lam_h, lam_a, lam_c=BIVARIATE_COV):
    """
    双变量泊松概率质量函数
    
    P(X=hg, Y=ag) = 
        exp(-(λ₁+λ₂+λ_c)) × (λ₁^hg / hg!) × (λ₂^ag / ag!) × 
        Σ_{k=0}^{min(hg,ag)} C(hg,k) × C(ag,k) × k! × (λ_c / (λ₁×λ₂))^k
    
    这比独立泊松多了协方差项 λ_c:
      - λ_c > 0: 主客进球正相关 (一方进球多另一方也容易多)
      - λ_c < 0: 负相关 (一队压着另一队打)
    
    对足球比分的影响:
      - 提高同分比分(0-0,1-1,2-2)的概率
      - 降低极端比分(5-0,0-5)的概率
    """
    if lam_h <= 0 or lam_a <= 0:
        return 0
    
    # 独立泊松部分
    def poisson(k, lam):
        if lam <= 0:
            return 1.0 if k == 0 else 0.0
        return (lam ** k) * exp(-lam) / factorial(k)
    
    # 独立泊松乘积
    indep_prob = poisson(hg, lam_h) * poisson(ag, lam_a)
    
    # 协方差修正项
    if lam_c > 0 and hg > 0 and ag > 0:
        cov_term = 0
        for k in range(min(hg, ag) + 1):
            try:
                term = (math.comb(hg, k) * math.comb(ag, k) * 
                       factorial(k) * ((lam_c / (lam_h * lam_a)) ** k))
                cov_term += term
            except (OverflowError, ValueError):
                continue
        prob = exp(-lam_c) * indep_prob * cov_term
    else:
        prob = indep_prob
    
    return max(prob, 0.0)


# ================================================================
# 5. Dixon-Coles 低比分修正
# ================================================================

def dixon_coles_adjustment(hg, ag, lam_h, lam_a, rho=DIXON_COLES_RHO):
    """
    Dixon-Coles (1997) 低比分修正
    
    在低比分区域 (hg+ag ≤ 1) 添加修正系数:
      τ(x, y, λ₁, λ₂, ρ) = 
        1 + ρ × (x - λ₁) × (y - λ₂) / √(λ₁×λ₂)  当 x+y ≤ 1
        1  当 x+y > 1
    
    效果:
      - ρ > 0: 提高 0-0, 1-0, 0-1, 1-1 的概率
      - 比分越高，修正越接近1 (无影响)
    """
    if hg + ag <= 1:
        if lam_h > 0 and lam_a > 0:
            tau = 1.0 + rho * (hg - lam_h) * (ag - lam_a) / math.sqrt(lam_h * lam_a)
            return max(tau, 0.0)  # 防止负修正
    return 1.0


# ================================================================
# 6. 经验比分分布表 (从历史大赛数据校准)
# ================================================================

# 世界杯历史比分分布 (基于2018+2022+2026已赛场次)
# 这是先验分布，会与泊松分布结果加权平均
# 格式: (hg, ag): 基础概率权重
EMPIRICAL_SCORE_WEIGHTS = {
    # 最常见比分 (权重最高)
    (0, 0): 1.0,   # ~8%
    (1, 0): 1.2,   # ~11%  ← 最常见
    (1, 1): 1.0,   # ~8%
    (2, 0): 1.0,   # ~8%
    (2, 1): 1.1,   # ~9%
    (0, 1): 0.9,   # ~7%
    (1, 2): 0.8,   # ~6%
    (0, 2): 0.7,   # ~5%
    (2, 2): 0.5,   # ~4%
    (3, 0): 0.7,   # ~5%
    (3, 1): 0.6,   # ~4%
    (0, 3): 0.5,   # ~3%
    (3, 2): 0.3,   # ~2%
    (2, 3): 0.25,  # ~2%
    (4, 0): 0.3,   # ~2%
    (4, 1): 0.2,   # ~1.5%
    (0, 4): 0.15,  # ~1%
    (1, 3): 0.4,   # ~3%
    (3, 3): 0.15,  # ~1%
    (5, 0): 0.1,   # <1%
    (0, 5): 0.08,  # <1%
    (4, 2): 0.15,  # ~1%
    (2, 4): 0.1,   # <1%
    (5, 1): 0.08,  # <1%
    (1, 4): 0.1,   # <1%
    (6, 0): 0.05,  # 罕见
    (0, 6): 0.03,  # 罕见
}

# 从经验权重计算经验概率
_total_empirical = sum(EMPIRICAL_SCORE_WEIGHTS.values())
EMPIRICAL_PROBS = {k: v / _total_empirical for k, v in EMPIRICAL_SCORE_WEIGHTS.items()}


def get_empirical_scores(home_elo_diff=None, style=None):
    """
    获取经验比分分布
    
    可根据Elo差调整分布:
      - Elo差大 → 加大强队零封弱队的概率(1-0, 2-0, 3-0)
      - Elo差小 → 回到基础分布 (均势)
    """
    probs = dict(EMPIRICAL_PROBS)
    
    if home_elo_diff is not None and abs(home_elo_diff) > 50:
        # Elo差显著时调整
        elo_factor = min(abs(home_elo_diff) / 200, 1.0)
        if home_elo_diff > 0:  # 主队更强
            for (hg, ag) in list(probs.keys()):
                if hg > ag and ag == 0:  # 主队零封胜
                    probs[(hg, ag)] *= (1.0 + elo_factor * 0.5)
                elif ag > hg:  # 客胜降权
                    probs[(hg, ag)] *= (1.0 - elo_factor * 0.3)
        else:  # 客队更强
            for (hg, ag) in list(probs.keys()):
                if ag > hg and hg == 0:
                    probs[(hg, ag)] *= (1.0 + elo_factor * 0.5)
                elif hg > ag:
                    probs[(hg, ag)] *= (1.0 - elo_factor * 0.3)
    
    # 归一化
    total = sum(probs.values())
    if total > 0:
        probs = {k: v / total for k, v in probs.items()}
    
    return probs


# ================================================================
# 7. 多模型融合比分预测 (主函数)
# ================================================================

def predict_scores_enhanced(home_team, away_team, 
                           result_value=None,  # 'home', 'draw', 'away' (来自主引擎)
                           stage='小组赛',
                           home_motive='', away_motive='',
                           goal_multiplier=1.0,
                           home_intensity=1.0, away_intensity=1.0,
                           poisson_weight=0.60,
                           empirical_weight=0.25,
                           elo_weight=0.15):
    """
    增强版比分预测 —— 多模型融合
    
    Args:
        home_team, away_team: 球队名
        result_value: 主引擎预测的赛果方向 (用于约束比分)
        stage: 比赛阶段
        home_motive, away_motive: 战意描述
        goal_multiplier, home_intensity, away_intensity: 战意调整参数
        poisson_weight: 泊松模型权重
        empirical_weight: 经验分布权重
        elo_weight: Elo直接推导权重
        
    Returns:
        dict: 比分预测结果
    """
    
    # ---- Step 1: 计算期望进球 ----
    exp_h, exp_a, elo_h, elo_a = calc_expected_goals(
        home_team, away_team, stage,
        home_motive, away_motive,
        goal_multiplier, home_intensity, away_intensity
    )
    
    elo_diff = elo_h - elo_a
    
    # ---- Step 2: 三个模型的比分概率矩阵 ----
    
    # 模型A: 双变量泊松 + Dixon-Coles 修正
    poisson_scores = {}
    for hg in range(8):
        for ag in range(8):
            # 双变量泊松
            prob = bivariate_poisson(hg, ag, exp_h, exp_a, BIVARIATE_COV)
            # Dixon-Coles 修正
            dc_adj = dixon_coles_adjustment(hg, ag, exp_h, exp_a, DIXON_COLES_RHO)
            prob *= dc_adj
            if prob > 0.001:
                poisson_scores[(hg, ag)] = prob
    
    # 归一化
    p_total = sum(poisson_scores.values())
    if p_total > 0:
        poisson_scores = {k: v / p_total for k, v in poisson_scores.items()}
    
    # 模型B: 经验分布 (根据Elo差调整)
    empirical_scores = get_empirical_scores(elo_diff)
    
    # 模型C: Elo直接推导 (用期望进球比例确定大致比分范围)
    elo_scores = {}
    elo_ratio = exp_h / max(exp_a, 0.01)
    for hg in range(8):
        for ag in range(8):
            # 用exp_h/exp_a作为λ的简化泊松
            prob = bivariate_poisson(hg, ag, exp_h, exp_a, 0.05)
            if prob > 0.001:
                elo_scores[(hg, ag)] = prob
    e_total = sum(elo_scores.values())
    if e_total > 0:
        elo_scores = {k: v / e_total for k, v in elo_scores.items()}
    
    # ---- Step 3: 加权融合 ----
    fused_scores = {}
    all_keys = set(list(poisson_scores.keys()) + 
                   list(empirical_scores.keys()) + 
                   list(elo_scores.keys()))
    
    for key in all_keys:
        p_prob = poisson_scores.get(key, 0)
        e_prob = empirical_scores.get(key, 0)
        el_prob = elo_scores.get(key, 0)
        
        fused = (p_prob * poisson_weight + 
                 e_prob * empirical_weight + 
                 el_prob * elo_weight)
        fused_scores[key] = fused
    
    # ---- Step 4: 庄家视角修正 ----
    # 庄家控制赔率的根本目的是不想输钱
    # 关键检测: 诱盘(吸引资金流向热门方) vs 阻盘(阻止资金流向冷门方)
    # 当检测到诱盘信号时，比分分布需要向反方向偏移
    
    # 从赔率结构推断庄家真实意图
    # 如果result_value指向热门方(赔率低的那边)，但赔率变动有诱盘迹象
    # 则实际比分可能向冷门方向偏移
    
    # 目前我们没有直接传入bookmaker_intent数据,
    # 但可以通过result_value与期望进球的对比来推断
    # 如果result_value与期望进球方向一致且赔率低 → 可能是诱盘
    
    # ---- Step 4b: 赛果约束 ----
    if result_value == 'home':
        for key in list(fused_scores.keys()):
            hg, ag = key
            if hg < ag:
                fused_scores[key] *= 0.1
            elif hg == ag:
                fused_scores[key] *= 0.6
    elif result_value == 'away':
        for key in list(fused_scores.keys()):
            hg, ag = key
            if hg > ag:
                fused_scores[key] *= 0.1
            elif hg == ag:
                fused_scores[key] *= 0.6
    elif result_value == 'draw':
        for key in list(fused_scores.keys()):
            hg, ag = key
            if hg != ag:
                fused_scores[key] *= 0.3
    
    # 重新归一化
    f_total = sum(fused_scores.values())
    if f_total > 0:
        fused_scores = {k: v / f_total for k, v in fused_scores.items()}
    
    # ---- Step 5: 排序输出 ----
    sorted_scores = sorted(fused_scores.items(), key=lambda x: -x[1])
    
    # 生成前N个比分
    top_n = []
    cumulative = 0
    for (hg, ag), prob in sorted_scores:
        top_n.append({
            'home_goals': hg,
            'away_goals': ag,
            'score': f"{home_team} {hg}-{ag} {away_team}",
            'probability': round(prob, 4),
        })
        cumulative += prob
        if len(top_n) >= 8 or cumulative > 0.90:
            break
    
    # ---- Step 6: 半场比分预测 ----
    # 半场进球 ≈ 全场的 38-45%
    ht_factor = 0.42
    ht_exp_h = exp_h * ht_factor
    ht_exp_a = exp_a * ht_factor
    
    ht_scores = []
    for hg in range(5):
        for ag in range(5):
            prob = bivariate_poisson(hg, ag, ht_exp_h, ht_exp_a, BIVARIATE_COV * 0.5)
            if prob > 0.02:
                ht_scores.append({
                    'home_goals': hg,
                    'away_goals': ag,
                    'score': f"{home_team} {hg}-{ag} {away_team}(HT)",
                    'probability': round(prob, 4),
                })
    ht_scores.sort(key=lambda x: -x['probability'])
    
    # ---- Step 7: 模型置信度评估 (v2) ----
    top_prob = top_n[0]['probability'] if top_n else 0
    second_prob = top_n[1]['probability'] if len(top_n) > 1 else 0
    prob_margin = top_prob - second_prob

    # 维度1: 首选概率本身 (越高越有信心)
    top_prob_score = min(top_prob * 2.5, 0.4)

    # 维度2: 概率差距 (拉开越大越有信心)
    margin_score = min(prob_margin * 3.0, 0.3)

    # 维度3: 模型一致性
    poisson_top = max(poisson_scores, key=poisson_scores.get) if poisson_scores else (0, 0)
    empirical_top = max(empirical_scores, key=empirical_scores.get) if empirical_scores else (0, 0)
    elo_top = max(elo_scores, key=elo_scores.get) if elo_scores else (0, 0)

    models_agree_set = {poisson_top, empirical_top, elo_top}
    if len(models_agree_set) == 1:
        consistency_score = 0.2
    elif len(models_agree_set) == 2:
        consistency_score = 0.1
    else:
        consistency_score = 0.0

    # 维度4: 期望进球区分度
    exp_diff = abs(exp_h - exp_a) / max(exp_h, exp_a, 0.01)
    diff_score = min(exp_diff * 0.15, 0.1)

    conf_score = top_prob_score + margin_score + consistency_score + diff_score
    conf_score = min(conf_score, 0.95)

    if conf_score >= 0.55:
        stars = 4
        conf_level = 'HIGH'
    elif conf_score >= 0.40:
        stars = 3
        conf_level = 'MED'
    elif conf_score >= 0.28:
        stars = 2
        conf_level = 'LOW'
    else:
        stars = 1
        conf_level = 'VERYLOW'

    return {
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
            'model_consistency': round(consistency_score, 3),
        },
        'models': {
            'poisson_expected': {'home': round(exp_h, 2), 'away': round(exp_a, 2)},
            'poisson_top': poisson_top,
            'empirical_top': empirical_top,
            'elo_top': elo_top,
        },
        'elo': {
            'home': elo_h,
            'away': elo_a,
            'diff': elo_h - elo_a,
        },
    }


# ================================================================
# 8. 批量预测 (读取predict.py的输出作为输入)
# ================================================================

def batch_predict(input_file=None):
    """
    批量读取预测数据，重新生成比分预测
    
    Args:
        input_file: 预测JSON文件路径，默认用最新的 predictions-latest.json
    """
    if input_file is None:
        input_file = DATA_DIR / 'predictions-latest.json'
    
    if not os.path.exists(input_file):
        print(f"❌ 找不到输入文件: {input_file}")
        return
    
    with open(input_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    matches = data.get('predictions', data if isinstance(data, list) else [])
    if isinstance(data, dict) and 'predictions' in data:
        matches = data['predictions']
    
    print(f"\n📥 加载 {len(matches)} 场比赛数据...")
    print(f"=" * 70)
    
    # ---- 庄家意图分析函数 ----
    def analyze_bookmaker_trap(factor_analysis, result_prediction):
        """
        从因子分析文本中提取庄家诱盘/阻盘信号
        
        返回:
            trap_info: dict with trap_detected, direction, confidence, signals
        """
        trap_info = {
            'trap_detected': False,
            'trap_direction': None,  # 'home' = 诱主(实际看好客), 'away' = 诱客(实际看好主)
            'anti_trap_pick': None,  # 反着买的建议
            'trap_confidence': 0.0,
            'signals': [],
            'bookmaker_margin': 0.0,
        }
        
        if not factor_analysis:
            return trap_info
        
        for line in factor_analysis:
            # 检测诱盘信号
            if '诱盘' in line:
                trap_info['trap_detected'] = True
                trap_info['signals'].append(line)
                if '诱客' in line or '诱客胜' in line:
                    trap_info['trap_direction'] = 'away'  # 诱客 → 实际主
                    trap_info['anti_trap_pick'] = 'home'
                    trap_info['trap_confidence'] += 0.2
                elif '诱主' in line:
                    trap_info['trap_direction'] = 'home'
                    trap_info['anti_trap_pick'] = 'away'
                    trap_info['trap_confidence'] += 0.2
            
            # 检测阻盘信号
            if '阻盘' in line:
                trap_info['signals'].append(line)
                if '阻客' in line:
                    # 阻客 = 庄家不想你买客 = 真看好客
                    if trap_info['trap_direction'] is None:
                        trap_info['trap_direction'] = 'away'
                    trap_info['trap_confidence'] += 0.15
                elif '阻主' in line or '阻盘' in line:
                    # 阻主 = 庄家不想你买主 = 真看好主
                    if trap_info['trap_direction'] is None:
                        trap_info['trap_direction'] = 'home'
                    trap_info['trap_confidence'] += 0.15
            
            # 检测高抽水（庄家风险规避）
            if '高抽水' in line:
                trap_info['signals'].append(line)
                # 高抽水=庄家没信心，防冷门
                trap_info['trap_confidence'] += 0.08
            
            # 检测反买信号
            if '反买' in line:
                trap_info['signals'].append(line)
                trap_info['trap_detected'] = True
                # 提取修正值
                import re
                fix_m = re.search(r'修正([+-]?[\d.]+)', line)
                if fix_m:
                    trap_info['trap_confidence'] += min(abs(float(fix_m.group(1))) * 0.5, 0.2)
            
            # 检测冷门
            if '冷门检测' in line and '防冷' in line:
                trap_info['signals'].append(line)
                trap_info['trap_confidence'] += 0.05
        
        trap_info['trap_confidence'] = min(trap_info['trap_confidence'], 0.8)
        return trap_info

    def decide_actual_result_from_trap(result_val, trap_info):
        """
        庄家视角: "庄家不想输钱"
        - 如果诱盘信号强: 反向购买
        - 如果阻盘信号强: 顺向购买
        """
        if not trap_info['trap_detected'] or trap_info['trap_confidence'] < 0.2:
            return result_val, 0  # 无诱盘信号，使用原预测
        
        # 诱盘强度足够
        trap_strength = trap_info['trap_confidence']
        
        if trap_info['anti_trap_pick']:
            # 反着买 = 庄家真实意图
            return trap_info['anti_trap_pick'], trap_strength
        
        # 有诱盘信号但没有明确方向，谨慎
        return result_val, trap_strength * 0.5

    enhanced_predictions = []
    for i, m in enumerate(matches):
        home = m.get('home_team', '?')
        away = m.get('away_team', '?')
        result_val = m.get('result_prediction', {}).get('value', None)
        result_pred = m.get('result_prediction', {})
        stage = m.get('stage', '小组赛')

        # 庄家视角分析
        factor_analysis = m.get('factor_analysis', [])
        trap_info = analyze_bookmaker_trap(factor_analysis, result_pred)
        actual_result_val, trap_strength = decide_actual_result_from_trap(result_val, trap_info)

        # 如果庄家意图明确，使用修正后的方向
        score_result_val = actual_result_val if trap_strength > 0.3 else result_val

        home_motive = ''
        away_motive = ''
        goal_mult = 1.0
        home_int = 1.0
        away_int = 1.0

        mot = m.get('motivation_analysis', {})
        if isinstance(mot, dict):
            home_motive = mot.get('home_motive', '')
            away_motive = mot.get('away_motive', '')
            goal_mult = mot.get('goal_multiplier', 1.0)
            home_int = mot.get('home_intensity', 1.0)
            away_int = mot.get('away_intensity', 1.0)

        print(f"  [{i+1:02d}/{len(matches)}] {home} vs {away}...", end=' ', flush=True)

        result = predict_scores_enhanced(
            home, away,
            result_value=score_result_val,
            stage=stage,
            home_motive=home_motive,
            away_motive=away_motive,
            goal_multiplier=goal_mult,
            home_intensity=home_int,
            away_intensity=away_int,
        )

        enhanced_predictions.append({
            'match': f"{home} vs {away}",
            'home_team': home,
            'away_team': away,
            'result_prediction': m.get('result_prediction', {}),
            'original_score': m.get('score_prediction', {}),
            'enhanced_score': result,
            'bookmaker_trap': trap_info,
            'result_with_trap_correction': score_result_val,
        })

        # 打印
        orig = m.get('score_prediction', {}).get('most_likely', '?')
        new = result['most_likely']['score'] if result['most_likely'] else '?'
        conf = result['confidence']['level']

        trap_mark = ''
        if trap_info['trap_detected']:
            if trap_info['anti_trap_pick'] == 'home':
                trap_mark = ' TRAP:庄家诱客!'
            elif trap_info['anti_trap_pick'] == 'away':
                trap_mark = ' TRAP:庄家诱主!'

        print(f"OK {orig} -> {new} ({conf}){trap_mark}")

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_file = DATA_DIR / f'scores-enhanced-{timestamp}.json'
    output = {
        'generate_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'input_file': str(input_file),
        'total_matches': len(enhanced_predictions),
        'model_params': {
            'bivariate_cov': BIVARIATE_COV,
            'dixon_coles_rho': DIXON_COLES_RHO,
            'home_advantage': HOME_ADVANTAGE,
            'poisson_weight': 0.60,
            'empirical_weight': 0.25,
            'elo_weight': 0.15,
        },
        'predictions': enhanced_predictions,
    }

    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*70}")
    print(f"  SAVED: {output_file}")
    print(f"{'='*70}")

    # 打印汇总
    print(f"\n{'='*70}")
    print(f"  ENHANCED SCORE PREDICTION SUMMARY (with BOOKMAKER TRAP analysis)")
    print(f"{'='*70}")
    
    trap_count = 0
    for p in enhanced_predictions:
        es = p['enhanced_score']
        top = es['most_likely']
        alts = es['alternatives']
        trap = p.get('bookmaker_trap', {})
        
        team_line = f"{p['home_team']} vs {p['away_team']}"
        print(f"\n  {team_line}")
        
        if top:
            print(f"    SCORE: {top['score']:25s} (p={top['probability']:.1%})")
        alt_str = ', '.join([f"{a['score']}({a['probability']:.0%})" for a in alts[:3]])
        print(f"    ALT:   {alt_str}")
        print(f"    CONF:  {es['confidence']['level']} ({es['confidence']['stars']}*)")
        print(f"    EXPG:  {p['home_team']}({es['expected_home_goals']}) vs {p['away_team']}({es['expected_away_goals']})")
        
        # 庄家视角
        if trap['trap_detected']:
            trap_count += 1
            dir_text = '诱客(实看主)' if trap['trap_direction'] == 'away' else '诱主(实看客)'
            print(f"    TRAP:  [{dir_text}] 置信度 {trap['trap_confidence']:.0%}")
            for sig in trap['signals'][:2]:
                print(f"           {sig}")
        
        # 原预测vs修正后
        orig_val = p.get('result_prediction', {}).get('value', '?')
        corrected_val = p.get('result_with_trap_correction', '?')
        if orig_val != corrected_val:
            print(f"    FIX:   原预测方向{orig_val} -> 庄家修正方向{corrected_val}")

    print(f"\n{'='*70}")
    print(f"  Total: {len(enhanced_predictions)} matches, {trap_count} with trap signals")
    print(f"{'='*70}")
    return output_file


# ================================================================
# 9. 单场测试 + 与旧模型对比
# ================================================================

def test_single_match():
    """单场测试：对比新旧模型"""
    test_cases = [
        # (主队, 客队, 赛果方向, 阶段, 旧模型预测比分)
        ('厄瓜多尔', '德国', 'home', '小组赛', '厄瓜多尔 2-1 德国'),  # ✅ 已中
        ('库拉索', '科特迪瓦', 'away', '小组赛', '库拉索 0-3 科特迪瓦'),  # 实际0-2
        ('突尼斯', '荷兰', 'away', '小组赛', '突尼斯 0-3 荷兰'),  # 实际1-3
        ('日本', '瑞典', 'home', '小组赛', '日本 1-0 瑞典'),  # 实际1-1 ❌
        ('巴拉圭', '澳大利亚', 'draw', '小组赛', '巴拉圭 0-0 澳大利亚'),  # 半场0-0
        ('土耳其', '美国', 'away', '小组赛', '土耳其 0-2 美国'),  # 半场1-1
    ]
    
    print(f"\n{'='*80}")
    print(f"  🎯 新旧比分模型对比测试")
    print(f"{'='*80}")
    
    for home, away, result_val, stage, orig_pred in test_cases:
        # 旧模型复制 (直接用if/else链模拟) 
        # 这里我们调用新模型 + 设置poisson_weight=1.0来模拟旧模型
        
        # 新模型 (全权重)
        new_result = predict_scores_enhanced(
            home, away, 
            result_value=result_val,
            stage=stage,
        )
        
        # 旧模型模拟 (仅泊松, 无经验, 无Elo)
        old_result = predict_scores_enhanced(
            home, away,
            result_value=result_val,
            stage=stage,
            poisson_weight=1.0,
            empirical_weight=0.0,
            elo_weight=0.0,
        )
        
        new_top = new_result['most_likely']['score'] if new_result['most_likely'] else '?'
        old_top = old_result['most_likely']['score'] if old_result['most_likely'] else '?'
        new_conf = new_result['confidence']['level']
        new_stars = new_result['confidence']['stars']
        old_conf = old_result['confidence']['level']
        old_stars = old_result['confidence']['stars']
        old_star_s = '*' * old_stars
        new_star_s = '*' * new_stars

        print(f"\n  {home:6s} vs {away:6s}")
        print(f"    旧模型: {old_top:28s} ({old_conf}) {old_star_s}")
        print(f"    +新模型: {new_top:28s} ({new_conf}) {new_star_s}")
        print(f"    实际:   {orig_pred:28s}")


# ================================================================
# 10. 命令行入口
# ================================================================

def main():
    if len(sys.argv) > 1 and sys.argv[1] == '--batch':
        # 批量模式
        input_file = sys.argv[2] if len(sys.argv) > 2 else None
        batch_predict(input_file)
    elif len(sys.argv) > 1 and sys.argv[1] == '--test':
        # 测试模式
        test_single_match()
    elif len(sys.argv) > 1 and sys.argv[1] == '--compare':
        # 对比历史预测
        print("对比模式: 读取已有predictions-*.json并重新比分预测")
        # 找最新的预测文件
        pred_files = sorted(DATA_DIR.glob('predictions-*.json'))
        if pred_files:
            latest = pred_files[-1]
            print(f"使用: {latest}")
            batch_predict(latest)
        else:
            print("❌ 未找到预测文件")
    else:
        # 默认: 单场测试+对比
        test_single_match()


if __name__ == '__main__':
    main()
