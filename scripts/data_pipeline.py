#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
⚡ 波神数据管道修复 v1 — 直接注入结构化赔率数据

问题: engine.py 从 predictions-latest.json 的文本中解析赔率，导致赔率数据丢失
修复: 直接从 odds-latest.json 拿结构化的赔率数据注入 engine.py

用法:
  python scripts/data_pipeline.py              # 完整运行: 采集→预测
  python scripts/data_pipeline.py --fetch-only  # 仅采集
  python scripts/data_pipeline.py --predict     # 仅用最新数据预测
"""

import json, re, sys, os
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
PROJECT_DIR = SCRIPT_DIR.parent
DATA_DIR = PROJECT_DIR / "data"

sys.path.insert(0, str(SCRIPT_DIR))

# ================================================================
# 1. 采集 (从 cp.nowscore.com)
# ================================================================

def fetch_cp_nowscore(date_param: str = None) -> str:
    """从 cp.nowscore.com 获取竞彩页面HTML"""
    import urllib.request
    
    url = "https://cp.nowscore.com/"
    if date_param:
        url += f"?typeID=101&oddstype=2&date={date_param}"
    
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read().decode('utf-8', errors='replace')


def parse_matches(html: str) -> list:
    """解析竞彩HTML → 结构化比赛数据"""
    parts = re.split(r'<tr[^>]*?id="row_(\d+)"[^>]*?>', html)
    matches = []
    
    for i in range(1, len(parts) - 1, 2):
        content = parts[i+1]
        
        # 基础信息
        lg_m = re.search(r"<a[^>]*href='https://info\.nowscore\.com[^>]*>([^<]+)</a>", content)
        dt_m = re.search(r'开赛时间[：:]([^"]+)', content)
        hm_m = re.search(r'id="HomeTeam_\d+"[^>]*>([^<]+)<', content)
        sc_m = re.search(r'font-weight:bold;color:[^;]+;"[^>]*>([^<]+)<', content)
        aw_m = re.search(r'class="dz14"[^>]*id="GuestTeam_\d+"[^>]*>([^<]+)<', content)
        hp_m = re.search(r'<font[^>]*color=(green|red)[^>]*>([+-]?\d+)<', content)
        ns_m = re.search(r'HomeTeam_(\d+)', content)
        num_m = re.search(r'hideRow.*?>(\d{3})<', content)
        
        # SPF 赔率 (竞彩)
        oh = re.search(r'cell_\d+_52.*?sp_\d+_52[^>]*>([\d.]+)<', content)
        od = re.search(r'cell_\d+_53.*?sp_\d+_53[^>]*>([\d.]+)<', content)
        oa = re.search(r'cell_\d+_54.*?sp_\d+_54[^>]*>([\d.]+)<', content)
        
        # RQ 赔率 (让球)
        rh = re.search(r'cell_\d+_1.*?sp_\d+_1[^>]*>([\d.]+)<', content)
        rd = re.search(r'cell_\d+_2.*?sp_\d+_2[^>]*>([\d.]+)<', content)
        ra = re.search(r'cell_\d+_3.*?sp_\d+_3[^>]*>([\d.]+)<', content)
        
        ht = hm_m.group(1).strip() if hm_m else ''
        at = aw_m.group(1).strip() if aw_m else ''
        if not ht and not at:
            continue
        
        score = sc_m.group(1).strip() if sc_m else '-'
        
        # 抽水率计算
        margin = 0
        if oh and od and oa:
            try:
                h, d, a = float(oh.group(1)), float(od.group(1)), float(oa.group(1))
                margin = round(1/h + 1/d + 1/a - 1, 4)
            except:
                pass
        
        match = {
            'schedule_id': ns_m.group(1) if ns_m else '',
            'match_no': num_m.group(1) if num_m else '',
            'home_team': ht,
            'away_team': at,
            'league': lg_m.group(1).strip() if lg_m else '',
            'date': dt_m.group(1).strip() if dt_m else '',
            'score': score,
            'handicap': hp_m.group(2) if hp_m else '0',
            'jingcai': {
                'home_win': float(oh.group(1)) if oh else 0,
                'draw': float(od.group(1)) if od else 0,
                'away_win': float(oa.group(1)) if oa else 0,
            } if oh else {},
            'rq': {
                'home_win': float(rh.group(1)) if rh else 0,
                'draw': float(rd.group(1)) if rd else 0,
                'away_win': float(ra.group(1)) if ra else 0,
            } if rh else {},
            'margin': margin,
            'prob': {},
        }
        
        # 计算隐含概率
        if match['jingcai'] and match['jingcai']['home_win'] > 0:
            h, d, a = match['jingcai']['home_win'], match['jingcai']['draw'], match['jingcai']['away_win']
            total = 1/h + 1/d + 1/a
            match['prob'] = {
                'home': round((1/h)/total, 4),
                'draw': round((1/d)/total, 4),
                'away': round((1/a)/total, 4),
            }
        
        matches.append(match)
    
    return matches


# ================================================================
# 1.5 多源赔率采集 ★v3新增
# ================================================================

def fetch_europe_odds(match_id: str) -> dict:
    """从 live.nowscore.com 获取欧赔/亚盘/大小球"""
    import urllib.request
    url = f"https://live.nowscore.com/odds/match/{match_id}.htm"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode('utf-8', errors='replace')
    except:
        return {}
    
    def sh(t): return re.sub(r'<[^>]+>', '', t).strip()
    result = {'asian': {}, 'euro': {}, 'overunder': {}, 'companies': []}
    
    tbl = re.search(r'<table[^>]*oddstablebox[^>]*>(.*?)</table>', html, re.DOTALL|re.IGNORECASE)
    if not tbl:
        return result
    
    for row in re.findall(r'<tr[^>]*datatr[^>]*>(.*?)</tr>', tbl.group(1), re.DOTALL|re.IGNORECASE):
        name_m = re.search(r'cpy[^>]*>([^<]+)', row)
        if not name_m:
            continue
        tds = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL|re.IGNORECASE)
        if len(tds) < 19: continue
        co = {'name': name_m.group(1).strip()}
        co['euro_initial'] = {'home': sh(tds[7]), 'draw': sh(tds[8]), 'away': sh(tds[9])}
        co['euro_current'] = {'home': sh(tds[10]), 'draw': sh(tds[11]), 'away': sh(tds[12])}
        co['asian_initial'] = {'home': sh(tds[1]), 'handicap': sh(tds[2]), 'away': sh(tds[3])}
        co['asian_current'] = {'home': sh(tds[4]), 'handicap': sh(tds[5]), 'away': sh(tds[6])}
        co['ou_initial'] = {'over': sh(tds[13]), 'line': sh(tds[14]), 'under': sh(tds[15])}
        co['ou_current'] = {'over': sh(tds[16]), 'line': sh(tds[17]), 'under': sh(tds[18])}
        result['companies'].append(co)
    
    for co in result['companies'][:8]:
        for kw in ['澳*', '36*', '皇G', '立B', '伟D', '威*']:
            if kw in co['name']:
                for k, sk in [('euro', 'euro'), ('asian', 'asian'), ('overunder', 'ou')]:
                    result[k] = {'initial': co[f'{sk}_initial'], 'current': co[f'{sk}_current'], 'company': co['name']}
                break
        if result.get('euro'): break
    if not result.get('euro') and result['companies']:
        c = result['companies'][0]
        for k, sk in [('euro', 'euro'), ('asian', 'asian'), ('overunder', 'ou')]:
            result[k] = {'initial': c[f'{sk}_initial'], 'current': c[f'{sk}_current'], 'company': c['name']}
    return result


def compare_odds_sources(jingcai: dict, euro: dict) -> dict:
    """对比竞彩vs欧赔, 检测差异"""
    res = {'jingcai_prob': {}, 'euro_prob': {}, 'jingcai_vs_euro': {}, 'signals': [], 'direction_match': True}
    jc = jingcai
    eu = euro.get('current', {})
    try:
        jh, jd, ja = float(jc.get('home_win',0)), float(jc.get('draw',0)), float(jc.get('away_win',0))
        eh, ed, ea = float(eu.get('home',0)), float(eu.get('draw',0)), float(eu.get('away',0))
    except:
        return res
    if jh == 0 or eh == 0: return res
    
    jt = 1/jh + 1/jd + 1/ja
    et = 1/eh + 1/ed + 1/ea
    jp = {'home': (1/jh)/jt, 'draw': (1/jd)/jt, 'away': (1/ja)/jt}
    ep = {'home': (1/eh)/et, 'draw': (1/ed)/et, 'away': (1/ea)/et}
    res['jingcai_prob'] = {k: round(v,4) for k,v in jp.items()}
    res['euro_prob'] = {k: round(v,4) for k,v in ep.items()}
    res['jingcai_margin'] = round(jt-1, 3)
    res['euro_margin'] = round(et-1, 3)
    
    for key, lb in [('home','主胜'),('draw','平局'),('away','客胜')]:
        diff = round(jp[key] - ep[key], 4)
        res['jingcai_vs_euro'][key] = {'jingcai': round(jp[key],4), 'euro': round(ep[key],4), 'diff': diff}
        if abs(diff) > 0.03:
            if diff > 0: res['signals'].append(f"{lb} 竞彩比欧赔更看好(+{abs(diff):.0%})")
            else: res['signals'].append(f"{lb} 欧赔比竞彩更看好(+{abs(diff):.0%})")
    
    mj, me = max(jp, key=jp.get), max(ep, key=ep.get)
    res['direction_match'] = (mj == me)
    if not res['direction_match']:
        res['signals'].append(f"⚡ 方向分歧: 竞彩→{mj}, 欧赔→{me}")
    elif abs(res.get('jingcai_margin',0) - res.get('euro_margin',0)) > 0.01:
        jm = res.get('jingcai_margin',0)
        em = res.get('euro_margin',0)
        if jm > em:
            res['signals'].append(f"欧赔抽水{em:.1%}比竞彩{jm:.1%}更低, 欧赔更高效")
        else:
            res['signals'].append(f"竞彩抽水{jm:.1%}比欧赔{em:.1%}更低, 竞彩更高效")
    return res


# ================================================================
# 2. 注入赔率数据到 engine.py 可读的格式
# ================================================================

# ================================================================
# 2.5 赔率变动追踪 (Odds Movement Tracking) ★v2新增
# ================================================================

ODDS_HISTORY_FILE = DATA_DIR / 'odds-history.json'


def load_odds_history() -> dict:
    """加载历史赔率快照"""
    if ODDS_HISTORY_FILE.exists():
        try:
            with open(ODDS_HISTORY_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return {'snapshots': [], 'latest_match_odds': {}}


def save_odds_snapshot(matches: list):
    """保存当前赔率为历史快照"""
    history = load_odds_history()
    
    # 构建当前快照: match_id -> {jingcai, rq}
    current = {}
    for m in matches:
        key = m['match_no'] or f"{m['home_team']}_{m['away_team']}"
        hcap = 0
        try:
            hcap = float(m.get('handicap', 0))
        except:
            pass
        current[key] = {
            'home_team': m['home_team'],
            'away_team': m['away_team'],
            'jingcai': m.get('jingcai', {}),
            'rq': m.get('rq', {}),
            'handicap': hcap,
            'timestamp': datetime.now().isoformat(),
        }
    
    # 保存快照
    snapshot = {
        'timestamp': datetime.now().isoformat(),
        'match_count': len(matches),
        'odds': current,
    }
    history['snapshots'].append(snapshot)
    # 只保留最近50个快照
    if len(history['snapshots']) > 50:
        history['snapshots'] = history['snapshots'][-50:]
    
    # 更新latest
    history['latest_match_odds'] = current
    
    with open(ODDS_HISTORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def detect_odds_movements(new_matches: list) -> dict:
    """检测赔率变动
    
    Returns:
        {match_key: {
            'spf': {'home': 变幅, 'draw': 变幅, 'away': 变幅},
            'movement_dir': 'home'/'away'/None,  # 资金流向方向
            'movement_strength': 'STRONG'/'MEDIUM'/'WEAK'/None,
            'analysis': '描述文本'
        }}
    """
    history = load_odds_history()
    prev = history.get('latest_match_odds', {})
    if not prev:
        # 首次运行, 无历史对比
        return {}
    
    movements = {}
    
    for m in new_matches:
        key = m['match_no'] or f"{m['home_team']}_{m['away_team']}"
        old = prev.get(key)
        if not old:
            continue
        
        # 对比SPF赔率
        old_jc = old.get('jingcai', {})
        new_jc = m.get('jingcai', {})
        
        if not old_jc or not new_jc:
            continue
        
        old_h = old_jc.get('home_win', 0)
        old_d = old_jc.get('draw', 0)
        old_a = old_jc.get('away_win', 0)
        new_h = new_jc.get('home_win', 0)
        new_d = new_jc.get('draw', 0)
        new_a = new_jc.get('away_win', 0)
        
        if old_h == 0 or new_h == 0:
            continue
        
        # 计算变动
        dh = round(new_h - old_h, 2)  # 正=升(看衰), 负=降(看好)
        dd = round(new_d - old_d, 2)
        da = round(new_a - old_a, 2)
        
        # 判断方向
        movement_dir = None
        movement_strength = None
        signals = []
        
        # 主胜赔率下降 → 资金追捧主队
        if dh <= -0.05:
            strength = 'STRONG' if dh <= -0.15 else 'MEDIUM'
            signals.append(f'主胜降{abs(dh):.2f} → 资金追捧主队')
            if movement_dir is None:
                movement_dir = 'home'
                movement_strength = strength
        elif dh >= 0.08:
            signals.append(f'主胜升{dh:.2f} → 主队不被看好')
        
        # 客胜赔率下降 → 资金追捧客队
        if da <= -0.05:
            strength = 'STRONG' if da <= -0.15 else 'MEDIUM'
            signals.append(f'客胜降{abs(da):.2f} → 资金追捧客队')
            if movement_dir is None or (movement_dir == 'away' and strength == 'STRONG'):
                movement_dir = 'away'
                movement_strength = strength
        elif da >= 0.08:
            signals.append(f'客胜升{da:.2f} → 客队不被看好')
        
        # 平赔变动
        if dd <= -0.08:
            signals.append(f'平赔降{abs(dd):.2f} → 资金防范平局')
        elif dd >= 0.10:
            signals.append(f'平赔升{dd:.2f} → 排除平局')
        
        # 综合判断: 胜负方向冲突时, 降幅大的一方胜出
        if movement_dir == 'home' and da <= -0.05 and abs(dh) < abs(da):
            movement_dir = 'away'
            movement_strength = 'STRONG' if da <= -0.15 else 'MEDIUM'
        elif movement_dir == 'away' and dh <= -0.05 and abs(da) < abs(dh):
            movement_dir = 'home'
            movement_strength = 'STRONG' if dh <= -0.15 else 'MEDIUM'
        
        if not signals:
            # 有历史数据但无显著变动
            signals.append('赔率稳定, 无明显变动')
            movement_strength = 'STABLE'
        
        movements[key] = {
            'spf_change': {'home': dh, 'draw': dd, 'away': da},
            'movement_dir': movement_dir,
            'movement_strength': movement_strength,
            'signals': signals,
            'analysis': '; '.join(signals),
        }
    
    return movements


def apply_odds_movements(input_data: dict, movements: dict):
    """将赔率变动信息注入引擎输入数据"""
    for p in input_data['predictions']:
        key = p['match_no'] or f"{p['home_team']}_{p['away_team']}"
        mov = movements.get(key)
        if mov:
            p['odds_movement'] = mov
            # 也追加到 factor_analysis
            p.setdefault('factor_analysis', [])
            for sig in mov['signals']:
                p['factor_analysis'].append(f"odds_movement: {sig}")


def build_engine_input(matches: list) -> dict:
    """构建 engine.py 可直接读取的结构化输入"""
    engine_matches = []
    
    for m in matches:
        # 确定比赛阶段 (v4修复: 根据小组赛完成情况自动检测)
        stage = detect_stage()
        
        # 计算 Elo 期望
        elo_h = get_team_elo_local(m['home_team'])
        elo_a = get_team_elo_local(m['away_team'])
        exp_h = 1.0 / (1.0 + 10.0 ** ((elo_a - elo_h) / 400.0))
        
        # 构建 factor_analysis (给engine.py的文本解析层用，作为fallback)
        factor_lines = []
        if m['prob']:
            max_prob = max(m['prob'].values())
            if m['prob']['home'] == max_prob:
                factor_lines.append(f"odds_implied: ->home (cf={max_prob:.2f}, w=0.35)")
            elif m['prob']['away'] == max_prob:
                factor_lines.append(f"odds_implied: ->away (cf={max_prob:.2f}, w=0.35)")
        
        # 战意分析 (v4修复: 根据小组积分自动推算真实战意)
        motive_lines = []
        home_motive = infer_team_motivation(m['home_team'], m['away_team'])
        away_motive = infer_team_motivation(m['away_team'], m['home_team'])
        motive_lines.append(f"{m['home_team']}: {home_motive}")
        motive_lines.append(f"{m['away_team']}: {away_motive}")
        
        # ── 从赔率数据计算初始预测 ──
        init_val = ''
        init_conf = 0.0
        init_audit = {}

        if m['prob'] and m['prob'].get('home', 0) > 0:
            hp = m['prob'].get('home', 0)
            dp = m['prob'].get('draw', 0)
            ap = m['prob'].get('away', 0)
            sorted_p = sorted([hp, dp, ap], reverse=True)
            margin = sorted_p[0] - sorted_p[1]

            if sorted_p[0] == hp:
                init_val = 'home'
            elif sorted_p[0] == ap:
                init_val = 'away'
            else:
                init_val = 'draw'

            # 置信度直接映射: 概率差决定可信度
            if margin < 0.03:
                init_conf = 0.0       # 无信号
            elif margin < 0.08:
                init_conf = 0.35      # 弱信号
            elif margin < 0.15:
                init_conf = 0.50      # 可用
            elif margin < 0.25:
                init_conf = 0.65      # 良好
            else:
                init_conf = min(0.70 + (margin - 0.25) * 0.5, 0.90)  # 强信号
            init_audit = {
                'source': 'jingcai_spf', 'prob_margin': round(margin, 3),
                'home_prob': hp, 'draw_prob': dp, 'away_prob': ap,
            }
        elif m['rq'] and m['rq'].get('home_win', 0) > 0:
            # 备用: RQ让球赔率
            hp = 1.0 / m['rq']['home_win'] if m['rq']['home_win'] > 0 else 0
            dp = 1.0 / m['rq']['draw'] if m['rq']['draw'] > 0 else 0
            ap = 1.0 / m['rq']['away_win'] if m['rq']['away_win'] > 0 else 0
            total = hp + dp + ap
            if total > 0:
                hp, dp, ap = hp / total, dp / total, ap / total
                sorted_p = sorted([hp, dp, ap], reverse=True)
                margin = sorted_p[0] - sorted_p[1]
                if sorted_p[0] == hp:
                    init_val = 'home'
                elif sorted_p[0] == ap:
                    init_val = 'away'
                else:
                    init_val = 'draw'
                init_conf = min(0.30 + margin * 1.2, 0.70)
                init_audit = {
                    'source': 'jingcai_rq', 'prob_margin': round(margin, 3),
                    'home_prob': hp, 'draw_prob': dp, 'away_prob': ap,
                }
        elif abs(elo_h - elo_a) > 80:
            # 无赔率时用Elo兜底
            init_val = 'home' if elo_h > elo_a else 'away'
            init_conf = 0.40
            init_audit = {'source': 'elo_fallback', 'prob_margin': 0, 'elo_diff': elo_h - elo_a}

        engine_matches.append({
            'match': f"{m['home_team']} vs {m['away_team']}",
            'match_no': m['match_no'],
            'home_team': m['home_team'],
            'away_team': m['away_team'],
            'stage': stage,
            'schedule_id': m['schedule_id'],
            'jingcai': m['jingcai'],
            'rq': m['rq'],
            'handicap': m.get('handicap', 0),
            'margin': m['margin'],
            'prob': m['prob'],
            'odds_comparison': m.get('odds_comparison', {}),
            'elo': {
                'home': elo_h, 'away': elo_a,
                'diff': elo_h - elo_a,
                'expected': round(exp_h, 4),
                'big_gap': abs(elo_h - elo_a) > 80,
            },
            'factor_analysis': factor_lines,
            'motivation_analysis': motive_lines,
            'result_prediction': {
                'prediction': init_val,
                'value': init_val,
                'confidence': round(init_conf, 3),
                'votes': {'home': 0, 'draw': 0, 'away': 0},
                'initial_audit': init_audit,
            },
            'score_prediction': {},
            'over_under_prediction': {},
            'ht_prediction': {},
            'htft_prediction': {},
        })
    
    current_stage = detect_stage()
    return {
        'predict_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'predict_date': datetime.now().strftime('%Y-%m-%d'),
        'stage': current_stage,
        'total_matches': len(engine_matches),
        'predictions': engine_matches,
    }


# ================================================================
# 3. Elo 工具
# ================================================================

TEAM_ALIASES = {'阿尔及利': '阿尔及利亚', '乌兹别克': '乌兹别克斯坦'}

# 基础 Elo (世界杯48队)
INITIAL_ELO = {
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
    # 芬兰联赛球队 (估算Elos)
    '坦山猫': 1300, '塞伊奈': 1280, '赫尔辛基': 1320, '库奥皮奥': 1280,
    '玛丽港': 1240, '国际图尔': 1260, 'TPS图尔': 1240, '雅罗': 1220,
    '赫尔火花': 1260, '瓦萨': 1280, 'AC奥卢': 1240, '拉赫蒂': 1250,
    '阿尔及利': 1500, '乌兹别克': 1460,
}


def get_team_elo_local(team_name):
    name = TEAM_ALIASES.get(team_name, team_name)
    # 先从本地保存的 elo-data.json 读
    elo_file = DATA_DIR / 'elo-data.json'
    if elo_file.exists():
        try:
            with open(elo_file, 'r', encoding='utf-8') as f:
                elo_dict = json.load(f)
            if name in elo_dict:
                return elo_dict[name]
        except:
            pass
    return INITIAL_ELO.get(name, 1500)


def detect_stage() -> str:
    """
    根据 group-standings.json 自动检测当前比赛阶段
    
    规则:
    - 如果所有球队 MP < 3 → '小组赛'
    - 如果所有球队 MP >= 3 → 根据日期推断淘汰赛轮次
    """
    standings_file = DATA_DIR / 'group-standings.json'
    if not standings_file.exists():
        return '小组赛'
    try:
        with open(standings_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except:
        return '小组赛'
    
    all_mp_complete = True
    for gid, grp in data.get('standings', {}).items():
        for t in grp:
            mp = t.get('mp', 0)
            if mp < 3:
                all_mp_complete = False
                break
        if not all_mp_complete:
            break
    
    if not all_mp_complete:
        return '小组赛'
    
    # 小组赛已全部完成 → 根据当前日期判断淘汰赛轮次
    now = datetime.now()
    # 2026世界杯: 6月11-27日小组赛, 6月29-7月2日32强, 7月4-5日16强,
    # 7月8-9日8强, 7月13-14日半决赛, 7月19日决赛
    stage_map = [
        (6, 11, '小组赛'),    # 6/11-
        (6, 29, '32强'),      # 6/29-
        (7, 4,  '16强'),      # 7/4-
        (7, 8,  '8强'),       # 7/8-
        (7, 13, '半决赛'),    # 7/13-
        (7, 19, '决赛'),      # 7/19
    ]
    
    current_stage = '淘汰赛'
    for month, day, s in reversed(stage_map):
        if now.month >= month and now.day >= day:
            current_stage = s
            break
    return current_stage


def infer_team_motivation(team: str, opponent: str) -> str:
    """
    根据小组积分榜自动推算球队战意
    
    使用 group-standings.json 判断:
    - 已提前出线 → '已出线, 可能留力轮换'
    - 取胜即可出线 → '取胜即可确保出线'
    - 打平即可出线 → '保平即可出线'
    - 背水一战 → '背水一战, 必须取胜'
    - 已被淘汰 → '荣誉之战, 为尊严而战'
    - 需要看其他场次结果 → '出线形势复杂, 需看同组其他场次'
    """
    standings_file = DATA_DIR / 'group-standings.json'
    if not standings_file.exists():
        return '小组赛争夺积分'
    
    try:
        with open(standings_file, 'r', encoding='utf-8') as f:
            standings_data = json.load(f)
    except:
        return '小组赛争夺积分'
    
    # 找球队所在的组
    team_group = None
    team_info = None
    for gid, grp in standings_data.get('standings', {}).items():
        for t in grp:
            if t.get('team_cn', '') == team:
                team_group = gid
                team_info = t
                break
        if team_group:
            break
    
    if not team_group or not team_info:
        return '小组赛争夺积分'
    
    mp = team_info.get('mp', 0)
    pts = team_info.get('pts', 0)
    gd = team_info.get('gd', 0)
    
    # 计算最大可得积分
    max_possible_pts = pts  # 小组赛已结束
    
    # 如果是淘汰赛阶段 (mp=3 小组赛已结束)
    # 所有小组赛完成, 应看是否晋级
    grp = standings_data['standings'][team_group]
    # 前两名直接晋级
    rank = team_info.get('rank', 99)
    
    if mp >= 3:
        # 小组赛结束, 看排名
        if rank <= 2:
            # 已晋级
            # 查看是否锁定小组第一
            top_two = sorted(grp, key=lambda x: (-x['pts'], -x['gd']))[:2]
            if top_two[0].get('team_cn', '') == team and top_two[0]['pts'] > top_two[1]['pts']:
                return '已锁定小组第一, 淘汰赛蓄力'
            elif rank == 1:
                return '小组第一, 淘汰赛全力以赴'
            elif rank == 2:
                return '小组第二晋级, 淘汰赛全力争胜'
            else:
                return '已晋级淘汰赛'
        else:
            # 3,4名 → 已被淘汰
            return '已被淘汰, 荣誉之战'
    
    # 小组赛进行中
    if mp <= 1:
        return '首战, 全力争胜'
    
    # 第2/3轮: 根据积分判断
    remaining = 3 - mp
    remaining_pts = remaining * 3
    
    # 看前两名分数
    top_2nd = sorted(grp, key=lambda x: (-x['pts'], -x['gd']))[1]
    top_2nd_pts = top_2nd['pts']
    
    if pts + remaining_pts < top_2nd_pts:
        return '已被淘汰, 荣誉之战'
    
    # 判断当前排名
    # 第一名
    top_1st = sorted(grp, key=lambda x: (-x['pts'], -x['gd']))[0]
    if top_1st.get('team_cn', '') == team:
        return '暂列第一, 争取锁定头名'
    
    if rank <= 2:
        # 在出线区
        pts_gap = pts - grp[2]['pts'] if len(grp) > 2 else 99
        if pts_gap >= 3 + remaining_pts:
            return '已提前出线, 可能留力轮换'
        elif pts_gap >= 3:
            return '出线形势乐观, 保平即可'
        else:
            return '出线关键战, 不容有失'
    else:
        # 在淘汰区
        pts_to_catch = top_2nd_pts - pts
        if pts_to_catch <= 3:
            if remaining >= 1:
                return '背水一战, 必须取胜才有出线希望'
            else:
                return '最后一搏, 全力争胜'
        else:
            return '出线希望渺茫, 荣誉之战'


# ================================================================
# 4. 运行 engine.py 预测
# ================================================================

def run_engine(input_data: dict) -> dict:
    """调用 engine.py 的预测逻辑"""
    try:
        from engine import analyze_match
        
        results = []
        trap_count = 0
        
        for raw in input_data['predictions']:
            ctx = analyze_match(raw)
            results.append(ctx)
            if ctx.bookmaker['trap_detected']:
                trap_count += 1
        
        # 构建输出
        output = {
            'generate_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'total_matches': len(results),
            'trap_detected': trap_count,
            'model': '波神v4-数据管道修复版',
            'predictions': [],
        }
        
        for ctx in results:
            # 多层因子审计
            audit = audit_prediction(ctx)
            
            # ── 从比分概率推导 大小球 + 半全场 ──
            sp = ctx.score_prediction or {}
            all_scores = sp.get('all_scores', [])
            ht_pred = sp.get('ht_prediction', {})
            most_likely = sp.get('most_likely', {})
            
            # 大小球: 从全部分数概率累计
            over_prob = 0.0
            under_prob = 0.0
            for s in all_scores:
                hg = s.get('home_goals', 0)
                ag = s.get('away_goals', 0)
                prob = s.get('probability', 0)
                if hg + ag >= 3:
                    over_prob += prob
                else:
                    under_prob += prob
            total_ou = over_prob + under_prob
            if total_ou > 0:
                over_prob /= total_ou
                under_prob /= total_ou
            
            ou_prediction = {
                'over': round(over_prob, 3),
                'under': round(under_prob, 3),
                'verdict': '大球' if over_prob > under_prob else '小球',
                'confidence': round(max(over_prob, under_prob), 2),
            }
            
            # 半全场: 从半场预测 + 全场比分推导
            ft_h = most_likely.get('home_goals', 0)
            ft_a = most_likely.get('away_goals', 0)
            ht_h = ht_pred.get('home_goals', 0)
            ht_a = ht_pred.get('away_goals', 0)
            
            def result_label(h, a):
                if h > a: return '胜'
                if h < a: return '负'
                return '平'
            
            ht_result = result_label(ht_h, ht_a)
            ft_result = result_label(ft_h, ft_a)
            htft_prediction = {
                'ht_score': f"{ht_h}-{ht_a}",
                'ft_score': f"{ft_h}-{ft_a}",
                'ht_result': ht_result,
                'ft_result': ft_result,
                'htft': f"{ht_result}{ft_result}",
                'confidence': round(sp.get('confidence', {}).get('score', 0), 2),
            }
            
            output['predictions'].append({
                'match': f"{ctx.home_team} vs {ctx.away_team}",
                'match_no': ctx.match_no,
                'home_team': ctx.home_team,
                'away_team': ctx.away_team,
                'stage': ctx.stage,
                'result_prediction': ctx.result_prediction,
                'score_prediction': sp,
                'ou_prediction': ou_prediction,
                'htft_prediction': htft_prediction,
                'bookmaker_analysis': ctx.bookmaker,
                'elo': ctx.elo,
                'odds_summary': {
                    'home_prob': ctx.odds_features.get('home_prob', 0),
                    'draw_prob': ctx.odds_features.get('draw_prob', 0),
                    'away_prob': ctx.odds_features.get('away_prob', 0),
                    'margin': ctx.odds_features.get('margin', 0),
                    'handicap': ctx.asian_features.get('handicap_text', ''),
                    'signal_strength': ctx.odds_features.get('signal_strength', ''),
                    'movement_dir': ctx.odds_features.get('movement_dir'),
                    'movement_strength': ctx.odds_features.get('movement_strength'),
                    'movement_analysis': ctx.odds_features.get('movement_analysis', ''),
                    'data_source': ctx.odds_features.get('data_source', 'SPF'),
                    'odds_comparison': ctx.raw.get('odds_comparison', {}),
                },
                'audit': audit,
            })
        
        return output
    except ImportError as e:
        print(f'  ⚠️ engine.py 加载失败: {e}')
        print(f'  使用简化预测模式')
        return simple_predict(input_data)


def simple_predict(input_data: dict) -> dict:
    """简化预测模式 (当engine.py不可用时)"""
    output = {
        'generate_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'total_matches': 0,
        'trap_detected': 0,
        'model': '波神-简化版',
        'predictions': [],
    }
    
    for raw in input_data['predictions']:
        prob = raw.get('prob', {})
        elo = raw.get('elo', {})
        
        # 直接根据概率判断
        if prob:
            max_key = max(prob, key=prob.get)
            conf = round(prob[max_key], 2)
            val_map = {'home': 'home', 'draw': 'draw', 'away': 'away'}
            result_val = val_map.get(max_key, 'draw')
        else:
            # 退回到Elo
            if elo.get('diff', 0) > 50:
                result_val = 'home'
                conf = 0.55
            elif elo.get('diff', 0) < -50:
                result_val = 'away'
                conf = 0.55
            else:
                result_val = 'draw'
                conf = 0.33
        
        output['predictions'].append({
            'match': raw['match'],
            'match_no': raw['match_no'],
            'home_team': raw['home_team'],
            'away_team': raw['away_team'],
            'stage': raw['stage'],
            'result_prediction': {
                'value': result_val,
                'confidence': round(conf, 3),
            },
            'score_prediction': {'most_likely': None},
            'bookmaker_analysis': {'trap_detected': False},
            'elo': elo,
            'odds_summary': {
                'home_prob': prob.get('home', 0),
                'draw_prob': prob.get('draw', 0),
                'away_prob': prob.get('away', 0),
                'margin': raw.get('margin', 0),
            },
        })
        output['total_matches'] += 1
    
    return output


# ================================================================
# 5. 打印输出
# ================================================================

# ================================================================
# 5a. 多层因子审计系统
# ================================================================

def audit_prediction(ctx_or_dict) -> dict:
    """
    对单个预测进行多层因子审核, 返回审计报告。
    检查项:
      1. 赔率信号强度 (prob_margin)
      2. 数据源完整性
      3. 竞彩vs欧赔方向一致性
      4. 赔率变动方向确认
      5. Elo差距一致性
      6. 庄家诱盘风险
      7. 抽水率
    """
    # 统一输入: 可能是ctx对象或dict
    if hasattr(ctx_or_dict, 'raw'):
        ctx = ctx_or_dict
        raw = ctx.raw
        rp = ctx.result_prediction
        odds = ctx.odds_features
        bm = ctx.bookmaker
        elo = ctx.elo
        asian = ctx.asian_features
        oc = raw.get('odds_comparison', {})
        mov_dir = odds.get('movement_dir')
        mov_strength = odds.get('movement_strength')
        has_odds = odds.get('has_odds_data', False)
        hp = odds.get('home_prob', 0)
        dp = odds.get('draw_prob', 0)
        ap = odds.get('away_prob', 0)
        margin_val = odds.get('margin', 0)
        data_source = odds.get('data_source', 'SPF')
        rq_hp = asian.get('rq_home_prob', 0)
        handicap_text = asian.get('handicap_text', '')
    else:
        d = ctx_or_dict
        raw = d.get('raw', {})
        rp = d.get('result_prediction', {})
        odds = d.get('odds_summary', {})
        bm = d.get('bookmaker_analysis', {})
        elo = d.get('elo', {})
        oc = d.get('odds_comparison', {}) or odds.get('odds_comparison', {})
        mov_dir = odds.get('movement_dir')
        mov_strength = odds.get('movement_strength')
        has_odds = bool(odds.get('home_prob', 0))
        hp = odds.get('home_prob', 0)
        dp = odds.get('draw_prob', 0)
        ap = odds.get('away_prob', 0)
        margin_val = odds.get('margin', 0) or raw.get('margin', 0)
        data_source = odds.get('data_source', 'SPF')
        rq_hp = 0
        handicap_text = odds.get('handicap', '')

    pred_val = rp.get('value', '')
    pred_conf = rp.get('confidence', 0)

    if not pred_val or pred_conf == 0:
        return {'passed': False, 'summary': '❌ 无预测', 'checks': {}, 'overall_score': 0}

    checks = {}
    passed_count = 0
    total_weight = 0

    # ── 因子1: 赔率信号强度 (权重25%) ──
    sorted_p = sorted([hp, dp, ap], reverse=True)
    pm = sorted_p[0] - sorted_p[1] if len(sorted_p) >= 2 else 0
    if pm >= 0.15:
        checks['odds_strength'] = {'status': '✅', 'weight': 25, 'score': 25, 'detail': f'概率差{pm:.0%},信号强'}
    elif pm >= 0.08:
        checks['odds_strength'] = {'status': '⚠️', 'weight': 25, 'score': 15, 'detail': f'概率差{pm:.0%},信号中等'}
    elif pm >= 0.03:
        checks['odds_strength'] = {'status': '⚡', 'weight': 25, 'score': 5, 'detail': f'概率差{pm:.0%},信号弱'}
    else:
        checks['odds_strength'] = {'status': '❌', 'weight': 25, 'score': 0, 'detail': f'概率差{pm:.0%},无信号'}
    passed_count += 1 if checks['odds_strength']['score'] >= 15 else 0
    total_weight += 25

    # ── 因子2: 数据源完整性 (权重15%) ──
    if data_source in ('SPF', 'RQ让球'):
        checks['data_source'] = {'status': '✅', 'weight': 15, 'score': 15, 'detail': f'来源:{data_source}'}
    else:
        checks['data_source'] = {'status': '⚠️', 'weight': 15, 'score': 5, 'detail': f'来源:{data_source},精度受限'}
    passed_count += 1 if checks['data_source']['score'] >= 15 else 0
    total_weight += 15

    # ── 因子3: 竞彩vs欧赔一致性 (权重15%) ──
    if oc and oc.get('direction_match') is not None:
        if oc['direction_match']:
            checks['source_consensus'] = {'status': '✅', 'weight': 15, 'score': 15, 'detail': '竞彩vs欧赔方向一致'}
        else:
            jc_dir = max(oc.get('jingcai_prob', {}), key=oc['jingcai_prob'].get) if oc.get('jingcai_prob') else '?'
            eu_dir = max(oc.get('euro_prob', {}), key=oc['euro_prob'].get) if oc.get('euro_prob') else '?'
            checks['source_consensus'] = {'status': '❌', 'weight': 15, 'score': 0, 'detail': f'方向分歧:竞彩→{jc_dir},欧赔→{eu_dir}'}
    else:
        checks['source_consensus'] = {'status': '⚠️', 'weight': 15, 'score': 8, 'detail': '无欧赔对比数据'}
    passed_count += 1 if checks['source_consensus']['score'] >= 15 else 0
    total_weight += 15

    # ── 因子4: 赔率变动确认 (权重10%) ──
    if mov_dir and mov_strength and mov_strength != 'STABLE':
        mov_confirms = odds.get('movement_confirms', False)
        if mov_confirms:
            checks['movement'] = {'status': '✅', 'weight': 10, 'score': 10, 'detail': f'变动{mov_strength},方向确认'}
        else:
            checks['movement'] = {'status': '⚠️', 'weight': 10, 'score': 3, 'detail': f'变动{mov_strength},方向矛盾'}
    else:
        checks['movement'] = {'status': '➖', 'weight': 10, 'score': 5, 'detail': '无显著变动'}
    passed_count += 1 if checks['movement']['score'] >= 10 else 0
    total_weight += 10

    # ── 因子5: Elo差距一致性 (权重15%) ──
    elo_diff = elo.get('diff', 0) if elo else 0
    if elo_diff != 0:
        elo_home_favored = elo_diff > 0
        pred_home = pred_val == 'home'
        if (elo_home_favored and pred_home) or (not elo_home_favored and not pred_home and pred_val != 'draw'):
            checks['elo'] = {'status': '✅', 'weight': 15, 'score': 15, 'detail': f'Elo差{elo_diff:.0f},与预测方向一致'}
        elif pred_val == 'draw' and abs(elo_diff) < 80:
            checks['elo'] = {'status': '✅', 'weight': 15, 'score': 12, 'detail': f'Elo差{elo_diff:.0f},平局合理'}
        else:
            checks['elo'] = {'status': '⚠️', 'weight': 15, 'score': 5, 'detail': f'Elo差{elo_diff:.0f},与预测方向矛盾'}
    else:
        checks['elo'] = {'status': '➖', 'weight': 15, 'score': 8, 'detail': '无Elo数据'}
    passed_count += 1 if checks['elo']['score'] >= 12 else 0
    total_weight += 15

    # ── 因子6: 庄家诱盘风险 (权重10%) ──
    trap = bm.get('trap_detected', False) if bm else False
    corrected = rp.get('corrected_by_trap', False)
    if trap and corrected:
        checks['trap'] = {'status': '⚠️', 'weight': 10, 'score': 5, 'detail': f'诱盘{bm.get("trap_direction","?")},已反买修正'}
    elif trap:
        checks['trap'] = {'status': '⚠️', 'weight': 10, 'score': 3, 'detail': f'诱盘信号,方向{bp.get("trap_direction","?")}'}
    else:
        checks['trap'] = {'status': '✅', 'weight': 10, 'score': 10, 'detail': '无诱盘信号'}
    passed_count += 1 if checks['trap']['score'] >= 10 else 0
    total_weight += 10

    # ── 因子7: 抽水率 (权重10%) ──
    if margin_val and margin_val < 0.08:
        checks['margin'] = {'status': '✅', 'weight': 10, 'score': 10, 'detail': f'抽水{margin_val:.1%},低'}
    elif margin_val and margin_val < 0.13:
        checks['margin'] = {'status': '✅', 'weight': 10, 'score': 8, 'detail': f'抽水{margin_val:.1%},正常'}
    elif margin_val and margin_val < 0.18:
        checks['margin'] = {'status': '⚠️', 'weight': 10, 'score': 4, 'detail': f'抽水{margin_val:.1%},偏高'}
    else:
        checks['margin'] = {'status': '➖', 'weight': 10, 'score': 5, 'detail': '无抽水数据'}
    passed_count += 1 if checks['margin']['score'] >= 8 else 0
    total_weight += 10

    # ── 综合评分 ──
    weighted_sum = sum(c['score'] for c in checks.values())
    overall_score = round(weighted_sum / max(total_weight, 1), 2)

    # 判定: 综合分>=0.6 且 赔率信号非❌ 且 至少4个因子通过
    passed = (overall_score >= 0.6 and
              checks.get('odds_strength', {}).get('score', 0) >= 5 and
              passed_count >= 4)

    # 审计摘要
    status_icon = '✅' if passed else ('⚠️' if overall_score >= 0.4 else '❌')
    summary = f'{status_icon} 综合{overall_score:.0%} 通过{passed_count}/7项'

    return {
        'passed': passed,
        'overall_score': overall_score,
        'passed_checks': passed_count,
        'total_checks': len(checks),
        'summary': summary,
        'checks': checks,
    }


def print_results(output: dict):
    """格式化打印预测结果 (含多层因子审计)"""
    preds = output['predictions']
    print(f"\n{'='*70}")
    print(f"  {output['model']}")
    
    # 审计统计
    audited = [p for p in preds if p.get('audit')]
    passed_audit = [p for p in audited if p['audit'].get('passed')]
    
    for p in preds:
        rp = p['result_prediction']
        val_map = {'home': f"{p['home_team']}胜", 'away': f"{p['away_team']}胜", 'draw': '平局'}
        result_text = val_map.get(rp['value'], '?')
        
        os_ = p['odds_summary']
        odds_str = f"P主{os_.get('home_prob',0):.0%} 平{os_.get('draw_prob',0):.0%} 客{os_.get('away_prob',0):.0%}" if os_.get('home_prob') else '无赔率数据'
        
        # ── 审计标签 ──
        audit = p.get('audit', {})
        audit_tag = ''
        if audit and audit.get('passed'):
            audit_tag = f" ✅审计{audit.get('overall_score',0):.0%}"
        elif audit and not audit.get('passed'):
            audit_tag = f" ⚠️审计{audit.get('overall_score',0):.0%}"
        
        trap_str = ''
        bm = p.get('bookmaker_analysis', {})
        if bm.get('trap_detected') and rp.get('corrected_by_trap'):
            trap_str = f" ★诱{bm.get('trap_direction','?')}→反买{bm.get('anti_trap_pick','?')}"
        
        elo = p.get('elo', {})
        elo_str = f" Elo:{elo.get('home','?')}vs{elo.get('away','?')}" if elo else ''
        
        margin_str = f" 抽水:{os_.get('margin',0):.1%}" if os_.get('margin') else ''
        
        # 置信度阈值标记
        conf = rp.get('confidence', 0)
        if conf >= 0.65:
            level_tag = '🟢'
        elif conf >= 0.50:
            level_tag = '🟡'
        elif conf >= 0.35:
            level_tag = '🟠'
        else:
            level_tag = '⚪'
        
        print(f"  #{p['match_no']:3s} {p['home_team']:8s} vs {p['away_team']:8s}")
        print(f"      {level_tag} {result_text} (信{rp.get('confidence',0):.0%}){trap_str}{audit_tag}")
        print(f"      {odds_str}{margin_str}{elo_str}")
        
        # 赔率变动信号
        mov_dir = os_.get('movement_dir')
        mov_str = os_.get('movement_strength')
        mov_analysis = os_.get('movement_analysis', '')
        if mov_dir and mov_str and mov_str != 'STABLE':
            print(f"      📈 {mov_analysis[:60]}")
        
        # 多源对比
        oc = p.get('odds_comparison', {}) or p.get('odds_summary', {}).get('odds_comparison', {})
        if oc and oc.get('direction_match') is not None:
            if not oc['direction_match']:
                max_jc = max(oc.get('jingcai_prob',{}), key=oc['jingcai_prob'].get) if oc.get('jingcai_prob') else '?'
                max_eu = max(oc.get('euro_prob',{}), key=oc['euro_prob'].get) if oc.get('euro_prob') else '?'
                print(f"      ⚡ 方向分歧: 竞彩→{max_jc}, 欧赔→{max_eu}")
            eu_margin = oc.get('euro_margin', 0)
            if eu_margin:
                jc_margin = oc.get('jingcai_margin', 0)
                if abs(jc_margin - eu_margin) > 0.02:
                    print(f"      🌍 欧赔抽水:{eu_margin:.1%}(比竞彩低{jc_margin-eu_margin:.1%})")
        
        # 比分预测
        sp = p.get('score_prediction', {})
        if sp and sp.get('most_likely'):
            print(f"      比分: {sp['most_likely'].get('score','?')}")
        
        # ── 大小球 ──
        ou = p.get('ou_prediction', {})
        if ou:
            icon = '🔴' if ou.get('verdict') == '大球' else '🔵'
            print(f"      {icon} 大小球: {ou.get('verdict','?')} (大{ou.get('over',0):.0%}/小{ou.get('under',0):.0%})")
        
        # ── 半全场 ──
        htft = p.get('htft_prediction', {})
        if htft and htft.get('htft'):
            print(f"      ⏱ 半全场: {htft.get('ht_result','?')}(半)→{htft.get('ft_result','?')}(全)  [{htft.get('htft','?')}]  半场{htft.get('ht_score','?')}")
        
        # 诱盘信号
        if bm.get('trap_detected') and bm.get('signals'):
            for sig in bm['signals'][:1]:
                print(f"      信号: {sig.get('detail','')[:60]}")
        
        # ── 审计详情（仅显示未通过项） ──
        if audit and audit.get('checks'):
            failed_checks = [c for k, c in audit['checks'].items() if c['status'] in ('❌', '⚠️', '⚡')]
            if failed_checks:
                for c in failed_checks[:3]:
                    print(f"      {c['status']} {c['detail']}")



# ================================================================
# main
# ================================================================




# ================================================================
# 6. 复盘系统 (Review & Optimize)
# ================================================================

REVIEW_FILE = DATA_DIR / 'review-records.json'


def init_review_db():
    if not REVIEW_FILE.exists():
        with open(REVIEW_FILE, 'w', encoding='utf-8') as f:
            json.dump({
                'reviews': [],
                'optimization_history': [],
                'total_reviewed': 0,
                'accuracy': {
                    'result_accuracy': 0,
                    'result_correct': 0,
                    'result_wrong': 0,
                }
            }, f, ensure_ascii=False, indent=2)
    return REVIEW_FILE


def add_review(match_no, home_team, away_team, score, prediction_value, prediction_confidence, odds_summary):
    """添加一条复盘记录"""
    init_review_db()
    with open(REVIEW_FILE, 'r', encoding='utf-8') as f:
        db = json.load(f)
    
    # 解析比分判断实际赛果
    if '-' in score:
        parts = score.split('-')
        try:
            h, a = int(parts[0]), int(parts[1])
            if h > a:
                actual_result = 'home'
            elif a > h:
                actual_result = 'away'
            else:
                actual_result = 'draw'
        except:
            actual_result = None
    else:
        actual_result = None
    
    correct = (prediction_value == actual_result) if actual_result else None
    
    review = {
        'match_no': match_no,
        'home_team': home_team,
        'away_team': away_team,
        'score': score,
        'prediction': prediction_value,
        'confidence': prediction_confidence,
        'actual': actual_result,
        'correct': correct,
        'timestamp': datetime.now().isoformat(),
        'odds': odds_summary,
    }
    
    db['reviews'].append(review)
    db['total_reviewed'] += 1
    
    if correct is True:
        db['accuracy']['result_correct'] += 1
    elif correct is False:
        db['accuracy']['result_wrong'] += 1
    
    total = db['accuracy']['result_correct'] + db['accuracy']['result_wrong']
    if total > 0:
        db['accuracy']['result_accuracy'] = db['accuracy']['result_correct'] / total
    
    with open(REVIEW_FILE, 'w', encoding='utf-8') as f:
        json.dump(db, f, ensure_ascii=False, indent=2)
    
    return correct


# ================================================================
# Elo动态更新系统 ★v2新增
# ================================================================

def expected_score_elo(rating_a: float, rating_b: float) -> float:
    """Elo期望胜率"""
    return 1.0 / (1.0 + 10.0 ** ((rating_b - rating_a) / 400.0))


def update_elo_from_match(home_team: str, away_team: str, home_goals: int, away_goals: int,
                           k_factor: int = 32, save: bool = True) -> dict:
    """
    根据比赛结果更新两队Elo评分
    
    规则:
    - 标准K=32
    - 大比分胜利(净胜≥3): K×1.15
    - 冷门(Elodiff>100且弱队胜): K×1.5
    - 平局: K×0.5
    
    Returns: {home: 新Elo, away: 新Elo, home_change: 变化, away_change: 变化}
    """
    alias = {'阿尔及利': '阿尔及利亚', '乌兹别克': '乌兹别克斯坦', '沙特': '沙特阿拉伯'}
    h_name = alias.get(home_team, home_team)
    a_name = alias.get(away_team, away_team)
    
    # 读取当前Elo
    elo_file = DATA_DIR / 'elo-data.json'
    if elo_file.exists():
        with open(elo_file, 'r', encoding='utf-8') as f:
            elo_dict = json.load(f)
    else:
        elo_dict = {}
    
    # 从engine.py获取初始值
    try:
        from engine import INITIAL_ELO_BASE
    except:
        INITIAL_ELO_BASE = {}
    
    old_h = elo_dict.get(h_name, INITIAL_ELO_BASE.get(h_name, 1500))
    old_a = elo_dict.get(a_name, INITIAL_ELO_BASE.get(a_name, 1500))
    
    # 计算期望
    exp_h = expected_score_elo(old_h, old_a)
    exp_a = 1 - exp_h
    
    # 实际结果 (1=胜, 0.5=平, 0=负)
    if home_goals > away_goals:
        actual_h, actual_a = 1.0, 0.0
    elif home_goals < away_goals:
        actual_h, actual_a = 0.0, 1.0
    else:
        actual_h, actual_a = 0.5, 0.5
    
    # K值调整
    k = k_factor
    goal_diff = abs(home_goals - away_goals)
    if goal_diff >= 3:
        k *= 1.15  # 大比分
    
    elo_diff = old_h - old_a
    is_upset = (abs(elo_diff) > 100 and 
                ((home_goals < away_goals and elo_diff > 0) or 
                 (home_goals > away_goals and elo_diff < 0)))
    if is_upset:
        k *= 1.5  # 冷门
    
    if home_goals == away_goals:
        k *= 0.5  # 平局减半
    
    # 更新
    new_h = old_h + k * (actual_h - exp_h)
    new_a = old_a + k * (actual_a - exp_a)
    
    # 保存
    elo_dict[h_name] = round(new_h, 1)
    elo_dict[a_name] = round(new_a, 1)
    
    # 也保存别名
    if home_team != h_name:
        elo_dict[home_team] = round(new_h, 1)
    if away_team != a_name:
        elo_dict[away_team] = round(new_a, 1)
    
    if save:
        with open(elo_file, 'w', encoding='utf-8') as f:
            json.dump(elo_dict, f, ensure_ascii=False, indent=2)
    
    return {
        'home_team': home_team, 'away_team': away_team,
        'home_goals': home_goals, 'away_goals': away_goals,
        'old_home_elo': old_h, 'new_home_elo': round(new_h, 1),
        'old_away_elo': old_a, 'new_away_elo': round(new_a, 1),
        'home_change': round(new_h - old_h, 1),
        'away_change': round(new_a - old_a, 1),
        'k_factor': round(k, 1),
        'expected_home_win': round(exp_h, 3),
        'is_upset': is_upset,
    }


def auto_update_elo_from_reviews():
    """从复盘记录自动更新Elo"""
    init_review_db()
    with open(REVIEW_FILE, 'r', encoding='utf-8') as f:
        db = json.load(f)
    
    # 找出有比分的复盘记录
    scored_reviews = []
    for r in db.get('reviews', []):
        score = r.get('score', '')
        if '-' in score:
            parts = score.split('-')
            try:
                h, a = int(parts[0]), int(parts[1])
                scored_reviews.append({
                    'home': r['home_team'],
                    'away': r['away_team'],
                    'home_goals': h,
                    'away_goals': a,
                })
            except:
                pass
    
    if not scored_reviews:
        print('⚠️ 复盘记录中没有比分数据, 无法更新Elo')
        return
    
    print(f'\n📊 基于 {len(scored_reviews)} 场复盘更新Elo...')
    changes = []
    for match in scored_reviews:
        result = update_elo_from_match(
            match['home'], match['away'],
            match['home_goals'], match['away_goals']
        )
        changes.append(result)
        change_str = f"{result['home_team']}: {result['old_home_elo']}→{result['new_home_elo']}({result['home_change']:+d}), "
        change_str += f"{result['away_team']}: {result['old_away_elo']}→{result['new_away_elo']}({result['away_change']:+d})"
        print(f'  {result["home_team"]} {result["home_goals"]}-{result["away_goals"]} {result["away_team"]}')
        print(f'    {change_str}')
    
    return changes


def auto_review_predictions(v4_file=None):
    """自动复盘最新预测vs实际结果
    从文件名 v4-predictions-{timestamp}.json 自动查找
    用户交互输入比分
    """
    if v4_file:
        pred_file = Path(v4_file)
    else:
        # 找最新的v4预测文件
        files = sorted(DATA_DIR.glob('v4-predictions-*.json'), reverse=True)
        if not files:
            print('❌ 未找到预测文件')
            return
        pred_file = files[0]
    
    with open(pred_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    preds = data['predictions']
    print(f"\n📋 复盘: {pred_file.name}")
    print(f'   共 {len(preds)} 场预测')
    print()
    
    init_review_db()
    with open(REVIEW_FILE, 'r', encoding='utf-8') as f:
        db = json.load(f)
    
    # 过滤已有复盘的
    existing = {(r['match_no'], r['home_team'], r['away_team']) for r in db['reviews']}
    
    new_reviews = 0
    for p in preds:
        key = (p['match_no'], p['home_team'], p['away_team'])
        if key in existing:
            continue
        
        rp = p['result_prediction']
        val_map = {'home': p['home_team']+'胜', 'away': p['away_team']+'胜', 'draw': '平局'}
        
        print(f"  {p['match_no']:3s} {p['home_team']:8s} vs {p['away_team']:8s}")
        print(f"     预测: {val_map.get(rp['value'],'?')} (信{rp.get('confidence',0):.0%})")
        print(f"     请输入比分 (如 2-1, 直接回车跳过): ", end='')
        
        try:
            score_input = input().strip()
        except:
            score_input = ''
        
        if not score_input:
            print(f"     已跳过")
            continue
        
        if '-' not in score_input:
            print(f"     格式错误, 跳过")
            continue
        
        correct = add_review(
            p['match_no'], p['home_team'], p['away_team'],
            score_input, rp['value'], rp.get('confidence', 0),
            p.get('odds_summary', {})
        )
        
        status = '✅ 正确!' if correct else ('❌ 错误' if correct is False else '⚠️ 未知')
        print(f"     {status}")
        new_reviews += 1
    
    if new_reviews == 0:
        print('   没有新的复盘记录')
    
    # 显示统计
    print(f"\n📊 复盘统计:")
    print(f"   总复盘: {db['total_reviewed']} 场")
    print(f"   准确率: {db['accuracy']['result_accuracy']:.1%}")
    print(f"   正确: {db['accuracy']['result_correct']} / 错误: {db['accuracy']['result_wrong']}")
    
    # 置信度校准检查
    if db['reviews']:
        print(f"\n📈 置信度校准:")
        for threshold in [0.7, 0.5]:
            bucket = [r for r in db['reviews'] if r.get('confidence', 0) >= threshold and r['correct'] is not None]
            if bucket:
                bucket_correct = sum(1 for r in bucket if r['correct'])
                print(f"   信>={threshold:.0%}: {bucket_correct}/{len(bucket)} = {bucket_correct/len(bucket):.0%}")


def show_optimization_status():
    """显示优化状态"""
    init_review_db()
    with open(REVIEW_FILE, 'r', encoding='utf-8') as f:
        db = json.load(f)
    
    print(f"\n📊 波神优化状态")
    print(f"{'='*50}")
    print(f"   总复盘: {db['total_reviewed']} 场")
    print(f"   赛果准确率: {db['accuracy']['result_accuracy']:.1%}")
    print(f"   正确: {db['accuracy']['result_correct']} / 错误: {db['accuracy']['result_wrong']}")
    
    if db['reviews']:
        # 最近复盘
        print(f"\n📋 最近复盘:")
        for r in db['reviews'][-5:]:
            status = '✅' if r.get('correct') else ('❌' if r.get('correct') is False else '?')
            val_map = {'home': '主胜', 'away': '客胜', 'draw': '平局'}
            print(f"   {status} {r['home_team']} {r['score']} {r['away']} (预测:{val_map.get(r['prediction'],'?')} 信{r.get('confidence',0):.0%})")
        
        # 置信度校准
        print(f"\n📈 置信度校准:")
        for threshold in [0.7, 0.6, 0.5, 0.4]:
            bucket = [r for r in db['reviews'] if r.get('confidence', 0) >= threshold and r['correct'] is not None and r.get('confidence',0) < threshold + 0.1]
            if bucket:
                correct = sum(1 for r in bucket if r['correct'])
                total_b = len(bucket)
                if total_b > 0:
                    print(f"   信{threshold:.0%}-{threshold+0.1:.0%}: {correct}/{total_b} = {correct/total_b:.0%}")
    
    if db.get('optimization_history'):
        print(f"\n🔄 优化历史:")
        for opt in db['optimization_history'][-3:]:
            print(f"   {opt.get('timestamp','')[:16]} | 调整前:{opt.get('before',0):.0%} → 后:{opt.get('after',0):.0%}")


def auto_optimize():
    """自动优化: 基于复盘数据调整算法参数"""
    init_review_db()
    with open(REVIEW_FILE, 'r', encoding='utf-8') as f:
        db = json.load(f)
    
    reviews = [r for r in db['reviews'] if r['correct'] is not None]
    if len(reviews) < 3:
        print(f'⚠️ 样本不足 (需≥3场, 当前{len(reviews)}场), 跳过优化')
        return
    
    print(f'\n🔄 开始自动优化 (基于{len(reviews)}场复盘)...')
    
    # 分析不同信号强度的准确率
    by_signal = {}
    for r in reviews:
        odds = r.get('odds', {})
        # 从odds判断信号强度
        margin = abs(odds.get('home_prob', 0) - 0.333)  # 简化版
        if margin > 0.15:
            signal = 'STRONG'
        elif margin > 0.05:
            signal = 'MEDIUM'
        else:
            signal = 'WEAK'
        
        if signal not in by_signal:
            by_signal[signal] = {'correct': 0, 'total': 0}
        by_signal[signal]['total'] += 1
        if r['correct']:
            by_signal[signal]['correct'] += 1
    
    print(f'\n   按信号强度:')
    for sig, stats in sorted(by_signal.items()):
        rate = stats['correct']/stats['total'] if stats['total'] > 0 else 0
        print(f'     {sig}: {stats["correct"]}/{stats["total"]} = {rate:.0%}')
    
    # 置信度校准检查
    print(f'\n   置信度校准:')
    for thr in [0.7, 0.5, 0.33]:
        subset = [r for r in reviews if r.get('confidence', 0) >= thr]
        if subset:
            c = sum(1 for r in subset if r['correct'])
            actual_rate = c / len(subset)
            avg_conf = sum(r.get('confidence', 0) for r in subset) / len(subset)
            print(f'     信≥{thr:.0%}: 实际准确率{actual_rate:.0%} (平均置信{avg_conf:.0%}, N={len(subset)})')
            # 校准建议
            if actual_rate < avg_conf - 0.1:
                print(f'       → ⚠️ 过度自信! 建议降低{(avg_conf - actual_rate):.0%}')
    
    # 记录优化
    before_acc = db['accuracy']['result_accuracy']
    
    opt_record = {
        'timestamp': datetime.now().isoformat(),
        'total_reviewed': len(reviews),
        'before': before_acc,
        'after': before_acc,  # 当前不变, 等下次复盘后才会变化
        'by_signal': by_signal,
        'recommendations': [],
    }
    db['optimization_history'].append(opt_record)
    
    with open(REVIEW_FILE, 'w', encoding='utf-8') as f:
        json.dump(db, f, ensure_ascii=False, indent=2)
    
    print(f'\n✅ 优化记录已保存')


def main():
    import argparse
    parser = argparse.ArgumentParser(description='波神数据管道')
    parser.add_argument('--fetch-only', action='store_true', help='仅采集')
    parser.add_argument('--predict', action='store_true', help='仅用最新数据预测')
    parser.add_argument('--date', type=str, default=None, help='指定日期 (如 2026-6-26)')
    parser.add_argument('--review', type=str, nargs='?', const='latest', help='复盘预测结果')
    parser.add_argument('--optimize', action='store_true', help='基于复盘数据自动优化')
    parser.add_argument('--status', action='store_true', help='查看优化状态')
    parser.add_argument('--update-elo', action='store_true', help='从复盘记录更新Elo评分')
    args = parser.parse_args()
    
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    
    if args.update_elo:
        auto_update_elo_from_reviews()
        return
    if args.status:
        show_optimization_status()
        return
    if args.optimize:
        auto_optimize()
        return
    if args.review:
        auto_review_predictions(args.review if args.review != 'latest' else None)
        return
    
    if args.predict:
        print('📂 使用已有数据进行预测...')
        latest_file = DATA_DIR / 'predictions-latest.json'
        if latest_file.exists():
            with open(latest_file, 'r', encoding='utf-8') as f:
                input_data = json.load(f)
            print(f'   加载 {len(input_data.get("predictions",[]))} 场比赛数据')
        else:
            print('   ❌ 未找到数据')
            return
    else:
        print('🌐 正在从 cp.nowscore.com 采集数据...')
        html = fetch_cp_nowscore(args.date)
        print(f'   页面大小: {len(html)} bytes')
        matches = parse_matches(html)
        print(f'   解析到 {len(matches)} 场比赛')
        # ── 日期过滤：只保留今天及之后的比赛 ──
        today_str = datetime.now().strftime('%Y-%m-%d')
        before = len(matches)
        matches = [m for m in matches if m.get('date', '') >= today_str and m.get('date', '')]
        after = len(matches)
        if before > after:
            print(f'   📅 过滤掉 {before-after} 场历史比赛(保留{after}场今日及之后)')
        if after == 0:
            print('   ❌ 今日无待赛比赛，尝试取明天的比赛...')
            # 如果今天没比赛，取最近一天(明天)的
            from datetime import timedelta
            tomorrow = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
            # 重新从所有原始解析中过滤
            all_matches = parse_matches(html)
            matches = [m for m in all_matches if m.get('date', '') >= today_str]
            if not matches:
                print('   ⚠️ 确实无近期比赛数据')
        # 保存赔率快照
        odds_data = {'fetch_time': datetime.now().isoformat(), 'date': args.date or datetime.now().strftime('%Y-%m-%d'), 'matches': matches}
        with open(DATA_DIR / 'odds-latest.json', 'w', encoding='utf-8') as f:
            json.dump(odds_data, f, ensure_ascii=False, indent=2)
        
        # ★★★ 赔率变动追踪 ★★★
        save_odds_snapshot(matches)
        movements = detect_odds_movements(matches)
        if movements:
            print(f'   📊 赔率变动: {len(movements)} 场检测到变化')
        
        # ★★★ v3: 多源赔率对比(欧赔) ★★★
        print('   🌍 获取欧洲赔率对比...')
        euro_odds_map = {}
        euro_count = 0
        for m in matches:
            if m.get('schedule_id'):
                euro = fetch_europe_odds(m['schedule_id'])
                if euro.get('euro'):
                    euro_odds_map[m['match_no'] or f"{m['home_team']}_{m['away_team']}"] = euro
                    euro_count += 1
                    # 对比竞彩vs欧赔
                    comparison = compare_odds_sources(m.get('jingcai', {}), euro.get('euro', {}))
                    m['odds_comparison'] = comparison
        print(f'      {euro_count} 场比赛获取成功')
        
        input_data = build_engine_input(matches)
        
        # 注入赔率变动信息
        if movements:
            apply_odds_movements(input_data, movements)
        
        with open(DATA_DIR / 'predictions-latest.json', 'w', encoding='utf-8') as f:
            json.dump(input_data, f, ensure_ascii=False, indent=2)
    
    print('\n🔮 运行预测引擎...')
    output = run_engine(input_data)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_file = DATA_DIR / f'v4-predictions-{timestamp}.json'
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    with open(DATA_DIR / 'v4-predictions-latest.json', 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print_results(output)
    print(f'\n✅ 完成! {output_file}')
    preds = output['predictions']
    no_odds = sum(1 for p in preds if p['odds_summary'].get('home_prob', 0) == 0)
    if no_odds: print(f'⚠️ {no_odds}/{len(preds)} 场无赔率数据')
    else: print(f'✅ 全部 {len(preds)} 场均有赔率数据!')
    
    # 审计统计
    audited = [p for p in preds if p.get('audit')]
    passed_audit = [p for p in audited if p['audit'].get('passed')]
    if audited:
        print(f'🔍 多层因子审计: {len(passed_audit)}/{len(audited)} 场通过审核')
        if passed_audit:
            print(f'   ✅ 通过: {"  ".join(f"#{p["match_no"]} {p["home_team"]}vs{p["away_team"]}" for p in passed_audit)}')
        failed = [p for p in audited if not p['audit'].get('passed')]
        if failed:
            print(f'   ❌ 未通过: {len(failed)} 场 (低信心预测已过滤)')
    
    # 高置信度汇总
    high_conf = [p for p in preds if p.get('result_prediction', {}).get('confidence', 0) >= 0.50 and p.get('audit', {}).get('passed')]
    if high_conf:
        print(f'🎯 推荐关注 ({len(high_conf)} 场通过审计+置信度>=50%):')
        for p in high_conf:
            rp = p['result_prediction']
            val_map = {'home': p['home_team'], 'away': p['away_team'], 'draw': '平局'}
            print(f'   #{p["match_no"]} {p["home_team"]} vs {p["away_team"]} → {val_map.get(rp["value"],"?")} (信{rp["confidence"]:.0%})')

if __name__ == '__main__':
    main()
