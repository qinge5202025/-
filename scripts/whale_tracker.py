#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🐋 Polymarket 鲸鱼情绪追踪模块 v1

从 Polymarket API 获取大户持仓数据, 计算鲸鱼情绪指标

数据源:
  - Gamma API (https://gamma-api.polymarket.com) — 市场数据
  - Data API  (https://data-api.polymarket.com) — 持仓数据

输出:
  - data/polymarket-whale.json — 每支球队的鲸鱼情绪指标
"""

import json
import sys
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
PROJECT_DIR = SCRIPT_DIR.parent
DATA_DIR = PROJECT_DIR / "data"

GAMMA_API = "https://gamma-api.polymarket.com"
DATA_API = "https://data-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"
USER_AGENT = "Mozilla/5.0"

# ================================================================
# 球队英文名 → 中文名映射
# ================================================================

EN_TO_CN = {
    'Argentina': '阿根廷', 'Spain': '西班牙', 'France': '法国',
    'England': '英格兰', 'Brazil': '巴西', 'Germany': '德国',
    'Portugal': '葡萄牙', 'Netherlands': '荷兰', 'Uruguay': '乌拉圭',
    'Croatia': '克罗地亚', 'Morocco': '摩洛哥', 'Colombia': '哥伦比亚',
    'Japan': '日本', 'Norway': '挪威', 'USA': '美国',
    'Mexico': '墨西哥', 'Canada': '加拿大', 'Switzerland': '瑞士',
    'South Korea': '韩国', 'Belgium': '比利时', 'Senegal': '塞内加尔',
    'Ecuador': '厄瓜多尔', 'Egypt': '埃及', 'Australia': '澳大利亚',
    'Scotland': '苏格兰', 'Turkiye': '土耳其', 'Czechia': '捷克',
    'Bosnia-Herzegovina': '波黑', 'Qatar': '卡塔尔', 'Paraguay': '巴拉圭',
    'Ivory Coast': '科特迪瓦', 'Tunisia': '突尼斯', 'Iran': '伊朗',
    'New Zealand': '新西兰', 'Saudi Arabia': '沙特阿拉伯',
    'Algeria': '阿尔及利亚', 'Ghana': '加纳', 'Panama': '巴拿马',
    'Iraq': '伊拉克', 'Uzbekistan': '乌兹别克斯坦', 'Jordan': '约旦',
    'South Africa': '南非', 'Haiti': '海地', 'Cape Verde': '佛得角',
    'Congo DR': '刚果金', 'Austria': '奥地利', 'Sweden': '瑞典',
    'Curacao': '库拉索',
}

CN_TO_EN = {v: k for k, v in EN_TO_CN.items()}


def fetch(url: str) -> str:
    """HTTP GET请求"""
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read().decode('utf-8', errors='replace')
    except Exception as e:
        return f'{{"error": "{e}"}}'


def get_whale_metrics():
    """
    获取所有世界杯参赛队的鲸鱼情绪指标
    
    从Gamma API获取的市场数据中, 提取以下指标:
      - yes_price:       当前YES代币价格(=市场隐含夺冠概率)
      - volume:          总交易量
      - liquidity:       流动性深度
      - volume_24hr:     24小时交易量
      - price_change_1d: 24小时价格变动
    
    鲸鱼情绪计算:
      - whale_attention: 鲸鱼关注度 = volume × liquidity / 1e12
      - whale_momentum:  鲸鱼动量  = price_change_1d (正=鲸鱼买入)
      - whale_score:     综合得分  = attention × (1 + momentum×10)
    """
    # 获取赛事数据
    raw = fetch(f"{GAMMA_API}/events/30615")
    data = json.loads(raw)
    markets = data.get('markets', [])
    
    metrics = {}
    for m in markets:
        q = m.get('question', '')
        team_en = q.replace('Will ', '').replace(' win the 2026 FIFA World Cup?', '').strip()
        
        if any(x in team_en for x in ['Team ', 'Any Other', 'Peru', 'Italy']):
            continue
        
        team_cn = EN_TO_CN.get(team_en, team_en)
        
        prices = json.loads(m.get('outcomePrices', '[0,0]'))
        yes_price = float(prices[0]) if prices else 0
        volume = float(m.get('volume', 0))
        liquidity = float(m.get('liquidity', 0))
        vol_24hr = float(m.get('volume24hr', 0))
        price_change = float(m.get('oneDayPriceChange', 0))
        
        # 鲸鱼关注度: 成交量越大 + 流动性越深 = 鲸鱼越关注
        attention = (volume * liquidity) / 1e12
        
        # 鲸鱼动量: 价格24小时变动 (正=持续买入, 负=抛售)
        # 对于低概率队(<1%), 价格变动更有意义
        momentum = price_change
        if yes_price > 0.05:
            # 高概率队, 用百分比变动
            momentum = price_change / yes_price if yes_price > 0 else 0
        
        # 综合鲸鱼得分
        whale_score = attention * (1.0 + max(momentum, -0.5) * 10)
        
        # 市场深度: 流动性/成交量比值
        depth_ratio = liquidity / volume if volume > 0 else 0
        
        metrics[team_cn] = {
            'team_en': team_en,
            'yes_price': round(yes_price, 4),
            'volume': round(volume),
            'liquidity': round(liquidity),
            'volume_24hr': round(vol_24hr),
            'price_change_1d': price_change,
            'whale_attention': round(attention, 2),
            'whale_momentum': round(momentum, 4),
            'whale_score': round(whale_score, 2),
            'depth_ratio': round(depth_ratio, 6),
        }
    
    return metrics


def get_top_whales(limit=5):
    """获取排行榜TOP大户的地址"""
    raw = fetch(f"{DATA_API}/v1/leaderboard")
    data = json.loads(raw) if raw else []
    whales = []
    for i, w in enumerate(data[:limit]):
        whales.append({
            'rank': i + 1,
            'address': w.get('proxyWallet', ''),
            'username': w.get('userName', ''),
            'pnl': round(float(w.get('pnl', 0)), 0),
            'volume': round(float(w.get('vol', 0)), 0),
        })
    return whales


def main():
    print(f"\n{'='*60}")
    print(f"  Polymarket 鲸鱼情绪追踪")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")
    
    # 获取鲸鱼指标
    metrics = get_whale_metrics()
    
    # 获取TOP鲸鱼
    whales = get_top_whales()
    
    # 保存
    output = {
        'fetch_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'source': 'Polymarket API (Gamma + Data)',
        'event': 'World Cup Winner 2026',
        'top_whales': whales,
        'whale_metrics': metrics,
        'summary': {},
    }
    
    # 汇总: 各档球队的鲸鱼情绪
    sorted_by_score = sorted(metrics.items(), key=lambda x: -x[1]['whale_score'])
    
    print(f"\n  鲸鱼综合得分 TOP 10:")
    print(f"  {'队伍':12s} {'得分':>8s} {'价格':>8s} {'动量':>8s} {'成交量':>12s}")
    print(f"  {'-'*50}")
    for team, m in sorted_by_score[:15]:
        score = m['whale_score']
        price = f"{m['yes_price']*100:.1f}%"
        mom = f"{m['whale_momentum']*100:+.1f}%" if abs(m['whale_momentum']) > 0.001 else '0.0%'
        vol = f"{m['volume']:.0f}"
        print(f"  {team:12s} {score:>8.1f} {price:>8s} {mom:>8s} {vol:>12s}")
    
    print(f"\n  鲸鱼动量 (价格变动信号):")
    sorted_by_mom = sorted(metrics.items(), key=lambda x: -abs(x[1]['whale_momentum']))
    for team, m in sorted_by_mom[:10]:
        mom = m['whale_momentum']
        direction = '买入' if mom > 0 else '卖出'
        print(f"  {team:12s} {abs(mom)*100:.2f}% {direction} (现价{m['yes_price']*100:.1f}%)")
    
    print(f"\n  TOP 5 交易鲸鱼:")
    for w in whales:
        print(f"    #{w['rank']} {w['username'] or w['address'][:10]+'...':20s} PnL=${w['pnl']:.0f}")
    
    # 保存
    output_file = DATA_DIR / 'polymarket-whale.json'
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    
    print(f"\n  已保存: {output_file}")
    print(f"{'='*60}")


if __name__ == '__main__':
    main()
