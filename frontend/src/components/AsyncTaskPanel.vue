<template>
  <div v-if="tasks.length > 0" class="async-panel">
    <div class="async-header" @click="collapsed = !collapsed">
      <span class="async-title">
        后台异步任务
        <span class="async-count">{{ tasks.length }}</span>
        <span v-if="runningCount > 0" class="async-running">（{{ runningCount }} 个进行中）</span>
      </span>
      <span class="async-toggle">{{ collapsed ? '展开' : '收起' }}</span>
    </div>

    <div v-show="!collapsed" class="async-list">
      <div v-for="task in tasks" :key="task.task_id" class="async-item">
        <div class="async-item-main" @click="$emit('toggle', task.task_id)">
          <span class="status-dot" :class="statusClass(task)"></span>
          <span class="async-name">{{ task.agent_name || 'async-subagent' }}</span>
          <span class="async-desc" :title="task.description">{{ task.description || '异步分析任务' }}</span>
          <span class="async-status">{{ statusLabel(task) }}</span>
          <button class="async-dismiss" title="从列表移除" @click.stop="$emit('dismiss', task.task_id)">
            ×
          </button>
        </div>

        <div v-if="task.expanded && task.content" class="async-content">
          <MarkdownRenderer :content="task.content" />
        </div>
        <div v-if="task.expanded && task.error" class="async-error">{{ task.error }}</div>
      </div>
    </div>
  </div>
</template>

<script setup>
import {computed, ref} from 'vue'
import MarkdownRenderer from './MarkdownRenderer.vue'

/**
 * 异步子 Agent 后台任务面板
 *
 * 主 Agent 用 start_async_task 派出的任务会立即返回 task_id，父 Agent 那一轮就结束了；
 * 子 Agent 在 Agent Protocol 上后台跑，结果由父组件轮询 /api/async-tasks/{task_id} 拿回，
 * 这里只负责展示状态与结果摘要。
 */

const props = defineProps({
  // [{ task_id, agent_name, description, status, done, content, error, expanded }]
  tasks: {
    type: Array,
    default: () => []
  }
})

defineEmits(['toggle', 'dismiss'])

const collapsed = ref(false)

const runningCount = computed(
  () => props.tasks.filter(t => !t.done && t.status !== 'pending').length
)

const STATUS_LABELS = {
  pending: '排队中',
  running: '执行中',
  success: '已完成',
  error: '失败',
  interrupted: '等待人工',
  cancelled: '已取消',
  timeout: '超时'
}

function statusLabel(task) {
  return STATUS_LABELS[task.status] || task.status || '未知'
}

function statusClass(task) {
  if (task.error) return 'status-error'
  if (task.status === 'success') return 'status-success'
  if (task.status === 'error' || task.status === 'cancelled' || task.status === 'timeout') {
    return 'status-error'
  }
  if (task.status === 'interrupted') return 'status-waiting'
  return 'status-running'
}
</script>

<style scoped>
.async-panel {
  margin: 0 auto 12px;
  max-width: 1100px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  overflow: hidden;
}

.async-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
  cursor: pointer;
  user-select: none;
}

.async-title {
  font-size: 13px;
  font-weight: 600;
  color: #334155;
}

.async-count {
  display: inline-block;
  margin-left: 6px;
  padding: 1px 7px;
  background: #e0f2fe;
  color: #0369a1;
  border-radius: 10px;
  font-size: 12px;
}

.async-running {
  color: #0284c7;
  font-weight: 500;
}

.async-toggle {
  font-size: 12px;
  color: #64748b;
}

.async-list {
  border-top: 1px solid #e2e8f0;
}

.async-item {
  border-bottom: 1px solid #eef2f7;
}

.async-item:last-child {
  border-bottom: none;
}

.async-item-main {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 14px;
  cursor: pointer;
  font-size: 13px;
}

.async-item-main:hover {
  background: #f1f5f9;
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}

.status-running {
  background: #0ea5e9;
  animation: blink 1.4s ease-in-out infinite;
}

.status-success {
  background: #10b981;
}

.status-error {
  background: #ef4444;
}

.status-waiting {
  background: #f59e0b;
}

.async-name {
  color: #0f172a;
  font-weight: 600;
  flex-shrink: 0;
}

.async-desc {
  flex: 1;
  color: #64748b;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.async-status {
  color: #475569;
  flex-shrink: 0;
}

.async-dismiss {
  border: none;
  background: transparent;
  color: #94a3b8;
  font-size: 16px;
  line-height: 1;
  cursor: pointer;
  padding: 0 4px;
}

.async-dismiss:hover {
  color: #ef4444;
}

.async-content {
  padding: 4px 14px 12px 30px;
  font-size: 13px;
  color: #334155;
  background: #ffffff;
  border-top: 1px dashed #e2e8f0;
  max-height: 320px;
  overflow-y: auto;
}

.async-error {
  padding: 4px 14px 12px 30px;
  font-size: 13px;
  color: #b91c1c;
  background: #fef2f2;
}

@keyframes blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.35; }
}
</style>
