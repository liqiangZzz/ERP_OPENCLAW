<template>
  <div v-if="visible && toolCalls.length > 0" class="tool-call-panel">
    <!-- 面板头部 -->
    <div class="panel-header" @click="togglePanel">
      <div class="header-left">
        <span class="panel-icon">🔧</span>
        <span class="panel-title">工具调用详情</span>
        <span class="tool-count">({{ toolCalls.length }})</span>
      </div>
      <span class="expand-icon">
        {{ isExpanded ? '▼' : '▶' }}
      </span>
    </div>

    <!-- 工具列表 -->
    <div v-show="isExpanded" class="tool-list">
      <div
        v-for="(tool, index) in toolCalls"
        :key="tool.id || index"
        class="tool-item"
        :class="{ expanded: expandedTools.includes(tool.id || index) }"
      >
        <!-- 工具头部 -->
        <div class="tool-header" @click="toggleExpand(tool.id || index)">
          <span class="tool-icon">{{ getToolIcon(tool.name) }}</span>
          <span class="tool-name">{{ tool.name }}</span>
          <span v-if="tool.source && tool.source !== 'main'" class="tool-source">
            ({{ formatSource(tool.source) }})
          </span>
          <span class="expand-icon">
            {{ expandedTools.includes(tool.id || index) ? '▼' : '▶' }}
          </span>
        </div>

        <!-- 工具详情 -->
        <div v-if="expandedTools.includes(tool.id || index)" class="tool-details">
          <!-- 参数 -->
          <div v-if="tool.args" class="detail-section">
            <div class="detail-label">参数:</div>
            <pre class="detail-content">{{ formatArgs(tool.args) }}</pre>
          </div>

          <!-- 结果 -->
          <div v-if="tool.result" class="detail-section">
            <div class="detail-label">结果:</div>
            <pre class="detail-content result">{{ tool.result }}</pre>
          </div>

          <!-- 无数据提示 -->
          <div v-if="!tool.args && !tool.result" class="no-data">
            暂无详细信息
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'

/**
 * 工具调用面板组件
 *
 * 显示 AI 助手调用工具的详细信息
 */

const props = defineProps({
  toolCalls: {
    type: Array,
    default: () => []
  }
})

// 控制面板显示/隐藏
const visible = ref(true)
// 面板是否展开（控制整个工具列表的折叠）
const isExpanded = ref(true)
// 展开的工具列表
const expandedTools = ref([])

/**
 * 切换面板展开/收起状态
 */
function togglePanel() {
  isExpanded.value = !isExpanded.value
}

/**
 * 切换工具展开/收起状态
 */
function toggleExpand(toolId) {
  const index = expandedTools.value.indexOf(toolId)
  if (index === -1) {
    expandedTools.value.push(toolId)
  } else {
    expandedTools.value.splice(index, 1)
  }
}

/**
 * 获取工具图标
 */
function getToolIcon(name) {
  if (!name) return '🔧'

  const lowerName = name.toLowerCase()
  if (lowerName.includes('chart') || lowerName.includes('fenxi') || lowerName.includes('生成图表')) {
    return '📊'
  }
  if (lowerName.includes('search') || lowerName.includes('web') || lowerName.includes('搜索')) {
    return '🔍'
  }
  if (lowerName.includes('research') || lowerName.includes('研究')) {
    return '📚'
  }
  if (lowerName.includes('read') || lowerName.includes('read_file')) {
    return '📖'
  }
  if (lowerName.includes('write') || lowerName.includes('write_file')) {
    return '✏️'
  }
  if (lowerName.includes('execute') || lowerName.includes('run')) {
    return '▶️'
  }

  return '🔧'
}

/**
 * 格式化来源名称（中文）
 */
function formatSource(source) {
  const sourceMap = {
    'chart-agent': '图表代理',
    'researcher': '研究代理',
    'model-agent': '模型代理',
    'general': '通用代理',
    'main': '主助手'
  }
  return sourceMap[source] || source
}

/**
 * 格式化参数（JSON 格式化）
 */
function formatArgs(args) {
  if (!args) return '无'

  try {
    // 尝试解析为 JSON 并格式化
    const parsed = JSON.parse(args)
    return JSON.stringify(parsed, null, 2)
  } catch {
    // 如果不是 JSON，直接返回原始字符串
    return args
  }
}
</script>

<style scoped>
.tool-call-panel {
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 16px;
  margin: 16px 24px;
  overflow: hidden;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
}

/* 面板头部 */
.panel-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 14px 18px;
  background: #f8fafc;
  cursor: pointer;
  border-bottom: 1px solid #e2e8f0;
  transition: all 0.2s ease;
}

.panel-header:hover {
  background: #f1f5f9;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 8px;
}

.panel-icon {
  font-size: 16px;
}

.panel-title {
  font-weight: 600;
  font-size: 14px;
  color: #1e293b;
}

.tool-count {
  font-size: 13px;
  color: #94a3b8;
}

/* 面板头部的折叠图标 */
.panel-header .expand-icon {
  font-size: 14px;
  color: #64748b;
  transition: transform 0.2s ease;
}

/* 工具列表 */
.tool-list {
  max-height: 400px;
  overflow-y: auto;
}

.tool-list::-webkit-scrollbar {
  width: 4px;
}

.tool-list::-webkit-scrollbar-track {
  background: transparent;
}

.tool-list::-webkit-scrollbar-thumb {
  background: #cbd5e1;
  border-radius: 2px;
}

.tool-list::-webkit-scrollbar-thumb:hover {
  background: #94a3b8;
}

.tool-item {
  border-bottom: 1px solid #f1f5f9;
}

.tool-item:last-child {
  border-bottom: none;
}

/* 工具头部 */
.tool-header {
  display: flex;
  align-items: center;
  padding: 14px 18px;
  cursor: pointer;
  gap: 10px;
  transition: all 0.2s ease;
}

.tool-header:hover {
  background: #f8fafc;
}

.tool-icon {
  font-size: 16px;
}

.tool-name {
  font-weight: 600;
  font-size: 14px;
  color: #1e293b;
}

.tool-source {
  font-size: 12px;
  color: #94a3b8;
}

.expand-icon {
  margin-left: auto;
  font-size: 12px;
  color: #64748b;
}

/* 工具详情 */
.tool-details {
  padding: 0 18px 18px;
  background: #f8fafc;
}

.detail-section {
  margin-bottom: 12px;
}

.detail-section:last-child {
  margin-bottom: 0;
}

.detail-label {
  font-size: 12px;
  font-weight: 600;
  color: #64748b;
  margin-bottom: 6px;
  text-transform: uppercase;
  letter-spacing: 0.8px;
}

.detail-content {
  background: #ffffff;
  border: 1px solid #e2e8f0;
  padding: 12px 14px;
  border-radius: 10px;
  font-size: 13px;
  font-family: 'Monaco', 'Menlo', 'JetBrains Mono', monospace;
  overflow-x: auto;
  white-space: pre-wrap;
  word-break: break-all;
  margin: 0;
  line-height: 1.6;
  color: #1e293b;
}

.detail-content.result {
  border-left: 3px solid #0ea5e9;
}

.no-data {
  font-size: 13px;
  color: #94a3b8;
  font-style: italic;
}
</style>
