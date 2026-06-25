#!/bin/bash
# ============================================================
# 绿茵神算 · 每日情报更新脚本
# 
# 用法: ./scripts/update-news.sh
# 功能: 通过 opencli 获取最新比赛结果/伤停新闻，更新 SKILL.md 的"六、最新情报"区
# 
# 前置条件: pi 的 opencli 工具可用
# ============================================================

SKILL_FILE="$(cd "$(dirname "$0")/.." && pwd)/SKILL.md"
TODAY=$(date +%Y-%m-%d)

echo "📰 绿茵神算 · 每日情报更新"
echo "=============================="
echo "📅 日期: $TODAY"
echo "📄 目标: $SKILL_FILE"
echo ""

# --- 步骤 1: 通过 opencli 获取最新比赛结果 ---
echo "🔍 正在获取最新赛果..."
LATEST_MATCHES=$(opencli bilibili search "2026世界杯 赛果" --limit 5 2>/dev/null || echo "")
if [ -z "$LATEST_MATCHES" ]; then
  echo "⚠️  无法获取最新赛果，跳过"
  LATEST_MATCHES="- 暂无新赛果"
fi

# --- 步骤 2: 通过 opencli 获取伤停新闻 ---
echo "🔍 正在获取伤停新闻..."
INJURY_NEWS=$(opencli bilibili search "2026世界杯 伤停" --limit 3 2>/dev/null || echo "")
if [ -z "$INJURY_NEWS" ]; then
  echo "⚠️  无法获取伤停新闻，跳过"
  INJURY_NEWS="- 暂无新的伤停报告"
fi

# --- 步骤 3: 生成新的情报区块 ---
echo "📝 正在生成情报更新..."

NEW_SECTION=$(cat <<EOF
## 六、最新情报（每日更新区）

> 本节由每日情报流程覆盖更新。**当本节与 TEAMS.md 冲突时，以本节为准**（本节更新）。

**情报日期：${TODAY}**

### 最新赛果
${LATEST_MATCHES}

### 伤停动态
${INJURY_NEWS}

### 明日赛程
- （待补充）
EOF
)

# --- 步骤 4: 更新 SKILL.md ---
# 找到 "## 六、最新情报" 到下一个 "## " 或文件结尾之间的内容，替换为新内容
if grep -q "## 六、最新情报" "$SKILL_FILE"; then
  # 使用 awk 替换从 "## 六、最新情报" 到下一个 "## " 之间的内容
  awk -v new="$NEW_SECTION" '
    /^## 六、最新情报/ { printing = 1; print new; next }
    /^## / && printing { printing = 0 }
    !printing { print }
  ' "$SKILL_FILE" > "${SKILL_FILE}.tmp" && mv "${SKILL_FILE}.tmp" "$SKILL_FILE"
  echo "✅ SKILL.md 已更新！"
else
  echo "⚠️  SKILL.md 中未找到「六、最新情报」章节，请检查文件格式"
  exit 1
fi

echo ""
echo "🎉 更新完成！重启 pi 后加载技能即可使用最新情报。"
