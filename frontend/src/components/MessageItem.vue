<template>
  <!-- ============================================================ -->
  <!-- 用户消息 - 右侧蓝色气泡 -->
  <!-- ============================================================ -->
  <div v-if="message.role === 'user'" class="message-item message-user">
    <div class="message-content-wrapper">
      <div class="message-content">
        <div class="content-text">{{ message.content }}</div>
      </div>
    </div>
    <div class="message-avatar">
      <div class="avatar user-avatar">👤</div>
    </div>
  </div>

  <!-- ============================================================ -->
  <!-- AI 助手消息 - 左侧，带头部标签栏 -->
  <!-- ============================================================ -->
  <div v-else-if="message.role === 'assistant'" class="message-item message-assistant" :class="{ streaming: isStreaming }">
    <div class="message-avatar">
      <div class="avatar assistant-avatar" :class="getAvatarClass(message.source)">
        {{ getRoleIcon(message.source) }}
      </div>
    </div>
    <div class="message-content-wrapper">
      <!-- 角色标签栏 -->
      <div class="msg-label-bar" :class="getLabelBarClass(message.source)">
        <span class="msg-role-badge">{{ getRoleLabel(message.source) }}</span>
        <span v-if="message.source && message.source !== 'main'" class="msg-source-badge">
          {{ getSourceName(message.source) }}
        </span>
      </div>
      <!-- 消息正文 -->
      <div class="message-content">
        <MarkdownRenderer :content="message.content" />
        <span v-if="isStreaming && message.content" class="typing-cursor">▋</span>
      </div>
    </div>
  </div>

  <!-- ============================================================ -->
  <!-- 工具调用消息 - 左侧，带 Tool 标签栏 -->
  <!-- ============================================================ -->
  <div v-else-if="message.role === 'tool'" class="message-item message-tool-inline">
    <div class="message-avatar">
      <div class="avatar tool-avatar">{{ getToolIcon(message.tool_name) }}</div>
    </div>
    <div class="message-content-wrapper">
      <!-- Tool 标签栏 -->
      <div class="msg-label-bar tool-label-bar">
        <span class="msg-role-badge tool-role-badge">Tool</span>
        <span class="msg-tool-name-badge">{{ formatToolName(message.tool_name) }}</span>
        <span v-if="message.source && message.source !== 'main'" class="msg-source-badge tool-source-badge">
          {{ getSourceName(message.source) }}
        </span>
        <span v-if="message.tool_status === 'calling' && !hasToolContent" class="tool-status-badge calling">执行中</span>
        <span v-else-if="hasToolContent" class="tool-status-badge done">完成</span>
      </div>

      <!-- 参数（可折叠） -->
      <div v-if="message.args" class="tool-inline-section">
        <div class="tool-section-header" @click="toggleSection('args')">
          <span class="section-arrow">{{ expandedSections.includes('args') ? '▼' : '▶' }}</span>
          <span class="section-label">参数</span>
        </div>
        <pre v-if="expandedSections.includes('args')" class="tool-inline-code">{{ formatArgs(message.args) }}</pre>
      </div>

      <!-- 结果文本（可折叠） -->
      <div v-if="message.text" class="tool-inline-section">
        <div class="tool-section-header" @click="toggleSection('result')">
          <span class="section-arrow">{{ expandedSections.includes('result') ? '▼' : '▶' }}</span>
          <span class="section-label">结果</span>
        </div>
        <div v-if="expandedSections.includes('result')" class="tool-inline-result">
          <MarkdownRenderer :content="cleanText(message.text)" />
        </div>
      </div>

      <!-- 结果图片 -->
      <div v-if="message.images && message.images.length > 0" class="tool-inline-images">
        <div
          v-for="(img, imgIdx) in message.images"
          :key="imgIdx"
          class="tool-image-wrapper"
        >
          <img
            :src="img"
            :alt="`图表 ${imgIdx + 1}`"
            class="tool-result-image"
            loading="lazy"
            style="max-width:100%;max-height:360px;width:auto;height:auto;object-fit:contain;border-radius:8px;cursor:pointer;"
            @click="previewImage(img)"
          />
        </div>
      </div>

      <!-- 等待中动画 -->
      <div v-if="message.tool_status === 'calling' && !hasToolContent" class="tool-waiting">
        <span class="waiting-dot"></span>
        <span class="waiting-dot"></span>
        <span class="waiting-dot"></span>
        <span class="waiting-text">执行中...</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import MarkdownRenderer from './MarkdownRenderer.vue'

const props = defineProps({
  message: { type: Object, required: true },
  isStreaming: { type: Boolean, default: false }
})

const expandedSections = ref(['result'])

const hasToolContent = computed(() => {
  return !!(props.message.text || (props.message.images && props.message.images.length > 0))
})

function toggleSection(name) {
  const idx = expandedSections.value.indexOf(name)
  if (idx === -1) { expandedSections.value.push(name) }
  else { expandedSections.value.splice(idx, 1) }
}

function previewImage(src) { window.open(src, '_blank') }

// ============================================================
// AI 消息标签
// ============================================================

/** AI 消息的角色标签文字 */
function getRoleLabel(source) {
  if (!source || source === 'main') return 'AI'
  return getSourceName(source)  // 如「图表代理」
}

/** AI 消息的头像 emoji */
function getRoleIcon(source) {
  if (!source || source === 'main') return '🤖'
  const name = (source || '').toLowerCase()
  if (name.includes('chart')) return '📊'
  if (name.includes('research')) return '📚'
  if (name.includes('model')) return '🧠'
  return '🤖'
}

/** AI 消息标签栏的 CSS class */
function getLabelBarClass(source) {
  if (!source || source === 'main') return 'label-bar-main'
  return 'label-bar-sub'
}

/** AI 消息头像的 CSS class */
function getAvatarClass(source) {
  if (!source || source === 'main') return 'avatar-main'
  return 'avatar-sub'
}

// ============================================================
// 工具相关
// ============================================================

function formatToolName(name) {
  if (!name) return '未知工具'
  const nameMap = {
    'task': '委派子代理 (task)',
    'generate_column_chart': '生成柱状图',
    'generate_bar_chart': '生成柱状图',
    'generate_line_chart': '生成折线图',
    'generate_pie_chart': '生成饼图',
    'generate_area_chart': '生成面积图',
    'generate_scatter_chart': '生成散点图',
    'web_search': '网络搜索',
    'read_file': '读取文件',
    'write_file': '写入文件',
    'execute_python': '执行 Python 代码',
  }
  return nameMap[name] || name
}

function getSourceName(source) {
  const sourceMap = {
    'main': '主助手',
    'chart-agent': '图表代理',
    'researcher': '研究代理',
    'model-agent': '模型代理',
    'general': '通用代理'
  }
  return sourceMap[source] || source
}

function getToolIcon(name) {
  if (!name) return '🔧'
  const lowerName = (name || '').toLowerCase()
  if (lowerName.includes('chart') || lowerName.includes('fenxi') || lowerName.includes('图表') || lowerName.includes('generate_')) return '📊'
  if (lowerName.includes('search') || lowerName.includes('web') || lowerName.includes('搜索')) return '🔍'
  if (lowerName.includes('research') || lowerName.includes('研究')) return '📚'
  if (lowerName.includes('read') || lowerName.includes('read_file')) return '📖'
  if (lowerName.includes('write') || lowerName.includes('write_file')) return '✏️'
  if (lowerName.includes('execute') || lowerName.includes('run')) return '▶️'
  if (lowerName.includes('task')) return '🤝'
  return '🔧'
}

function formatArgs(args) {
  if (!args) return ''
  try { return JSON.stringify(JSON.parse(args), null, 2) }
  catch { return args }
}

function cleanText(text) {
  if (!text) return ''
  let cleaned = text
  cleaned = cleaned.replace(/,?\s*'id':\s*'[^']*'/g, '')
  cleaned = cleaned.replace(/,?\s*"id":\s*"[^"]*"/g, '')
  return cleaned
}
</script>

<style scoped>
/* ============================================================ */
/* 基础布局 */
/* ============================================================ */
.message-item {
  display: flex;
  gap: 12px;
  padding: 12px 24px;
  max-width: 100%;
}

.message-item.streaming {
  background: linear-gradient(to right, transparent, rgba(14, 165, 233, 0.03), transparent);
}

/* ============================================================ */
/* 角色标签栏（AI 和 Tool 共用） */
/* ============================================================ */
.msg-label-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  padding: 6px 0;
  flex-wrap: wrap;
}

.msg-role-badge {
  display: inline-flex;
  align-items: center;
  padding: 3px 12px;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.3px;
  text-transform: uppercase;
}

/* 主 Agent AI 标签 */
.label-bar-main .msg-role-badge {
  background: #dbeafe;
  color: #1d4ed8;
}

/* 子 Agent AI 标签 */
.label-bar-sub .msg-role-badge {
  background: #ede9fe;
  color: #7c3aed;
}

/* Tool 标签 */
.tool-label-bar .tool-role-badge {
  background: #fef3c7;
  color: #b45309;
}

.msg-source-badge {
  display: inline-flex;
  align-items: center;
  padding: 3px 10px;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 600;
}

.label-bar-sub .msg-source-badge {
  background: #ddd6fe;
  color: #6d28d9;
}

.tool-source-badge {
  background: #fef9c3;
  color: #a16207;
}

.msg-tool-name-badge {
  display: inline-flex;
  align-items: center;
  padding: 3px 12px;
  border-radius: 6px;
  font-size: 13px;
  font-weight: 700;
  color: #92400e;
  background: #fef9c3;
  font-family: 'Monaco', 'Menlo', 'JetBrains Mono', monospace;
}

.tool-status-badge {
  margin-left: auto;
  font-size: 11px;
  padding: 3px 10px;
  border-radius: 10px;
  font-weight: 600;
}

.tool-status-badge.calling {
  background: #fef3c7;
  color: #b45309;
  animation: pulse 1.5s ease-in-out infinite;
}

.tool-status-badge.done {
  background: #d1fae5;
  color: #065f46;
}

/* ============================================================ */
/* 用户消息（右侧） */
/* ============================================================ */
.message-user {
  justify-content: flex-end;
}

.message-user .message-content-wrapper {
  max-width: 65%;
  text-align: right;
}

.message-user .message-content {
  background: linear-gradient(135deg, #0ea5e9 0%, #6366f1 100%);
  border-radius: 18px 18px 6px 18px;
  padding: 14px 18px;
  box-shadow: 0 2px 8px rgba(14, 165, 233, 0.2);
}

.message-user .content-text {
  color: #fff;
  font-size: 15px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
}

.message-user .avatar { background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%); }

/* ============================================================ */
/* AI 助手消息（左侧） */
/* ============================================================ */
.message-assistant { justify-content: flex-start; }

.message-assistant .message-content-wrapper {
  max-width: 80%;
  overflow: hidden;
}

.message-assistant .message-content {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 6px 14px 14px 14px;
  padding: 14px 18px;
  line-height: 1.7;
  overflow: hidden;
}

.avatar-main { background: linear-gradient(135deg, #0ea5e9 0%, #0284c7 100%); }
.avatar-sub { background: linear-gradient(135deg, #7c3aed 0%, #6d28d9 100%); }

/* ============================================================ */
/* 工具调用消息（左侧） */
/* ============================================================ */
.message-tool-inline { justify-content: flex-start; }

.message-tool-inline .message-content-wrapper {
  max-width: 85%;
  background: #fffbeb;
  border: 1px solid #fde68a;
  border-radius: 0 14px 14px 14px;
  padding: 14px 18px;
  overflow: hidden;
}

.message-tool-inline .avatar {
  background: linear-gradient(135deg, #f59e0b 0%, #d97706 100%);
  font-size: 22px !important;
}

/* 工具内容区域 */
.tool-inline-section { margin-top: 8px; }

.tool-section-header {
  display: flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
  padding: 6px 0;
  user-select: none;
  color: #92400e;
  font-size: 13px;
  font-weight: 600;
}

.section-arrow { font-size: 10px; width: 14px; text-align: center; }

.section-label {
  text-transform: uppercase;
  letter-spacing: 0.5px;
  font-size: 12px;
}

.tool-inline-code {
  background: #fffbeb;
  border: 1px solid #fde68a;
  padding: 10px 14px;
  border-radius: 8px;
  font-size: 13px;
  font-family: 'Monaco', 'Menlo', 'JetBrains Mono', monospace;
  overflow-x: auto;
  white-space: pre-wrap;
  word-break: break-all;
  margin: 4px 0 0 20px;
  color: #78350f;
  line-height: 1.6;
  max-height: 200px;
  overflow-y: auto;
}

.tool-inline-result {
  margin: 4px 0 0 20px;
  padding: 10px 14px;
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  font-size: 14px;
  color: #1e293b;
  line-height: 1.6;
  overflow: hidden;
}

/* 图片展示 */
.tool-inline-images {
  margin-top: 12px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  overflow: hidden;
}

.tool-image-wrapper {
  display: flex;
  justify-content: center;
  background: #ffffff;
  border-radius: 10px;
  padding: 8px;
  overflow: hidden;
}

.tool-result-image {
  max-width: 100% !important;
  max-height: 360px;
  width: auto;
  height: auto;
  border-radius: 8px;
  cursor: pointer;
  transition: transform 0.2s ease, box-shadow 0.2s ease;
  object-fit: contain;
  background: #f8fafc;
  display: block;
}

.tool-result-image:hover {
  transform: scale(1.02);
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.12);
}

/* 等待动画 */
.tool-waiting {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 10px 0;
  margin-top: 4px;
}

.waiting-dot {
  width: 8px;
  height: 8px;
  background: #f59e0b;
  border-radius: 50%;
  animation: bounce 1.4s infinite ease-in-out both;
}

.waiting-dot:nth-child(1) { animation-delay: -0.32s; }
.waiting-dot:nth-child(2) { animation-delay: -0.16s; }

.waiting-text { margin-left: 8px; font-size: 13px; color: #a16207; }

/* ============================================================ */
/* 通用样式 */
/* ============================================================ */
.message-avatar { flex-shrink: 0; }

.avatar {
  width: 38px;
  height: 38px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 19px;
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.08);
}

.typing-cursor {
  display: inline-block;
  animation: blink 1s infinite;
  color: #0ea5e9;
  margin-left: 3px;
  font-weight: bold;
}

/* ============================================================ */
/* 动画 */
/* ============================================================ */
@keyframes blink {
  0%, 50% { opacity: 1; }
  51%, 100% { opacity: 0; }
}

@keyframes bounce {
  0%, 80%, 100% { transform: scale(0.4); opacity: 0.4; }
  40% { transform: scale(1); opacity: 1; }
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}
</style>
