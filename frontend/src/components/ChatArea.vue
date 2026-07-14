<template>
  <div class="chat-area">
    <!-- 消息列表 -->
    <div class="message-list" ref="messageListRef">
      <!-- 空状态 -->
      <div v-if="displayMessages.length === 0" class="empty-state">
        <div class="empty-icon"><img src="https://www-static.mashibing.com/_nuxt/img/logo.69f6ec8.svg" alt="logo" /></div>
        <h2>基于Harness Engineering的智能助手</h2>
        <p>我是你的 AI 助手，可以帮助你完成各种任务</p>
        <div class="feature-list">
          <div class="feature-item">
            <span class="feature-icon">📊</span>
            <span>数据分析和图表生成</span>
          </div>
          <div class="feature-item">
            <span class="feature-icon">🔍</span>
            <span>网络搜索和信息查询</span>
          </div>
          <div class="feature-item">
            <span class="feature-icon">💻</span>
            <span>代码编写和调试</span>
          </div>
          <div class="feature-item">
            <span class="feature-icon">📝</span>
            <span>文档处理和写作</span>
          </div>
        </div>
      </div>

      <!-- 消息列表（user / assistant / tool 按时间顺序混合展示）-->
      <div v-else class="messages">
        <MessageItem
          v-for="(message, index) in displayMessages"
          :key="message.id || index"
          :message="message"
          :is-streaming="isStreamingForMessage(message)"
        />
      </div>
    </div>
  </div>
</template>

<script setup>
import {computed, nextTick, ref, watch} from 'vue'
import MessageItem from './MessageItem.vue'

/**
 * 对话区域组件
 *
 * 显示消息列表（user / assistant / tool 按时间顺序混合展示）
 * 当 showToolCalls 为 false 时，过滤掉 tool 消息只显示 AI 回复
 */

const props = defineProps({
  messages: {
    type: Array,
    default: () => []
  },
  streaming: {
    type: Boolean,
    default: false
  },
  showToolCalls: {
    type: Boolean,
    default: true
  }
})

// 根据开关过滤展示的消息
const displayMessages = computed(() => {
  if (props.showToolCalls) {
    return props.messages
  }
  // 过滤掉 tool 消息，只保留 user 和 assistant
  return props.messages.filter(m => m.role !== 'tool')
})

// 消息列表引用
const messageListRef = ref(null)

/**
 * 判断当前消息是否处于流式输出状态
 * 只有最后一条 assistant 消息在 streaming 时才显示光标
 */
function isStreamingForMessage(msg) {
  if (!props.streaming || msg.role !== 'assistant') return false
  // 找到 messages 中最后一条 assistant 消息
  const lastAssistant = [...props.messages].reverse().find(m => m.role === 'assistant')
  return lastAssistant === msg
}

// 监听消息变化，自动滚动到底部
watch(
  () => props.messages.length,
  () => {
    nextTick(() => {
      scrollToBottom()
    })
  }
)

// 深度监听消息内容变化（流式更新时也会触发滚动）
watch(
  () => props.messages,
  () => {
    nextTick(() => {
      scrollToBottom()
    })
  },
  { deep: true }
)

/**
 * 滚动到底部
 */
function scrollToBottom() {
  if (messageListRef.value) {
    messageListRef.value.scrollTop = messageListRef.value.scrollHeight
  }
}
</script>

<style scoped>
.chat-area {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: #ffffff;
}

/* 消息列表 */
.message-list {
  flex: 1;
  overflow-y: auto;
  padding: 24px 0;
}

.message-list::-webkit-scrollbar {
  width: 6px;
}

.message-list::-webkit-scrollbar-track {
  background: transparent;
}

.message-list::-webkit-scrollbar-thumb {
  background: #cbd5e1;
  border-radius: 3px;
}

.message-list::-webkit-scrollbar-thumb:hover {
  background: #94a3b8;
}

/* 空状态 */
.empty-state {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 40px 24px;
  text-align: center;
  background: linear-gradient(180deg, #f8fafc 0%, #ffffff 100%);
}

.empty-icon {
  margin-bottom: 24px;
  animation: float 3s ease-in-out infinite;
}

.empty-icon img {
  width: 240px;
  height: 240px;
}

@keyframes float {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-8px); }
}

.empty-state h2 {
  font-size: 28px;
  font-weight: 700;
  background: linear-gradient(135deg, #0ea5e9 0%, #6366f1 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
  margin-bottom: 12px;
}

.empty-state p {
  font-size: 15px;
  color: #64748b;
  margin-bottom: 40px;
}

.feature-list {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 16px;
  max-width: 540px;
}

.feature-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 18px 20px;
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 14px;
  transition: all 0.2s ease;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
}

.feature-item:hover {
  transform: translateY(-2px);
  border-color: #0ea5e9;
  box-shadow: 0 4px 12px rgba(14, 165, 233, 0.1);
}

.feature-icon {
  font-size: 24px;
}

.feature-item span:last-child {
  font-size: 14px;
  color: #1e293b;
  font-weight: 500;
}

/* 消息列表 */
.messages {
  max-width: 1200px;
  margin: 0 auto;
}
</style>
