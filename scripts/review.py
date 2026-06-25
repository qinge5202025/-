#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
⚽ 绿茵神算 · 赛后复盘 & 算法优化引擎

功能:
  1. 输入实际赛果 → 与预测对比
  2. 多维度准确率统计
  3. 算法权重自优化（基于历史表现）
  4. 生成复盘报告

用法:
  python scripts/review.py                       # 交互式复盘（手动输入赛果）
  python scripts/review.py --auto                # 全自动复盘（扫描所有未复盘的比赛）
  python scripts/review.py --report              # 查看历史复盘报告
  python scripts/review.py --optimize            # 执行算法优化
  python scripts/review.py --status              # 查看优化状态

数据文件:
  - data/reviews.json        → 所有复盘记录
  - data/performance.json    → 算法性能指标 & 权重
  - data/predictions-*.json  → 历史预测
"""

import json
import sys
import os
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict
from copy import deepcopy

SCRIPT_DIR = Path(__file__).parent.resolve()
PROJECT_DIR = SCRIPT_DIR.parent
DATA_DIR = PROJECT_DIR / "data"

REVIEW_FILE = DATA_DIR / "reviews.json"
PERFORMANCE_FILE = DATA_DIR / "performance.json"


# ============ 复盘引擎 ============

def load_reviews():
    """加载历史复盘记录"""
    if REVIEW_FILE.exists():
        try:
            with open(REVIEW_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return {'reviews': [], 'total_reviewed': 0, 'accuracy': {}, 'last_updated': ''}


def save_reviews(data):
    """保存复盘记录"""
    REVIEW_FILE.parent.mkdir(parents=True, exist_ok=True)
    data['last_updated'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with open(REVIEW_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_performance():
    """加载算法性能指标"""
    if PERFORMANCE_FILE.exists():
        try:
            with open(PERFORMANCE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return init_performance()


def init_performance():
    """初始化性能基准"""
    return {
        'weights': {
            'odds_implied': 0.40,
            'odds_movement': 0.25,
            'asian_handicap': 0.25,
            'over_under': 0.10,
        },
        'optimization_history': [],
        'factor_accuracy': {
            'odds_implied': {'correct': 0, 'total': 0, 'rate': 0},
            'odds_movement': {'correct': 0, 'total': 0, 'rate': 0},
            'asian_handicap': {'correct': 0, 'total': 0, 'rate': 0},
        },
        'prediction_accuracy': {
            'result': {'correct': 0, 'total': 0, 'rate': 0},
            'over_under': {'correct': 0, 'total': 0, 'rate': 0},
            'score_exact': {'correct': 0, 'total': 0, 'rate': 0},
            'score_diff': {'correct': 0, 'total': 0, 'rate': 0},
        },
        'confidence_buckets': {
            'high': {'correct': 0, 'total': 0, 'rate': 0, 'threshold': 0.70},
            'medium': {'correct': 0, 'total': 0, 'rate': 0, 'threshold': 0.40},
            'low': {'correct': 0, 'total': 0, 'rate': 0, 'threshold': 0},
        },
        'last_optimized': '',
        'version': 1,
    }


def save_performance(perf):
    """保存性能数据"""
    PERFORMANCE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(PERFORMANCE_FILE, 'w', encoding='utf-8') as f:
        json.dump(perf, f, ensure_ascii=False, indent=2)


def load_latest_predictions():
    """加载最新预测"""
    fp = DATA_DIR / "predictions-latest.json"
    if not fp.exists():
        print(f"❌ 未找到预测文件: {fp}")
        return None
    with open(fp, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_prediction_by_date(date_str):
    """加载指定日期的预测"""
    fp = DATA_DIR / f"predictions-{date_str}.json"
    if fp.exists():
        with open(fp, 'r', encoding='utf-8') as f:
            return json.load(f)
    return None


def get_match_result():
    """交互式输入赛果"""
    print("\n📝 请输入实际赛果（或输入 q 退出）")
    home_goals = input("  主队进球数: ").strip()
    if home_goals.lower() == 'q': return None
    away_goals = input("  客队进球数: ").strip()
    if away_goals.lower() == 'q': return None
    
    try:
        hg = int(home_goals)
        ag = int(away_goals)
        return hg, ag
    except ValueError:
        print("❌ 请输入数字")
        return get_match_result()


def compare_result(prediction, actual_home, actual_away):
    """将预测与实际对比"""
    home = prediction.get('home_team', '主队')
    away = prediction.get('away_team', '客队')
    
    # 实际赛果
    if actual_home > actual_away:
        actual_result = 'home'
        actual_text = f'{home}胜'
    elif actual_home == actual_away:
        actual_result = 'draw'
        actual_text = '平局'
    else:
        actual_result = 'away'
        actual_text = f'{away}胜'
    
    actual_total = actual_home + actual_away
    actual_ou = 'over' if actual_total >= 3 else 'under'  # 用3球为标准线
    
    # 预测数据
    rp = prediction.get('result_prediction', {})
    predicted_result = rp.get('value')
    predicted_confidence = rp.get('confidence', 0)
    
    op = prediction.get('over_under_prediction', {})
    predicted_ou = op.get('value')
    predicted_ou_confidence = op.get('confidence', 0)
    
    sp = prediction.get('score_prediction', {})
    predicted_score = sp.get('most_likely', '')
    
    # 对比
    result_correct = predicted_result == actual_result if predicted_result else None
    
    ou_correct = None
    if predicted_ou:
        ou_correct = predicted_ou == actual_ou
    
    # 比分接近度
    score_match = None
    predicted_numbers = [int(s) for s in predicted_score.split() if s.isdigit()]
    if len(predicted_numbers) >= 2:
        ph, pa = predicted_numbers[0], predicted_numbers[1]
        is_exact = (ph == actual_home and pa == actual_away)
        diff_diff = abs((ph - pa) - (actual_home - actual_away))
        score_match = {
            'exact': is_exact,
            'predicted_home': ph,
            'predicted_away': pa,
            'goal_diff_correct': diff_diff <= 1,
            'goal_diff_error': diff_diff,
        }
    
    return {
        'match': f"{home} vs {away}",
        'home_team': home,
        'away_team': away,
        'actual': {'home_goals': actual_home, 'away_goals': actual_away, 'result': actual_text, 'result_value': actual_result},
        'prediction': {
            'result': predicted_result,
            'result_text': rp.get('prediction', ''),
            'result_confidence': predicted_confidence,
            'over_under': predicted_ou,
            'over_under_text': op.get('prediction', ''),
            'ou_confidence': predicted_ou_confidence,
        },
        'comparison': {
            'result_correct': result_correct,
            'ou_correct': ou_correct,
            'score_detail': score_match,
        },
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }


def interactive_review():
    """交互式复盘流程"""
    pred_data = load_latest_predictions()
    if not pred_data:
        return
    
    predictions = pred_data.get('predictions', [])
    reviews = load_reviews()
    reviewed_matches = set()
    for r in reviews.get('reviews', []):
        reviewed_matches.add(f"{r.get('home_team','')}vs{r.get('away_team','')}")
    
    print(f"\n📋 共 {len(predictions)} 场预测，已复盘: {len(reviewed_matches)} 场\n")
    
    new_reviews = []
    for i, pred in enumerate(predictions):
        match_key = f"{pred.get('home_team','')}vs{pred.get('away_team','')}"
        if match_key in reviewed_matches:
            continue
        
        rp = pred.get('result_prediction', {})
        no = pred.get('match_no', '??')
        print(f"\n{'─'*80}")
        print(f"  [{i+1}/{len(predictions)}] {no} {pred['home_team']} vs {pred['away_team']}")
        print(f"  预测: {rp.get('prediction','?')} (信{rp.get('confidence',0):.0%})")
        print(f"  比分: {pred.get('score_prediction',{}).get('most_likely','?')}")
        
        result = get_match_result()
        if result is None:
            break
        
        hg, ag = result
        comparison = compare_result(pred, hg, ag)
        new_reviews.append(comparison)
        
        # 显示结果
        c = comparison['comparison']
        if c['result_correct'] is True:
            print(f"  ✅ 赛果预测正确！")
        elif c['result_correct'] is False:
            print(f"  ❌ 赛果预测错误 (实际: {comparison['actual']['result']})")
        else:
            print(f"  ⚪ 无明确赛果预测")
        
        if c.get('ou_correct') is True:
            print(f"  ✅ 大小球预测正确！")
        elif c.get('ou_correct') is False:
            print(f"  ❌ 大小球预测错误")
        
        sd = c.get('score_detail')
        if sd and sd.get('exact'):
            print(f"  🎯 比分精确命中！")
        elif sd:
            print(f"  📊 比分: 预测{sd['predicted_home']}-{sd['predicted_away']} vs 实际{hg}-{ag}")
    
    if new_reviews:
        reviews['reviews'].extend(new_reviews)
        reviews['total_reviewed'] = len(reviews['reviews'])
        recalc_accuracy(reviews)
        save_reviews(reviews)
        print(f"\n✅ 已保存 {len(new_reviews)} 场复盘记录")
        print_accuracy(reviews)
    else:
        print("\n📭 没有新的复盘数据")


def recalc_accuracy(reviews_data):
    """重新计算准确率"""
    all_reviews = reviews_data.get('reviews', [])
    if not all_reviews:
        return
    
    total = len(all_reviews)
    result_correct = sum(1 for r in all_reviews if r.get('comparison', {}).get('result_correct') is True)
    result_wrong = sum(1 for r in all_reviews if r.get('comparison', {}).get('result_correct') is False)
    result_na = sum(1 for r in all_reviews if r.get('comparison', {}).get('result_correct') is None)
    
    ou_correct = sum(1 for r in all_reviews if r.get('comparison', {}).get('ou_correct') is True)
    ou_total = sum(1 for r in all_reviews if r.get('comparison', {}).get('ou_correct') is not None)
    
    score_exact = sum(1 for r in all_reviews if r.get('comparison', {}).get('score_detail') and r['comparison']['score_detail'].get('exact'))
    
    # 置信度分桶
    conf_buckets = {'high': {'correct': 0, 'total': 0}, 'medium': {'correct': 0, 'total': 0}, 'low': {'correct': 0, 'total': 0}}
    for r in all_reviews:
        conf = r.get('prediction', {}).get('result_confidence', 0)
        correct = r.get('comparison', {}).get('result_correct')
        if conf >= 0.70:
            conf_buckets['high']['total'] += 1
            if correct: conf_buckets['high']['correct'] += 1
        elif conf >= 0.40:
            conf_buckets['medium']['total'] += 1
            if correct: conf_buckets['medium']['correct'] += 1
        else:
            conf_buckets['low']['total'] += 1
            if correct: conf_buckets['low']['correct'] += 1
    
    reviews_data['accuracy'] = {
        'total_reviewed': total,
        'result_accuracy': round(result_correct / max(total - result_na, 1), 4) if total > result_na else 0,
        'result_correct': result_correct,
        'result_wrong': result_wrong,
        'result_na': result_na,
        'ou_accuracy': round(ou_correct / max(ou_total, 1), 4) if ou_total > 0 else 0,
        'ou_correct': ou_correct,
        'ou_total': ou_total,
        'score_exact_matches': score_exact,
        'confidence_buckets': {
            'high': {'correct': conf_buckets['high']['correct'], 'total': conf_buckets['high']['total'],
                     'rate': round(conf_buckets['high']['correct'] / max(conf_buckets['high']['total'], 1), 4)},
            'medium': {'correct': conf_buckets['medium']['correct'], 'total': conf_buckets['medium']['total'],
                       'rate': round(conf_buckets['medium']['correct'] / max(conf_buckets['medium']['total'], 1), 4)},
            'low': {'correct': conf_buckets['low']['correct'], 'total': conf_buckets['low']['total'],
                    'rate': round(conf_buckets['low']['correct'] / max(conf_buckets['low']['total'], 1), 4)},
        }
    }


def print_accuracy(reviews_data):
    """打印准确率统计"""
    acc = reviews_data.get('accuracy', {})
    print(f"\n{'='*60}")
    print(f"  📊 累计复盘统计")
    print(f"{'='*60}")
    print(f"  复盘场次: {acc.get('total_reviewed', 0)}")
    print(f"  赛果准确率: {acc.get('result_accuracy', 0):.1%} ({acc.get('result_correct',0)}/{acc.get('total_reviewed',0)-acc.get('result_na',0)})")
    print(f"  大小球准确率: {acc.get('ou_accuracy', 0):.1%} ({acc.get('ou_correct',0)}/{acc.get('ou_total',0)})")
    print(f"  精确比分命中: {acc.get('score_exact_matches', 0)} 场")
    
    buckets = acc.get('confidence_buckets', {})
    if buckets:
        print(f"\n  置信度分桶准确率:")
        print(f"    高(>70%): {buckets.get('high',{}).get('rate',0):.1%} ({buckets.get('high',{}).get('correct',0)}/{buckets.get('high',{}).get('total',0)})")
        print(f"    中(40-70%): {buckets.get('medium',{}).get('rate',0):.1%} ({buckets.get('medium',{}).get('correct',0)}/{buckets.get('medium',{}).get('total',0)})")
        print(f"    低(<40%): {buckets.get('low',{}).get('rate',0):.1%} ({buckets.get('low',{}).get('correct',0)}/{buckets.get('low',{}).get('total',0)})")
    print(f"{'='*60}\n")


# ============ 算法优化引擎 ============

def factor_accuracy_from_reviews(reviews_data):
    """从复盘数据中反推各因子准确率（近似）"""
    reviews = reviews_data.get('reviews', [])
    if len(reviews) < 3:
        return None
    
    # 简单统计：当各因子方向一致时的正确率
    factor_stats = {
        'odds_implied': {'correct': 0, 'total': 0},
        'odds_movement': {'correct': 0, 'total': 0},
        'asian_handicap': {'correct': 0, 'total': 0},
    }
    
    for r in reviews:
        # 每个因子如果方向与最终预测一致，算投了赞成票
        # 这是一 种简化：我们无法精确知道每个因子独立的表现
        # 实际优化中可以有更复杂的贝叶斯方法
        predicted_result = r.get('prediction', {}).get('result')
        actual_result = r.get('actual', {}).get('result_value')
        comparison = r.get('comparison', {})
        result_correct = comparison.get('result_correct')
        
        # 这里只是估算各因子参与的程度
        if result_correct is True:
            for k in factor_stats:
                factor_stats[k]['correct'] += 1
                factor_stats[k]['total'] += 1
        elif result_correct is False:
            for k in factor_stats:
                factor_stats[k]['total'] += 1
    
    return {
        k: {
            'correct': v['correct'],
            'total': v['total'],
            'rate': round(v['correct'] / max(v['total'], 1), 4)
        }
        for k, v in factor_stats.items()
    }


def optimize_weights(reviews_data, perf):
    """基于历史表现优化权重"""
    reviews = reviews_data.get('reviews', [])
    if len(reviews) < 5:
        print("⚠️ 复盘数据不足5场，暂不优化（至少需要5场）")
        return perf
    
    # 统计各置信度分桶的准确率
    acc = reviews_data.get('accuracy', {})
    buckets = acc.get('confidence_buckets', {})
    
    high = buckets.get('high', {})
    medium = buckets.get('medium', {})
    low = buckets.get('low', {})
    
    result_accuracy = acc.get('result_accuracy', 0)
    
    # 计算当前权重的"有效度"
    total_reviewed = acc.get('total_reviewed', 0)
    total_correct = acc.get('result_correct', 0)
    baseline_rate = total_correct / max(total_reviewed, 1)
    
    print(f"\n🧪 当前算法表现: 基线准确率 {baseline_rate:.1%}")
    
    # ----- 权重优化逻辑 -----
    # 策略：增加表现好的因子权重，减少表现差的
    # 但由于我们缺乏每个因子的独立表现评估，我们用启发式调整
    
    old_weights = perf['weights']
    new_weights = deepcopy(old_weights)
    
    # 启发式1: 如果高置信度准确率 > 基线，说明算法偏向正确，提升隐含概率权重
    high_rate = high.get('rate', 0)
    if high_rate > baseline_rate + 0.1 and high.get('total', 0) >= 3:
        new_weights['odds_implied'] = min(old_weights['odds_implied'] * 1.05, 0.50)
        print(f"  📈 高置信准确({high_rate:.0%}) > 基线({baseline_rate:.0%})，提升赔率因子权重")
    
    # 启发式2: 如果中置信准确率低，降低变动因子权重
    medium_rate = medium.get('rate', 0)
    if medium_rate < baseline_rate - 0.1 and medium.get('total', 0) >= 3:
        new_weights['odds_movement'] = max(old_weights['odds_movement'] * 0.95, 0.15)
        print(f"  📉 中置信准确({medium_rate:.0%}) < 基线({baseline_rate:.0%})，降低变动因子权重")
    
    # 启发式3: 统计对亚盘数据的利用度
    asian_usage = sum(1 for r in reviews if r.get('comparison', {}).get('score_detail'))
    if asian_usage > total_reviewed * 0.5:
        new_weights['asian_handicap'] = min(old_weights['asian_handicap'] * 1.03, 0.35)
    
    # 归一化
    total = sum(new_weights.values())
    if total > 0:
        for k in new_weights:
            new_weights[k] = round(new_weights[k] / total, 4)
    
    # 记录优化
    optimization = {
        'date': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'old_weights': old_weights,
        'new_weights': new_weights,
        'baseline_accuracy': baseline_rate,
        'high_conf_accuracy': high_rate,
        'total_reviewed': total_reviewed,
        'reason': '自动优化',
    }
    perf['optimization_history'].append(optimization)
    
    # 更新因子准确率
    factor_acc = factor_accuracy_from_reviews(reviews_data)
    if factor_acc:
        perf['factor_accuracy'] = factor_acc
    
    # 更新整体准确率
    perf['prediction_accuracy']['result'] = {
        'correct': total_correct,
        'total': total_reviewed,
        'rate': baseline_rate,
    }
    
    # 更新置信度分桶
    perf['confidence_buckets']['high'] = {
        'correct': high.get('correct', 0),
        'total': high.get('total', 0),
        'rate': high.get('rate', 0),
        'threshold': 0.70,
    }
    perf['confidence_buckets']['medium'] = {
        'correct': medium.get('correct', 0),
        'total': medium.get('total', 0),
        'rate': medium.get('rate', 0),
        'threshold': 0.40,
    }
    perf['confidence_buckets']['low'] = {
        'correct': low.get('correct', 0),
        'total': low.get('total', 0),
        'rate': low.get('rate', 0),
        'threshold': 0,
    }
    
    # 写回权重
    perf['weights'] = new_weights
    perf['last_optimized'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    perf['version'] = perf.get('version', 0) + 1
    
    save_performance(perf)
    
    print(f"\n✅ 优化完成 (v{perf['version']})")
    print(f"  旧权重: {old_weights}")
    print(f"  新权重: {new_weights}")
    
    # 同步更新 predict.py 的权重
    update_predict_weights(new_weights)
    
    return perf


def update_predict_weights(weights):
    """将优化后的权重写回 predict.py"""
    predict_file = SCRIPT_DIR / "predict.py"
    if not predict_file.exists():
        print(f"⚠️ 未找到 {predict_file}，跳过权重更新")
        return
    
    with open(predict_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 替换 PREDICTION_WEIGHTS 字典
    weight_lines = []
    for k, v in weights.items():
        weight_lines.append(f"    '{k}': {v:.2f},")
    weight_str = '\n'.join(weight_lines)
    
    import re
    pattern = r"PREDICTION_WEIGHTS\s*=\s*\{(.*?)\}"
    replacement = f"PREDICTION_WEIGHTS = {{\n{weight_str}\n}}"
    
    if re.search(pattern, content, re.DOTALL):
        content = re.sub(pattern, replacement, content, flags=re.DOTALL)
        with open(predict_file, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"  ✅ 权重已同步到 predict.py")


def auto_review():
    """全自动复盘 - 用result_test数据模拟（演示用）"""
    print("🔄 全自动复盘模式")
    print("   提示: 使用 --input 指定实际赛果文件")
    print("   文件格式: JSON数组 [{'home_team':'阿根廷','away_team':'奥地利','home_goals':3,'away_goals':0}, ...]")
    
    if '--input' in sys.argv:
        idx = sys.argv.index('--input')
        if idx + 1 < len(sys.argv):
            results_file = Path(sys.argv[idx + 1])
            if results_file.exists():
                with open(results_file, 'r', encoding='utf-8') as f:
                    actuals = json.load(f)
                run_auto_review(actuals)
                return
    
    print("   用法示例: python scripts/review.py --auto --input data/actual-results.json")
    print("   交互模式: python scripts/review.py")


def run_auto_review(actuals_list):
    """执行自动复盘"""
    pred_data = load_latest_predictions()
    if not pred_data:
        return
    
    predictions = {f"{p.get('home_team','')}vs{p.get('away_team','')}": p for p in pred_data.get('predictions', [])}
    reviews = load_reviews()
    
    new_reviews = []
    for actual in actuals_list:
        key = f"{actual.get('home_team','')}vs{actual.get('away_team','')}"
        if key in predictions:
            comp = compare_result(predictions[key], actual['home_goals'], actual['away_goals'])
            new_reviews.append(comp)
            print(f"  {actual['home_team']} {actual['home_goals']}-{actual['away_goals']} {actual['away_team']} → {'✅' if comp['comparison']['result_correct'] else '❌'}")
    
    if new_reviews:
        reviews['reviews'].extend(new_reviews)
        reviews['total_reviewed'] = len(reviews['reviews'])
        recalc_accuracy(reviews)
        save_reviews(reviews)
        print(f"\n✅ 自动复盘完成: {len(new_reviews)} 场")
        print_accuracy(reviews)
    else:
        print("❌ 未找到匹配的预测")


def show_report():
    """显示复盘报告"""
    reviews = load_reviews()
    if not reviews.get('reviews'):
        print("📭 暂无复盘数据")
        return
    
    print(f"\n{'='*60}")
    print(f"  📊 绿茵神算 · 复盘报告")
    print(f"{'='*60}")
    print_accuracy(reviews)
    
    # 最近5场
    print(f"\n  最近5场复盘:")
    for r in reviews['reviews'][-5:]:
        c = r['comparison']
        icon = '✅' if c.get('result_correct') else '❌' if c.get('result_correct') is False else '⚪'
        print(f"    {icon} {r['match']}: 预测{r['prediction']['result_text']}→实际{r['actual']['result']}")


def show_status():
    """显示优化状态"""
    reviews = load_reviews()
    perf = load_performance()
    
    print(f"\n{'='*60}")
    print(f"  🧪 算法优化状态")
    print(f"{'='*60}")
    print(f"  版本: v{perf.get('version', 1)}")
    print(f"  最后优化: {perf.get('last_optimized', '未优化')}")
    print(f"  复盘场次: {reviews.get('total_reviewed', 0)}")
    
    acc = reviews.get('accuracy', {})
    print(f"  当前准确率: {acc.get('result_accuracy', 0):.1%}")
    
    print(f"\n  当前权重:")
    for k, v in perf.get('weights', {}).items():
        print(f"    {k}: {v:.2f}")
    
    print(f"\n  因子准确率:")
    for k, v in perf.get('factor_accuracy', {}).items():
        if v.get('total', 0) > 0:
            print(f"    {k}: {v.get('rate',0):.1%} ({v.get('correct',0)}/{v.get('total',0)})")
    
    print(f"\n  置信度分桶:")
    for k, v in perf.get('confidence_buckets', {}).items():
        if v.get('total', 0) > 0:
            print(f"    {k}(>{v.get('threshold',0):.0%}): {v.get('rate',0):.1%} ({v.get('correct',0)}/{v.get('total',0)})")
    
    print(f"\n  优化记录 ({len(perf.get('optimization_history', []))} 次):")
    for opt in perf.get('optimization_history', [])[-3:]:
        print(f"    {opt['date']}: {opt.get('reason','')} (准确率{opt.get('baseline_accuracy',0):.1%}→)")


def main():
    if '--report' in sys.argv:
        show_report()
    elif '--optimize' in sys.argv:
        reviews = load_reviews()
        perf = load_performance()
        optimize_weights(reviews, perf)
    elif '--status' in sys.argv:
        show_status()
    elif '--auto' in sys.argv:
        auto_review()
    else:
        interactive_review()


if __name__ == "__main__":
    main()
