#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
📝 预测报告生成器 - 生成 Obsidian 可读的 Markdown 文件

用法:
  python scripts/generate-report.py              # 生成最新预测报告
  python scripts/generate-report.py --odds       # 同时生成赔率分析报告
  python scripts/generate-report.py --standings  # 同时生成积分榜报告
  python scripts/generate-report.py --all        # 生成全部报告
"""

import json
import sys
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
PROJECT_DIR = SCRIPT_DIR.parent
DATA_DIR = PROJECT_DIR / "data"
VAULT_DIR = Path("D:/公众号仓库")  # Obsidian 仓库根目录


# 确保 VAULT_DIR 存在
VAULT_DIR.mkdir(parents=True, exist_ok=True)


def load_json(path):
    if not path.exists():
        return None
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def build_confidence_bar(cv):
    if cv >= 0.7:
        return f"🟢 {cv:.0%}"
    elif cv >= 0.4:
        return f"🟡 {cv:.0%}"
    else:
        return f"🔴 {cv:.0%}"


def build_stars(cv, max_s=5):
    filled = int(cv * max_s)
    return '⭐' * filled + '☆' * (max_s - filled)


def gen_predictions_report():
    """生成预测报告 Markdown"""
    data = load_json(DATA_DIR / "predictions-latest.json")
    if not data:
        return "❌ 无预测数据，请先运行 python scripts/predict.py"
    
    preds = data.get('predictions', [])
    ts = data.get('predict_time', 'N/A')
    
    # 加载积分榜
    standings = {}
    sd = load_json(DATA_DIR / "group-standings.json")
    if sd:
        for g, teams in sd.get('standings', {}).items():
            for t in teams:
                standings[t['team_cn']] = {**t, 'group': g}
    
    total = len(preds)
    home_wins = sum(1 for p in preds if p['result_prediction'].get('value') == 'home')
    away_wins = sum(1 for p in preds if p['result_prediction'].get('value') == 'away')
    draws_n = sum(1 for p in preds if p['result_prediction'].get('value') == 'draw')
    
    lines = []
    lines.append('---')
    lines.append(f'created: {ts}')
    lines.append('tags: [worldcup2026, prediction, round3]')
    lines.append('---')
    lines.append('')
    lines.append('# 2026世界杯 第3轮 完整预测报告')
    lines.append('')
    lines.append(f'> **{ts}** ｜ 共 {total} 场 ｜ 主胜 {home_wins} 客胜 {away_wins} 平局 {draws_n}')
    lines.append('')
    lines.append('---')
    lines.append('')
    
    # ===== 高置信度专区 =====
    high_p = [p for p in preds if p['result_prediction']['confidence'] >= 0.7]
    mid_p = [p for p in preds if 0.4 <= p['result_prediction']['confidence'] < 0.7]
    low_p = [p for p in preds if p['result_prediction']['confidence'] < 0.4]
    
    if high_p:
        lines.append('## 精选推荐（置信度 >= 70%）')
        lines.append('')
        for p in high_p:
            _append_match(lines, p, standings)
    
    if mid_p:
        lines.append('## 参考预测（40% - 70%）')
        lines.append('')
        for p in mid_p:
            _append_match(lines, p, standings)
    
    if low_p:
        lines.append('## 观察列表（< 40%）')
        lines.append('')
        for p in low_p:
            _append_match(lines, p, standings)
    
    # ===== 总览统计 =====
    lines.append('## 总览统计')
    lines.append('')
    lines.append(f'| 指标 | 数值 |')
    lines.append(f'|---|---|')
    lines.append(f'| 总场次 | {total} |')
    lines.append(f'| 主胜 | {home_wins} |')
    lines.append(f'| 客胜 | {away_wins} |')
    lines.append(f'| 平局 | {draws_n} |')
    lines.append(f'| 高置信度(>=70%) | {len(high_p)} |')
    lines.append(f'| 中置信度(40-70%) | {len(mid_p)} |')
    lines.append(f'| 低置信度(<40%) | {len(low_p)} |')
    
    ouc = sum(1 for p in preds if p['over_under_prediction'].get('value') == 'over')
    udc = sum(1 for p in preds if p['over_under_prediction'].get('value') == 'under')
    lines.append(f'| 大球 | {ouc} |')
    lines.append(f'| 小球 | {udc} |')
    
    htft_counts = {}
    for p in preds:
        h = p['htft_prediction']['most_likely']
        htft_counts[h] = htft_counts.get(h, 0) + 1
    if htft_counts:
        for k, v in sorted(htft_counts.items(), key=lambda x: -x[1])[:5]:
            lines.append(f'| 半全场-{k} | {v} |')
    
    lines.append('')
    lines.append('---')
    lines.append('')
    
    # ===== 方法论 =====
    lines.append('## 预测方法论 v3 (Elo + 动态权重)')
    lines.append('')
    lines.append('基于 **7重多因子集成模型**，支持动态权重和Elo评分融合：')
    lines.append('')
    lines.append('| 因子 | 小组赛权重 | 淘汰赛权重 | 说明 |')
    lines.append('|---|---|---|---|')
    lines.append('| 欧赔隐含概率 | 35% | 15~30% | 赔率反推胜平负概率 + 凯利指数价值判断 |')
    lines.append('| 赔率变动方向 | 15% | 5~10% | 初盘→现盘变化，追踪庄家真实意图 |')
    lines.append('| 亚盘深度分析 | 20% | 15~20% | 盘口数值 + 水位变动 + 升/降盘信号 |')
    lines.append('| **战意因子** | **15%** | **5~10%** | **小组排名/积分/出线形势/背水一战/保平即出线** |')
    lines.append('| 大小球趋势 | 10% | 5~8% | 盘口大小 + 水位 + 变动方向 |')
    lines.append('| **Elo评分** | **5%** | **22~55%** | **48支球队Elo初始评分，客观强度对比** |')
    lines.append('| 小组赛基本面 | 5% | 5% | 排名/积分/净胜球/出线动机 |')
    lines.append('')
    lines.append('**动态权重规则**: 随比赛阶段自动调整，淘汰赛阶段Elo权重逐步上升至占主导 →55%')
    lines.append('')
    lines.append('### 赔率变动判定（三档信号）')
    lines.append('')
    lines.append('| 变动 | 幅度 | 信号 |')
    lines.append('|---|---|---|')
    lines.append('| 主胜降 | >0.05 | 🟢 资金追捧主队 → 倾向主胜 |')
    lines.append('| 客胜降 | >0.05 | 🟢 资金追捧客队 → 倾向客胜 |')
    lines.append('| 平赔升 | >0.10 | 🔴 排除平局 |')
    lines.append('| 平赔降 | >0.08 | 🔵 防平信号 |')
    lines.append('| 变动微 | <0.05 | ⚪ 噪音，不计入 |')
    lines.append('')
    lines.append('**今日实际变动示例**：阿根廷 1.58→1.36(-0.22,强烈主胜)、南非 3.30→4.80(+1.50,强烈客胜)、瑞士 2.09→2.26(+0.17)+加拿大 3.19→2.98(-0.21,客降+防平→平局)')
    lines.append('')
    lines.append('### 战意判定标准（第3轮专用）')
    lines.append('')
    lines.append('| 状态 | 条件 | 影响 |')
    lines.append('|---|---|---|')
    lines.append('| 已出线 | >=6分 | 可能轮换，预期进球x0.75 |')
    lines.append('| 保平出线 | 前2名且>=4分 | 保守战术，预期进球x0.80 |')
    lines.append('| 主动进取 | 前2名需赢球锁定 | 全力争胜，预期进球x1.15 |')
    lines.append('| 背水一战 | 第3名有积分 | 必须赢，预期进球x1.30 |')
    lines.append('| 绝境求生 | 第3/4名低分 | 必须赢+等结果 |')
    lines.append('| 荣誉之战 | 已出局 | 无压力，预期进球x1.05 |')
    lines.append('')
    lines.append('**半全场预测**: 基于赔率模式+历史统计的概率分布模型')
    lines.append('')
    lines.append('**Elo评分例**：捷克(1510) vs 墨西哥(1590) → 墨西哥期望胜率 61%')
    lines.append('')
    lines.append('> 免责声明：足球比赛不确定性大，本预测仅供参考娱乐。')
    lines.append('')
    
    return '\n'.join(lines)


def _append_match(lines, p, standings):
    """添加一场比赛的Markdown内容"""
    no = p['match_no']
    ht = p['home_team']
    at = p['away_team']
    rp = p['result_prediction']
    sp = p['score_prediction']
    hp = p['htft_prediction']
    op = p['over_under_prediction']
    ht_score = p['ht_prediction']['score']
    recs = p.get('recommendations', [])
    
    cv = rp['confidence']
    
    # 小组信息
    grp_info = ''
    hs = standings.get(ht, {})
    ha = standings.get(at, {})
    if hs and ha:
        grp_info = f'（{hs.get("group","?")}组 #{hs.get("rank","?")} {hs.get("pts","?")}分 vs {ha.get("group","?")}组 #{ha.get("rank","?")} {ha.get("pts","?")}分）'
    
    lines.append(f'### {no} {ht} vs {at} {grp_info}')
    lines.append('')
    lines.append(f'- **胜负**: {rp["prediction"]} {build_stars(cv)} {build_confidence_bar(cv)}')
    lines.append(f'- **比分**: {sp["most_likely"]}（半场 {ht_score}）')
    
    if sp['alternatives']:
        lines.append(f'- **备选比分**: {" / ".join(sp["alternatives"][:3])}')
    
    lines.append(f'- **半全场**: {hp["most_likely"]}（{hp["probability"]:.0%}）- {hp["description"]}（备选: {" / ".join(hp["alternatives"])}）')
    
    if op.get('value'):
        lines.append(f'- **大小球**: {op["prediction"]}（盘口 {op.get("line","?")}，可信 {op.get("confidence",0):.0%}）')
    
    lines.append('')
    
    # 战意分析
    ma = p.get('motivation_analysis', [])
    hm = p.get('home_motive', '')
    am = p.get('away_motive', '')
    gm = p.get('goal_multiplier', 1.0)
    if ma:
        motive_icon = '🔴' if '背水一战' in str(ma) else ('🟡' if '保平' in str(ma) else ('🟢' if '已出线' in str(ma) else '⚪'))
        lines.append(f'**{motive_icon} 战意分析**（进球乘数: {gm}）')
        for ma_line in ma:
            lines.append(f'- {ma_line}')
        lines.append('')
    
    # Elo评分
    elo = p.get('elo', {})
    if elo:
        elo_h = elo.get('home', 0)
        elo_a = elo.get('away', 0)
        elo_e = elo.get('expected', 0.5)
        elo_line = f'**📊 Elo评分**: {ht}({elo_h}) vs {at}({elo_a})'
        if elo_e > 0.6:
            elo_line += f' → {ht}胜率 {elo_e:.0%}（实力占优）'
        elif elo_e < 0.4:
            elo_line += f' → {at}胜率 {1-elo_e:.0%}（实力占优）'
        else:
            elo_line += ' → 实力接近'
        lines.append(elo_line)
        lines.append('')
    
    # 推荐
    if recs:
        lines.append('**推荐评级**')
        for r in recs[:3]:
            rs = build_stars(r.get('stars', 3) / 5, 5)
            note = f' - {r.get("note","")}' if r.get('note') else ''
            lines.append(f'- {rs} {r["type"]}: {r["pick"]}（可信度 {r["confidence"]}）{note}')
        lines.append('')
    
    # 因子分析
    fa_list = p.get('factor_analysis', [])
    if fa_list:
        lines.append('**因子分析**')
        for fa in fa_list[:3]:
            lines.append(f'- `{fa}`')
        lines.append('')
    
    # 小组形势
    if hs and ha:
        lines.append('**小组形势**')
        lines.append(f'- {ht}: {hs.get("group","?")}组 第{hs.get("rank","?")}名 {hs.get("pts","?")}分 ({hs.get("gf",0)}:{hs.get("ga",0)})')
        lines.append(f'- {at}: {ha.get("group","?")}组 第{ha.get("rank","?")}名 {ha.get("pts","?")}分 ({ha.get("gf",0)}:{ha.get("ga",0)})')
        lines.append('')
    
    lines.append('---')
    lines.append('')


def gen_standings_report():
    """生成积分榜 Markdown"""
    data = load_json(DATA_DIR / "group-standings.json")
    if not data:
        return "无积分数据，请先运行 python scripts/fetch-standings.py"
    
    standings = data.get('standings', {})
    matches = data.get('matches', {})
    updated = data.get('last_updated', 'N/A')
    
    lines = []
    lines.append('---')
    lines.append(f'created: {updated}')
    lines.append('tags: [worldcup2026, standings]')
    lines.append('---')
    lines.append('')
    lines.append('# 2026世界杯 小组积分榜')
    lines.append('')
    lines.append(f'> 更新于 {updated}')
    lines.append('')
    
    for letter in 'ABCDEFGHIJKL':
        s = standings.get(letter, [])
        if not s:
            continue
        
        # Count played matches
        mlist = matches.get(letter, [])
        played = sum(1 for m in mlist if m.get('full_time_score') and '-' in m.get('full_time_score', ''))
        total_m = len(mlist)
        
        lines.append(f'## {letter}组（已赛 {played}/{total_m} 场）')
        lines.append('')
        lines.append('| # | 球队 | 赛 | 胜 | 平 | 负 | 进 | 失 | 净 | 积分 | 形势 |')
        lines.append('|---|---|---|---|---|---|---|---|---|---|---|')
        for t in s:
            if t['rank'] <= 2:
                status = '✅ 晋级区'
            elif t['rank'] == 3:
                status = '⚖️ 争夺中'
            else:
                status = '❌ 危险'
            lines.append(f'| {t["rank"]} | **{t["team_cn"]}** | {t["mp"]} | {t["w"]} | {t["d"]} | {t["l"]} | {t["gf"]} | {t["ga"]} | {t["gd"]:+d} | **{t["pts"]}** | {status} |')
        lines.append('')
    
    # Matches section
    lines.append('## 完整赛果')
    lines.append('')
    for letter in 'ABCDEFGHIJKL':
        mlist = matches.get(letter, [])
        if not mlist:
            continue
        played = [m for m in mlist if m.get('full_time_score') and '-' in m.get('full_time_score', '')]
        if not played:
            continue
        lines.append(f'### {letter}组')
        lines.append('')
        lines.append('| 日期 | 主队 | 比分 | 客队 | 半场 | 轮次 |')
        lines.append('|---|---|---|---|---|---|')
        for m in played:
            d = m['date'][5:16] if m['date'] else 'TBD'
            s = m['full_time_score']
            h = m['half_time_score'] if m['half_time_score'] else '-'
            r = '第1轮' if m.get('round') == -1 else ('第2轮' if m.get('round') == 0 else '第3轮')
            lines.append(f'| {d} | {m["home_team_cn"]} | **{s}** | {m["away_team_cn"]} | {h} | {r} |')
        lines.append('')
    
    return '\n'.join(lines)


def save_report(content, filename):
    """保存 Markdown 报告到 Obsidian 根目录（同时在项目目录保留一份）"""
    for dest in [VAULT_DIR, PROJECT_DIR]:
        filepath = dest / filename
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
    print(f"已生成: {filepath}")


def main():
    flags = set(sys.argv[1:]) if len(sys.argv) > 1 else set()
    
    if not flags or '--all' in flags or '--predictions' in flags or not any(f.startswith('--') for f in flags):
        print("生成预测报告...")
        content = gen_predictions_report()
        save_report(content, '2026世界杯-第3轮预测报告.md')
    
    if '--standings' in flags or '--all' in flags:
        print("生成积分榜报告...")
        content = gen_standings_report()
        save_report(content, '2026世界杯-小组积分榜.md')
    
    if '--odds' in flags or '--all' in flags:
        print("生成赔率分析报告...")
        src = PROJECT_DIR / 'odds-analysis.md'
        dst = PROJECT_DIR / '2026世界杯-赔率分析.md'
        if src.exists():
            import shutil
            shutil.copy2(src, dst)
            print(f"已复制: {dst}")
    
    print("完成！可在 Obsidian 中查看" if flags else "完成！可在 Obsidian 中查看")


if __name__ == "__main__":
    main()
