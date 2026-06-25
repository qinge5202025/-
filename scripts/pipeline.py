#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
⚽ 绿茵神算 · 全自动工作流 v3

一键执行: 采集 → 预测(动态权重+Elo) → 报告生成 → 复盘 → 优化(可选)

用法:
  python scripts/pipeline.py                    # 采集+预测+报告
  python scripts/pipeline.py --no-fetch         # 仅预测+报告（用已有数据）
  python scripts/pipeline.py --stage 16强       # 指定比赛阶段（影响权重分配）
  python scripts/pipeline.py --review           # 采集+预测+报告+复盘
  python scripts/pipeline.py --optimize         # 采集+预测+报告+复盘+优化
  python scripts/pipeline.py --all              # 全流程
"""

import sys
import subprocess
import argparse
from pathlib import Path
from datetime import datetime

SCRIPT_DIR = Path(__file__).parent.resolve()
PROJECT_DIR = SCRIPT_DIR.parent


def run_step(name, cmd):
    """运行一个步骤"""
    print(f"\n{'='*80}")
    print(f"  ⏳ [{name}]")
    print(f"{'='*80}")
    
    result = subprocess.run(
        cmd,
        cwd=str(PROJECT_DIR),
        capture_output=False,
        text=True,
    )
    
    if result.returncode != 0:
        print(f"❌ [{name}] 失败 (code={result.returncode})")
        return False
    
    print(f"✅ [{name}] 完成")
    return True


def main():
    parser = argparse.ArgumentParser(description='绿茵神算 全自动工作流 v3')
    parser.add_argument('--no-fetch', action='store_true', help='跳过数据采集，使用已有数据')
    parser.add_argument('--stage', default='小组赛',
                        choices=['小组赛', '16强', '8强', '半决赛', '决赛'],
                        help='比赛阶段（影响Elo/赔率权重分配）')
    parser.add_argument('--review', action='store_true', help='运行复盘报告')
    parser.add_argument('--optimize', action='store_true', help='运行算法自优化')
    parser.add_argument('--all', action='store_true', help='全流程（含复盘+优化）')
    args = parser.parse_args()
    
    print(f"\n{'='*80}")
    print(f"  ⚽ 绿茵神算 · 全自动工作流 v3")
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  阶段: {args.stage}  (动态权重将据此调整)")
    print(f"{'='*80}")
    
    # 步骤1: 采集（可选跳过）
    fetch_ok = True
    if not args.no_fetch:
        fetch_ok = run_step("采集赔率", ['python', 'scripts/fetch-odds.py'])
        run_step("采集积分榜", ['python', 'scripts/fetch-standings.py'])
    else:
        print("  📂 使用已有数据（跳过采集）")
    
    # 步骤2: 预测（带阶段和动态权重）
    if fetch_ok:
        pred_cmd = ['python', 'scripts/predict.py', '--stage', args.stage]
        run_step("智能预测(Elo+动态权重)", pred_cmd)
    
    # 步骤3: 生成Obsidian报告
    run_step("生成报告", ['python', 'scripts/generate-report.py', '--all'])
    
    # 步骤4: 复盘（可选）
    if args.review or args.all:
        run_step("复盘报告", ['python', 'scripts/review.py', '--report'])
    
    # 步骤5: 优化（可选）
    if args.optimize or args.all:
        run_step("算法优化", ['python', 'scripts/review.py', '--optimize'])
        run_step("优化状态", ['python', 'scripts/review.py', '--status'])
    
    # 显示输出文件
    print(f"\n{'='*80}")
    print(f"  ✅ 工作流完成！")
    print(f"  文件说明:")
    print(f"    data/odds-latest.json              → 最新赔率")
    print(f"    data/predictions-latest.json       → 预测数据（含Elo/动态权重）")
    print(f"    data/group-standings.json          → 积分榜数据")
    print(f"    *.md (根目录)                       → Obsidian报告")
    print(f"      2026世界杯-预测报告.md             → 完整预测")
    print(f"      2026世界杯-小组积分榜.md           → 积分榜+赛果")
    print(f"      2026世界杯-赔率分析.md            → 赔率深度分析")
    print(f"{'='*80}\n")


if __name__ == "__main__":
    main()
