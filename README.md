# 波神 · 2026世界杯七因子预测引擎 ⚽

> v3.0 — 动态Elo + 庄家动机诱盘检测 + 冷门预警

## 核心能力

| 功能 | 说明 |
|:---|:---|
| 📊 **赔率采集** | 从 cp.nowscore.com 采集竞彩赔率、亚盘、欧盘、大小球 |
| 🧠 **七因子预测** | 欧赔隐含概率 + 赔率变动 + 亚盘分析 + 大小球 + 战意 + 动态Elo + 庄家动机 |
| 🎯 **比分预测** | 泊松分布模型，输出最可能比分 + 备选 + 半场预测 |
| 🔄 **半全场预测** | 7种HT/FT组合概率分析 |
| 🕵️ **庄家动机检测** | 诱盘识别、阻盘识别、高抽水风险预警 |
| 📈 **动态Elo** | 赛后自动更新，冷门自适应调整 |
| 🔬 **赛后复盘** | 自动对比预测vs实际，统计准确率，权重优化 |

## 回测表现

| 版本 | 准确率 | 说明 |
|:---|:---:|:---|
| 旧模型 | 70% | 静态Elo+6因子 |
| v2优化 | 80% | 动态Elo+新权重+冷门检测 |
| **v3波神** | **80%** | +庄家动机因子，高置信准确率86% |

## 快速开始

```bash
# 1. 采集最新赔率
python scripts/fetch-odds.py --json

# 2. 运行预测
python scripts/predict.py --file data/odds-latest.json

# 3. 赛后复盘
python scripts/review.py
```

## 项目结构

```
波神/
├── SKILL.md        # pi skill 入口
├── TEAMS.md        # 48支队数据库
├── scripts/
│   ├── fetch-odds.py    # 赔率采集
│   ├── predict.py       # 七因子预测引擎 ★v3
│   ├── review.py        # 复盘+优化
│   └── pipeline.py      # 全自动工作流
└── data/
    ├── odds-latest.json      # 最新赔率
    ├── elo-data.json         # 动态Elo评分
    └── predictions-*.json    # 预测结果
```

## 数据源

- **赔率**: https://cp.nowscore.com/
- **赛程**: https://nowscore.com/
