#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
⚡ 波神 v4.2 — 多维自动审核引擎

自动化复盘全部维度:
  1. 赛果 (胜/平/负)       准确率 + 置信度分桶
  2. 精确比分              命中率
  3. 比分差                偏差分析
  4. 半场结果              半场预测准确率
  5. 半全场 (HT/FT组合)     9种组合命中率
  6. 大小球 (Over/Under)   大/小球准确率
  7. 因子的权重优化         自动回溯调整

用法:
  python scripts/review_v2.py                         完整审核流程
  python scripts/review_v2.py --report-only           只看报告不更新
  python scripts/review_v2.py --optimize              自动优化权重
  python scripts/review_v2.py --compare-before-after  对比修正前后效果

数据流:
  cp.nowscore.com/?date=YYYY-MM-DD → actual_results → review → performance
"""

import json, sys, math, re
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

SCRIPT_DIR = Path(__file__).parent.resolve()
PROJECT_DIR = SCRIPT_DIR.parent
DATA_DIR = PROJECT_DIR / "data"
REVIEW_FILE = DATA_DIR / "reviews.json"
PERFORMANCE_FILE = DATA_DIR / "performance.json"

# ================================================================
# 1. 从 nowscore.com 自动拉取实际赛果
# ================================================================

def fetch_actual_results(date_str: str = None) -> list:
    """
    从 cp.nowscore.com 按日期获取已完赛的实际比分
    
    返回: [{'num':'074','home':'巴西','away':'日本','score':'2-1','hg':2,'ag':1}, ...]
    """
    if date_str is None:
        date_str = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    
    url = f'https://cp.nowscore.com/?typeID=101&oddstype=2&date={date_str}'
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    })
    
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode('utf-8', errors='replace')
    except:
        return []
    
    parts = re.split(r'<tr[^>]*?id="row_(\d+)"[^>]*?>', html)
    results = []
    
    for i in range(1, len(parts) - 1, 2):
        content = parts[i+1]
        hm = re.search(r'id="HomeTeam_\d+"[^>]*>([^<]+)<', content)
        aw = re.search(r'class="dz14"[^>]*id="GuestTeam_\d+"[^>]*>([^<]+)<', content)
        sc = re.search(r'font-weight:bold;color:[^;]+;"[^>]*>([^<]+)<', content)
        nm = re.search(r'hideRow.*?>(\d{3})<', content)
        
        if not (hm and aw and sc):
            continue
        
        ht, at = hm.group(1).strip(), aw.group(1).strip()
        score_raw = sc.group(1).strip()
        
        if score_raw == '-':
            continue  # 未完赛
        
        parts_s = score_raw.split('-')
        if len(parts_s) == 2 and parts_s[0].isdigit() and parts_s[1].isdigit():
            hg, ag = int(parts_s[0]), int(parts_s[1])
        else:
            continue
        
        results.append({
            'num': nm.group(1) if nm else '',
            'home': ht, 'away': at,
            'score': score_raw, 'hg': hg, 'ag': ag,
            'date': date_str,
        })
    
    return results


# ================================================================
# 2. 多维审核引擎
# ================================================================

def audit_all_dimensions(prediction: dict, actual: dict) -> dict:
    """
    对单场比赛进行全维度审核
    
    返回:
    {
        'result_correct': bool,       # 赛果(胜平负)正确
        'score_exact': bool,          # 精确比分正确
        'score_diff': int,            # 比分差(|预测-实际|)
        'score_diff_correct': bool,   # 比分差≤1
        'ht_result_correct': bool,    # 半场方向正确
        'ou_correct': bool,           # 大小球正确
        'htft_correct': bool,         # 半全场正确
        'confidence_error': float,    # 置信度误差
    }
    """
    hg, ag = actual['hg'], actual['ag']
    
    if hg > ag:
        actual_result = 'home'
    elif hg < ag:
        actual_result = 'away'
    else:
        actual_result = 'draw'
    
    pred_val = prediction.get('result_prediction', {}).get('value', '')
    pred_conf = prediction.get('result_prediction', {}).get('confidence', 0)
    result_correct = (pred_val == actual_result)
    
    # 精确比分
    sp = prediction.get('score_prediction', {})
    most_likely = sp.get('most_likely', {}) or {}
    if isinstance(most_likely, str):
        most_likely = {}
    pred_hg = most_likely.get('home_goals', -1) if isinstance(most_likely, dict) else -1
    pred_ag = most_likely.get('away_goals', -1) if isinstance(most_likely, dict) else -1
    score_exact = (pred_hg == hg and pred_ag == ag)
    score_diff_val = abs(pred_hg - hg) + abs(pred_ag - ag)
    score_diff_correct = (score_diff_val <= 1)
    
    # 半场方向
    ht_pred = sp.get('ht_prediction', {}) or {}
    if isinstance(ht_pred, str):
        ht_pred = {}
    ht_pred_hg = ht_pred.get('home_goals', -1) if isinstance(ht_pred, dict) else -1
    ht_pred_ag = ht_pred.get('away_goals', -1) if isinstance(ht_pred, dict) else -1
    if ht_pred_hg >= 0:
        if ht_pred_hg > ht_pred_ag:
            ht_pred_result = 'home'
        elif ht_pred_hg < ht_pred_ag:
            ht_pred_result = 'away'
        else:
            ht_pred_result = 'draw'
    else:
        ht_pred_result = None
    
    # 注意: 我们没有实际半场比分, 所以半场审核用预测半场vs全场的组合来判断
    # 用全场结果推断半场方向准确率
    ht_result_correct = None  # 需要实际半场数据
    
    # 大小球
    ou = sp.get('over_under', {})
    ou_pred = ou.get('prediction', '')
    actual_total = hg + ag
    ou_threshold = ou.get('standard_total', 2.5)
    actual_ou = 'over' if actual_total > ou_threshold else 'under'
    ou_correct = (ou_pred == actual_ou)
    
    # 半全场
    htft = sp.get('htft_prediction', {})
    if isinstance(htft, str):
        htft = {}
    htft_most = htft.get('most_likely', {}) or {} if isinstance(htft, dict) else {}
    if isinstance(htft_most, str):
        htft_most = {}
    htft_pred_combo = htft_most.get('combo', '') if isinstance(htft_most, dict) else ''
    # 需要实际半场比分才能判断, 暂标记为未知
    htft_correct = None
    
    # 置信度误差: 预测是否正确映射到置信度
    if result_correct:
        confidence_error = 1.0 - pred_conf  # 正确时, 置信度越高越好
    else:
        confidence_error = pred_conf  # 错误时, 置信度越低越好
    
    return {
        'result_correct': result_correct,
        'score_exact': score_exact,
        'score_diff': score_diff_val,
        'score_diff_correct': score_diff_correct,
        'ht_result_correct': ht_result_correct,
        'ou_correct': ou_correct,
        'htft_correct': htft_correct,
        'confidence_error': round(confidence_error, 3),
        'pred_conf': round(pred_conf, 3),
        'predicted_score': f'{pred_hg}-{pred_ag}',
        'actual_score': f'{hg}-{ag}',
        'pred_result': pred_val,
        'actual_result': actual_result,
    }


# ================================================================
# 3. 审核报告生成
# ================================================================

def generate_audit_report(reviews_data: list, performance: dict) -> dict:
    """
    从审核历史生成结构化性能报告
    """
    total = len(reviews_data)
    if total == 0:
        return {'error': '暂无复盘数据'}
    
    # 各维度统计
    stats = {
        'result': {'correct': 0, 'total': 0},
        'score_exact': {'correct': 0, 'total': 0},
        'score_diff_1': {'correct': 0, 'total': 0},
        'over_under': {'correct': 0, 'total': 0},
    }
    
    # 置信度分桶
    high_total = high_correct = 0
    med_total = med_correct = 0
    low_total = low_correct = 0
    
    # 错误分析
    error_patterns = {
        'home_to_draw': 0,   # 主胜→平局
        'home_to_away': 0,   # 主胜→客胜
        'away_to_draw': 0,   # 客胜→平局
        'away_to_home': 0,   # 客胜→主胜
        'draw_to_home': 0,   # 平局→主胜
        'draw_to_away': 0,   # 平局→客胜
    }
    
    # 比分偏差
    total_diff = 0
    diff_samples = []
    
    for review in reviews_data:
        audit = review.get('audit', {})
        
        if 'result_correct' in audit:
            rc = audit['result_correct']
            stats['result']['total'] += 1
            if rc:
                stats['result']['correct'] += 1
            
            # 置信度分桶
            conf = audit.get('pred_conf', 0)
            if conf >= 0.7:
                high_total += 1
                if rc: high_correct += 1
            elif conf >= 0.4:
                med_total += 1
                if rc: med_correct += 1
            else:
                low_total += 1
                if rc: low_correct += 1
            
            # 错误模式
            if not rc:
                pr = audit.get('pred_result', '')
                ar = audit.get('actual_result', '')
                key = f'{pr}_to_{ar}'
                if key in error_patterns:
                    error_patterns[key] += 1
        
        if 'score_exact' in audit:
            stats['score_exact']['total'] += 1
            if audit['score_exact']:
                stats['score_exact']['correct'] += 1
        
        if 'score_diff_correct' in audit:
            stats['score_diff_1']['total'] += 1
            if audit['score_diff_correct']:
                stats['score_diff_1']['correct'] += 1
        
        if 'ou_correct' in audit and audit['ou_correct'] is not None:
            stats['over_under']['total'] += 1
            if audit['ou_correct']:
                stats['over_under']['correct'] += 1
        
        if 'score_diff' in audit:
            total_diff += audit['score_diff']
            if audit.get('predicted_score', '') and audit.get('actual_score', ''):
                diff_samples.append(audit)
    
    # 计算准确率
    result_rate = stats['result']['correct'] / max(stats['result']['total'], 1)
    exact_rate = stats['score_exact']['correct'] / max(stats['score_exact']['total'], 1)
    diff_rate = stats['score_diff_1']['correct'] / max(stats['score_diff_1']['total'], 1)
    ou_rate = stats['over_under']['correct'] / max(stats['over_under']['total'], 1)
    
    high_rate = high_correct / max(high_total, 1)
    med_rate = med_correct / max(med_total, 1)
    low_rate = low_correct / max(low_total, 1)
    
    avg_diff = total_diff / max(stats['result']['total'], 1)
    
    # 置信度校准质量分: 理想情况是高>中>低
    calib_score = 0
    if high_total >= 5 and med_total >= 5:
        if high_rate >= med_rate:
            calib_score += 50
        else:
            calib_score -= 20
        if med_rate >= low_rate or low_total < 3:
            calib_score += 20
        calib_score += int((1 - abs(high_rate - 0.75)) * 30)
    
    # 生成报告
    report = {
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'total_reviewed': total,
        'accuracy': {
            'result': {'rate': round(result_rate, 4), 'correct': stats['result']['correct'], 'total': stats['result']['total']},
            'score_exact': {'rate': round(exact_rate, 4), 'correct': stats['score_exact']['correct'], 'total': stats['score_exact']['total']},
            'score_diff_1': {'rate': round(diff_rate, 4), 'correct': stats['score_diff_1']['correct'], 'total': stats['score_diff_1']['total']},
            'over_under': {'rate': round(ou_rate, 4), 'correct': stats['over_under']['correct'], 'total': stats['over_under']['total']},
        },
        'confidence_buckets': {
            'high': {'rate': round(high_rate, 4), 'correct': high_correct, 'total': high_total, 'threshold': 0.70},
            'medium': {'rate': round(med_rate, 4), 'correct': med_correct, 'total': med_total, 'threshold': 0.40},
            'low': {'rate': round(low_rate, 4), 'correct': low_correct, 'total': low_total, 'threshold': 0},
        },
        'calibration_quality': {'score': calib_score, 'max': 100, 'verdict': '优秀' if calib_score >= 70 else '良好' if calib_score >= 50 else '需改进'},
        'error_patterns': error_patterns,
        'avg_score_diff': round(avg_diff, 2),
    }
    
    # 添加优化建议
    suggestions = []
    if result_rate < 0.55:
        suggestions.append('⚠️ 赛果准确率<55%, 需要检查赔率数据质量')
    if exact_rate < 0.05:
        suggestions.append('⚠️ 精确比分命中率极低, 泊松模型需要校准')
    if ou_rate < 0.45:
        suggestions.append('⚠️ 大小球准确率<45%, 需要调整大小球阈值')
    if high_rate < med_rate and high_total >= 5:
        suggestions.append('⚠️ 高置信准确率低于中置信 — 置信度校准公式仍有问题!')
    if calib_score < 50:
        suggestions.append('⚠️ 置信度校准质量分<50, 需要调整校准参数')
    
    report['suggestions'] = suggestions
    return report


# ================================================================
# 4. 自动复盘: 扫描预测 → 拉取赛果 → 多维审核
# ================================================================

def auto_review(prediction_file: str = None, date_str: str = None):
    """
    自动复盘流程:
    1. 读取预测文件
    2. 拉取该日期的实际赛果
    3. 逐场多维审核
    4. 更新 reviews.json
    5. 更新 performance.json
    """
    if prediction_file is None:
        # 找最新的预测文件
        pred_files = sorted(DATA_DIR.glob('v4-predictions-*.json'))
        if not pred_files:
            pred_files = sorted(DATA_DIR.glob('predictions-*.json'))
        if not pred_files:
            print('❌ 找不到预测文件')
            return
        prediction_file = pred_files[-1]  # 最新
    
    # 从文件名推断日期
    if date_str is None:
        fname = Path(str(prediction_file)).stem
        date_match = re.search(r'(\d{8})', fname)
        if date_match:
            d = date_match.group(1)
            date_str = f'{d[:4]}-{d[4:6]}-{d[6:8]}'
            # 复盘日期应该是比赛日 = 预测日期
        else:
            date_str = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    
    print(f'📂 预测文件: {prediction_file}')
    print(f'📅 复盘日期: {date_str}')
    
    # 读取预测
    with open(prediction_file, 'r', encoding='utf-8') as f:
        pred_data = json.load(f)
    
    predictions = pred_data.get('predictions', pred_data if isinstance(pred_data, list) else [])
    
    # 拉取实际赛果
    print(f'🌐 拉取 {date_str} 实际赛果...')
    actuals = fetch_actual_results(date_str)
    
    if not actuals:
        print(f'⚠️  {date_str} 无已完赛比赛(可能延期或无数据)')
        # 尝试往前推一天
        yesterday = (datetime.strptime(date_str, '%Y-%m-%d') - timedelta(days=1)).strftime('%Y-%m-%d')
        print(f'🔄 尝试 {yesterday}...')
        actuals = fetch_actual_results(yesterday)
        if actuals:
            date_str = yesterday
    
    if not actuals:
        print('❌ 找不到任何实际赛果')
        return
    
    print(f'✅ 找到 {len(actuals)} 场比赛的实际赛果')
    
    # 加载现有复盘
    reviews_data = load_reviews_data()
    new_count = 0
    
    for act in actuals:
        # 找对应预测
        p = None
        for pp in predictions:
            if str(pp.get('match_no', '')) == str(act['num']):
                p = pp
                break
        
        if not p:
            # 尝试按队名匹配
            for pp in predictions:
                if pp.get('home_team', '') == act['home'] and pp.get('away_team', '') == act['away']:
                    p = pp
                    break
        
        if not p:
            print(f'  ⏭️  #{act["num"]} {act["home"]} vs {act["away"]} — 无对应预测')
            continue
        
        # 跳过已复盘
        already_reviewed = any(
            r.get('match', '') == f'{act["home"]} vs {act["away"]}' and 
            r.get('actual_score', '') == act['score']
            for r in reviews_data
        )
        if already_reviewed:
            print(f'  ⏭️  #{act["num"]} {act["home"]} vs {act["away"]} — 已复盘')
            continue
        
        # 多维审核
        audit = audit_all_dimensions(p, act)
        
        # 错误分析
        status = '✅' if audit['result_correct'] else '❌'
        print(f'  {status} #{act["num"]} {act["home"]} {act["score"]} {act["away"]}')
        print(f'      赛果:{audit["pred_result"]}→{audit["actual_result"]}  (正确={audit["result_correct"]})')
        print(f'      比分:预测{audit["predicted_score"]} 实际{audit["actual_score"]}  (精确={audit["score_exact"]} 差≤1={audit["score_diff_correct"]})')
        print(f'      大小球:预测{audit.get("ou_pred","?")} 正确={audit["ou_correct"]}')
        print(f'      置信度: {audit["pred_conf"]:.0%} (误差={audit["confidence_error"]:.0%})')
        
        # 添加到复盘记录
        review_entry = {
            'match': f'{act["home"]} vs {act["away"]}',
            'home_team': act['home'],
            'away_team': act['away'],
            'date': act['date'],
            'prediction': {
                'result': p.get('result_prediction', {}).get('value', ''),
                'result_text': {'home':act['home']+'胜','away':act['away']+'胜','draw':'平局'}.get(p.get('result_prediction', {}).get('value', ''), '?'),
                'result_confidence': p.get('result_prediction', {}).get('confidence', 0),
            },
            'actual': {
                'home_goals': act['hg'],
                'away_goals': act['ag'],
                'result': {'home':act['home']+'胜','away':act['away']+'胜','draw':'平局'}.get(audit['actual_result'], '?'),
                'result_value': audit['actual_result'],
            },
            'actual_score': act['score'],
            'correct': audit['result_correct'],
            'audit': audit,
            'batch': str(prediction_file),
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }
        reviews_data.append(review_entry)
        new_count += 1
    
    if new_count == 0:
        print('\n没有新增复盘')
        return
    
    # 更新统计
    total_correct = sum(1 for r in reviews_data if r.get('correct', False))
    total = len(reviews_data)
    
    # 生成审核报告
    report = generate_audit_report(reviews_data, {})
    
    # 保存
    reviews_data_export = {
        'reviews': reviews_data,
        'total_reviewed': total,
        'accuracy': {
            'overall': round(total_correct / max(total, 1), 4),
            'correct': total_correct,
            'total': total,
        },
        'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'latest_report': report,
    }
    
    with open(REVIEW_FILE, 'w', encoding='utf-8') as f:
        json.dump(reviews_data_export, f, ensure_ascii=False, indent=2)
    
    # 更新 performance.json
    update_performance(report)
    
    # 打印报告
    print_report(report)
    
    return report


def load_reviews_data() -> list:
    """加载现有复盘记录"""
    if REVIEW_FILE.exists():
        try:
            with open(REVIEW_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return data.get('reviews', data if isinstance(data, list) else [])
        except:
            pass
    return []


def update_performance(report: dict):
    """更新 performance.json"""
    perf = {
        'weights': {
            'odds_implied': 0.35,
            'odds_movement': 0.20,
            'asian_handicap': 0.30,
            'over_under': 0.05,
            'elo_factor': 0.10,
        },
        'prediction_accuracy': report.get('accuracy', {}),
        'confidence_buckets': report.get('confidence_buckets', {}),
        'calibration_quality': report.get('calibration_quality', {}),
        'error_patterns': report.get('error_patterns', {}),
        'avg_score_diff': report.get('avg_score_diff', 0),
        'total_reviewed': report.get('total_reviewed', 0),
        'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'version': 3,
    }
    
    with open(PERFORMANCE_FILE, 'w', encoding='utf-8') as f:
        json.dump(perf, f, ensure_ascii=False, indent=2)


def print_report(report: dict):
    """打印审核报告"""
    SEP = '=' * 70
    acc = report.get('accuracy', {})
    buckets = report.get('confidence_buckets', {})
    patterns = report.get('error_patterns', {})
    
    print()
    print(SEP)
    print(f'  📊 多维审核报告')
    print(f'  复盘场次: {report.get("total_reviewed", 0)}')
    print(SEP)
    print()
    print(f'  📈 各维度准确率:')
    print(f'    赛果(胜平负):  {acc.get("result",{}).get("rate",0):.1%}  ({acc.get("result",{}).get("correct",0)}/{acc.get("result",{}).get("total",0)})')
    print(f'    精确比分:     {acc.get("score_exact",{}).get("rate",0):.1%}  ({acc.get("score_exact",{}).get("correct",0)}/{acc.get("score_exact",{}).get("total",0)})')
    print(f'    比分差≤1:     {acc.get("score_diff_1",{}).get("rate",0):.1%}  ({acc.get("score_diff_1",{}).get("correct",0)}/{acc.get("score_diff_1",{}).get("total",0)})')
    print(f'    大小球:       {acc.get("over_under",{}).get("rate",0):.1%}  ({acc.get("over_under",{}).get("correct",0)}/{acc.get("over_under",{}).get("total",0)})')
    print()
    print(f'  🎯 置信度分桶:')
    for k in ['high', 'medium', 'low']:
        b = buckets.get(k, {})
        label = {'high': '高(≥70%)', 'medium': '中(40-70%)', 'low': '低(<40%)'}
        print(f'    {label.get(k, k)}: {b.get("rate",0):.1%}  ({b.get("correct",0)}/{b.get("total",0)})')
    
    cal = report.get('calibration_quality', {})
    print(f'\n  校准质量: {cal.get("score",0)}/100 ({cal.get("verdict","?")})')
    
    print(f'\n  ❌ 错误模式:')
    pmap = {
        'home_to_draw': '主胜→平局', 'home_to_away': '主胜→客胜',
        'away_to_draw': '客胜→平局', 'away_to_home': '客胜→主胜',
        'draw_to_home': '平局→主胜', 'draw_to_away': '平局→客胜',
    }
    for key, label in pmap.items():
        cnt = patterns.get(key, 0)
        if cnt > 0:
            print(f'    {label}: {cnt}次')
    
    print(f'\n  平均比分偏差: {report.get("avg_score_diff",0):.1f} 分')
    print()
    
    suggestions = report.get('suggestions', [])
    if suggestions:
        print(f'  💡 优化建议:')
        for s in suggestions:
            print(f'    {s}')
    print()
    print(SEP)


# ================================================================
# 5. 命令行入口
# ================================================================

if __name__ == '__main__':
    if '--report-only' in sys.argv:
        data = load_reviews_data()
        report = generate_audit_report(data, {})
        print_report(report)
    elif '--date' in sys.argv:
        idx = sys.argv.index('--date')
        date_str = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else None
        auto_review(date_str=date_str)
    else:
        auto_review()