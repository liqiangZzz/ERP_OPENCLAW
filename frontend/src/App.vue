<template>
  <div class="app-container">
    <!-- 侧边栏 -->
    <Sidebar
      :sessions="sessions"
      :current-thread-id="currentThreadId"
      @select-session="handleSelectSession"
      @new-chat="handleNewChat"
      @delete-session="handleDeleteSession"
    />

    <!-- 主内容区 -->
    <main class="main-content">
      <!-- 对话区域 -->
      <ChatArea
        :messages="messages"
        :streaming="isStreaming"
        :show-tool-calls="showToolCalls"
      />

      <!-- ★ 人工介入中断横幅（数据补充 / HITL 审批） -->
      <InterruptBanner
        v-if="interruptData"
        :interrupt-data="interruptData"
        :submitting="isResuming"
        @resume="handleResume"
      />

      <!-- ★ 异步子 Agent 后台任务面板（派出去的任务在这里跟踪，跑完自动回到消息流） -->
      <AsyncTaskPanel
        :tasks="visibleTasks"
        @toggle="handleToggleTask"
        @dismiss="handleDismissTask"
      />

      <!-- 输入区域：中断激活时隐藏；回复生成期间保持可输入 -->
      <InputArea
        v-if="!interruptData"
        @send="handleSend"
        @stop="handleStop"
        @toggle-tool-calls="showToolCalls = $event"
        :streaming="isStreaming"
        :active-runs="activeRuns"
        :queued-runs="queuedRuns"
        :show-tool-calls="showToolCalls"
      />
    </main>
  </div>
</template>

<script setup>
import {computed, onBeforeUnmount, onMounted, ref, watch} from 'vue'
import Sidebar from './components/Sidebar.vue'
import ChatArea from './components/ChatArea.vue'
import InputArea from './components/InputArea.vue'
import InterruptBanner from './components/InterruptBanner.vue'
import AsyncTaskPanel from './components/AsyncTaskPanel.vue'
import {getChatState, resumeChat, streamChat} from './api/chat.js'
import {deleteSession, getMessages, getSessions} from './api/history.js'
import {extractAsyncTaskId, getAsyncTask} from './api/asyncTasks.js'

/**
 * DeepAgent 聊天应用主组件
 *
 * 消息流按时间顺序混合展示：
 *   [user] → [assistant 文本] → [tool 调用] → [assistant 文本] → ...
 * 支持 Human-in-the-Loop 中断：
 *   ... → [interrupt] → [用户决策/补充] → [继续]
 *
 * 并发模型（本轮改造的重点）：
 *   输入框在回复生成期间保持可用，允许连续提问。同一个 thread 的 run 由后端
 *   _stream_with_thread_lock 串行化（避免两个并发 run 争抢同一份 MongoDB
 *   checkpoint 导致历史分叉），因此前端必须做到「每条消息归属某个 run」，
 *   而不是往数组尾部无脑追加 —— 否则上一条回复的后续片段会插到新提问之后。
 *
 * 异步子 Agent：主 Agent 调 start_async_task 后立即结束当轮（框架语义就是
 * 「launch and stop」），子 Agent 在 Agent Protocol 上后台跑。由于 spec 带 url、
 * deepagents 会丢弃父 Agent 的 assistant_id，跨 deployment 的「完成自动唤醒」
 * 不生效，结果只能由这里轮询 /api/async-tasks/{task_id} 拿回来。
 */

// ============================================================
// 状态定义
// ============================================================

// 会话列表
const sessions = ref([])
// 当前会话 ID
const currentThreadId = ref(null)
// 消息列表（混合 user/assistant/tool，按时间顺序）
const messages = ref([])
// 是否显示工具调用
const showToolCalls = ref(true)
// 中断数据（null = 无中断，object = 有中断等待处理）
const interruptData = ref(null)
// 是否正在处理中断恢复
const isResuming = ref(false)

// 正在进行的 run：{ runId, threadId, controller, lastMessageId, queued }
const runs = ref([])
// 异步后台任务：{ task_id, agent_name, description, status, done, content, error, expanded, thread_id }
const asyncTasks = ref([])
// task_id → 轮询定时器
const pollers = new Map()

// 刷新前最后打开的会话（本浏览器范围内记忆，避免刷新后回到空白新对话）
const LAST_THREAD_KEY = 'erp_last_thread_id'

// 是否有回复正在生成（仅用于样式/提示，不再限制输入）
const isStreaming = computed(() => runs.value.length > 0)
// 正在流式输出的 run 数量
const activeRuns = computed(() => runs.value.filter(r => !r.queued).length)
// 在服务端排队的 run 数量
const queuedRuns = computed(() => runs.value.filter(r => r.queued).length)
// 面板只展示属于当前会话的异步任务
const visibleTasks = computed(() =>
  asyncTasks.value.filter(t => !t.thread_id || t.thread_id === currentThreadId.value)
)

// ============================================================
// 生命周期
// ============================================================

onMounted(async () => {
  await loadSessions()
  restoreLastThread()
})

/**
 * 刷新后回到上次打开的会话
 *
 * 会话本身一直有持久化（MongoDB checkpoint），但 currentThreadId 之前只存在于内存，
 * 刷新后回到 null，看起来就像「会话没了」。这里在本浏览器记住最后打开的会话。
 */
function restoreLastThread() {
  const last = localStorage.getItem(LAST_THREAD_KEY)
  if (!last) return

  currentThreadId.value = last
  loadMessages(last)
  loadAsyncTasks(last)
}

// currentThreadId 变化时持久化（新建对话置 null 时清除）
watch(currentThreadId, (threadId) => {
  if (threadId) {
    localStorage.setItem(LAST_THREAD_KEY, threadId)
  } else {
    localStorage.removeItem(LAST_THREAD_KEY)
  }
})

onBeforeUnmount(() => {
  for (const timer of pollers.values()) clearTimeout(timer)
  pollers.clear()
})

// ============================================================
// 会话管理
// ============================================================

/**
 * 加载会话列表
 */
async function loadSessions() {
  try {
    const response = await getSessions(1, 100)
    sessions.value = response.sessions || []
  } catch (error) {
    console.error('[App] 加载会话列表失败:', error)
    sessions.value = []
  }
}

/**
 * 选择会话
 */
async function handleSelectSession(threadId) {
  if (threadId === currentThreadId.value) return

  currentThreadId.value = threadId
  interruptData.value = null  // 切换会话时清除中断状态
  await loadMessages(threadId)
  await loadAsyncTasks(threadId)
}

/**
 * 新建对话
 */
function handleNewChat() {
  // 取消所有进行中的请求
  for (const run of runs.value) {
    run.controller?.abort()
  }
  runs.value = []
  isResuming.value = false
  interruptData.value = null
  currentThreadId.value = null
  messages.value = []
}

/**
 * 删除会话
 */
async function handleDeleteSession(threadId) {
  try {
    await deleteSession(threadId)
    await loadSessions()

    if (currentThreadId.value === threadId) {
      handleNewChat()
    }
  } catch (error) {
    console.error('[App] 删除会话失败:', error)
    alert('删除会话失败，请重试')
  }
}

/**
 * 加载会话消息
 */
async function loadMessages(threadId) {
  try {
    const response = await getMessages(threadId)
    messages.value = response.messages || []
  } catch (error) {
    console.error('[App] 加载会话消息失败:', error)
    messages.value = []
  }
}

// ============================================================
// run 归属：把每条消息插到「它所属 run 的末尾」
// ============================================================

/**
 * 生成消息 ID
 */
function newId(prefix) {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
}

/**
 * 在指定 run 的消息区尾部插入一条消息
 *
 * 后端虽然把同 thread 的 run 串行了，但用户可以在上一条回复结束前先提问，
 * 此时新提问的 user 气泡已经排在后面；若仍按「尾部追加」写入，上一条回复的
 * 后续片段就会落到新提问之后。故统一按 run 自己的 lastMessageId 定位插入点。
 */
function pushRunMessage(run, msg) {
  const tagged = { ...msg, run_id: run.runId }
  const idx = run.lastMessageId
    ? messages.value.findIndex(m => m.id === run.lastMessageId)
    : -1

  if (idx >= 0) {
    messages.value.splice(idx + 1, 0, tagged)
  } else {
    messages.value.push(tagged)
  }
  run.lastMessageId = tagged.id
  return tagged
}

/**
 * 找到（或新建）该 run 当前的 assistant 气泡
 */
function runAssistantBubble(run, source) {
  const current = messages.value.find(m => m.id === run.lastMessageId)
  if (current && current.role === 'assistant') return current
  return pushRunMessage(run, {
    id: newId('assistant'),
    role: 'assistant',
    content: '',
    source: source || 'main'
  })
}

/**
 * 把该 run 仍在 calling 的工具气泡标记为 done
 */
function finishRunTools(run) {
  for (const m of messages.value) {
    if (m.role === 'tool' && m.tool_status === 'calling' && m.run_id === run.runId) {
      m.tool_status = 'done'
    }
  }
}

/**
 * 清理该 run 产生的空 assistant 气泡
 */
function pruneRunEmptyAssistant(run) {
  messages.value = messages.value.filter(m =>
    !(m.role === 'assistant' && !m.content && m.run_id === run.runId)
  )
}

// ============================================================
// 统一回调构造（streamChat / resumeChat 共用）
// ============================================================

function buildRunCallbacks(run) {
  return {
    // 服务端排队（上一条还在生成）
    onQueued: () => {
      run.queued = true
    },

    onToken: (content, source) => {
      run.queued = false
      const bubble = runAssistantBubble(run, source)
      bubble.content += content
      bubble.source = source || bubble.source || 'main'
    },

    onToolStart: (tool) => {
      run.queued = false
      pushRunMessage(run, {
        id: tool.id || newId('tool'),
        role: 'tool',
        tool_name: tool.name,
        args: '',
        text: '',
        images: [],
        source: tool.source || 'main',
        tool_status: 'calling'
      })
    },

    onToolArgs: (args) => {
      run.queued = false
      // 把参数片段累加到该 run 最后一个仍在 calling 的工具气泡上
      for (let i = messages.value.length - 1; i >= 0; i--) {
        const m = messages.value[i]
        if (m.role === 'tool' && m.tool_status === 'calling' && m.run_id === run.runId) {
          m.args += args
          break
        }
      }
    },

    onToolResult: (tool) => {
      const target = messages.value.find(m => m.id === tool.id && m.role === 'tool')
      if (target) {
        target.text = tool.text || ''
        target.images = tool.images || []
        target.tool_status = 'done'
      }
      // ★ 捕获异步子 Agent 任务并开始轮询
      registerAsyncTask(run, tool)
    },

    onToolEnd: (tool) => {
      if (tool) {
        const target = messages.value.find(m => m.id === tool.id && m.role === 'tool')
        if (target && target.tool_status === 'calling') target.tool_status = 'done'
      }
      finishRunTools(run)
    },

    onInterrupt: (data) => {
      console.log('[App] 检测到中断:', data.interrupt_type)
      finishRunTools(run)
      pruneRunEmptyAssistant(run)
      interruptData.value = data
    },

    onDone: (data) => {
      finishRunTools(run)
      pruneRunEmptyAssistant(run)

      if (data.aborted) {
        const bubble = runAssistantBubble(run, 'main')
        if (!bubble.content) bubble.content = '（对话已停止）'
      }

      // 新会话：首轮结束后才拿到后端确认的 thread_id
      if (data.thread_id && !currentThreadId.value && !data.interrupted) {
        currentThreadId.value = data.thread_id
        loadSessions()
      }
      if (!data.interrupted) {
        interruptData.value = null
      }
    },

    onError: (error) => {
      console.error('[App] 对话错误:', error)
      const bubble = runAssistantBubble(run, 'main')
      bubble.content = bubble.content
        ? `${bubble.content}\n抱歉，发生了错误：${error.message}`
        : `抱歉，发生了错误：${error.message}`
    }
  }
}

/**
 * run 结束后的收尾：从活跃列表移除
 */
function retireRun(run) {
  runs.value = runs.value.filter(r => r.runId !== run.runId)
}

// ============================================================
// 消息发送
// ============================================================

/**
 * 发送消息
 *
 * 不再因「上一条还在生成」而丢弃输入：后端会按 thread 排队，
 * 前端为这条消息单独开一个 run，各自维护自己的消息区间。
 */
async function handleSend(message) {
  // 新会话必须在发送前就定下 thread_id，否则连续两条会各自生成不同 thread、
  // 既拿不到上下文也共享不了 async_tasks 状态
  if (!currentThreadId.value) {
    currentThreadId.value = crypto.randomUUID()
  }

  const run = {
    runId: newId('run'),
    threadId: currentThreadId.value,
    controller: new AbortController(),
    lastMessageId: null,
    queued: false
  }
  runs.value.push(run)

  pushRunMessage(run, {
    id: newId('user'),
    role: 'user',
    content: message
  })

  interruptData.value = null

  try {
    await streamChat(
      message,
      run.threadId,
      buildRunCallbacks(run),
      run.controller.signal
    )
  } catch (error) {
    if (error.name !== 'AbortError') {
      console.error('[App] 发送消息失败:', error)
    }
  } finally {
    retireRun(run)
  }

  loadSessions()
}

/**
 * 停止对话：中止最近一条正在生成的回复（排队中的不动）
 */
function handleStop() {
  const candidates = runs.value.filter(r => !r.queued)
  const target = candidates[candidates.length - 1] || runs.value[runs.value.length - 1]
  if (!target) return

  target.controller?.abort()
  console.log('[App] 用户停止了对话 run:', target.runId)
}

// ============================================================
// 中断恢复
// ============================================================

/**
 * 处理中断恢复（由 InterruptBanner 触发）
 *
 * @param {Object} resumeData - 恢复数据：
 *   - 数据补充: { supplement: "..." }
 *   - HITL 审批: { decisions: [{ type: "approve" }] }
 */
async function handleResume(resumeData) {
  if (isResuming.value || !currentThreadId.value) return

  isResuming.value = true

  const run = {
    runId: newId('resume'),
    threadId: currentThreadId.value,
    controller: new AbortController(),
    lastMessageId: null,
    queued: false
  }
  runs.value.push(run)

  try {
    await resumeChat(
      run.threadId,
      resumeData,
      buildRunCallbacks(run),
      run.controller.signal
    )
  } catch (error) {
    if (error.name !== 'AbortError') {
      console.error('[App] 恢复请求失败:', error)
      interruptData.value = null
    }
  } finally {
    isResuming.value = false
    retireRun(run)
  }
}

// ============================================================
// 异步子 Agent 后台任务
// ============================================================

/**
 * 从 start_async_task 的工具返回中捕获 task_id 并开始轮询
 */
function registerAsyncTask(run, tool) {
  const taskId = extractAsyncTaskId(tool.name, tool.text)
  if (!taskId) return
  if (asyncTasks.value.some(t => t.task_id === taskId)) return

  // 任务描述/子 Agent 名来自工具入参（args 是逐片段累积的 JSON，可能不完整）
  let description = ''
  let agentName = 'async-subagent'
  try {
    const args = JSON.parse(tool.args || '{}')
    description = args.description || ''
    agentName = args.subagent_type || agentName
  } catch {
    // args 尚未拼完整或被截断，忽略即可
  }

  const task = {
    task_id: taskId,
    agent_name: agentName,
    description,
    status: 'running',
    done: false,
    content: '',
    error: null,
    expanded: false,
    thread_id: run.threadId || currentThreadId.value
  }
  asyncTasks.value.push(task)
  schedulePoll(task, 0)
}

/**
 * 刷新页面 / 切换会话后，从服务端 checkpoint 恢复任务列表
 */
async function loadAsyncTasks(threadId) {
  try {
    const state = await getChatState(threadId)
    const tasks = state.async_tasks || {}

    for (const [taskId, info] of Object.entries(tasks)) {
      if (asyncTasks.value.some(t => t.task_id === taskId)) continue

      const terminal = ['success', 'error', 'interrupted', 'cancelled', 'timeout'].includes(info.status)
      const task = {
        task_id: taskId,
        agent_name: info.agent_name || 'async-subagent',
        description: '',
        status: info.status || 'running',
        done: terminal,
        content: '',
        error: null,
        expanded: false,
        thread_id: threadId
      }
      asyncTasks.value.push(task)
      if (!terminal) schedulePoll(task, 0)
    }
  } catch (error) {
    console.warn('[App] 恢复异步任务列表失败:', error)
  }
}

/**
 * 安排下一次轮询（4s → 8s → 15s 封顶）
 */
function schedulePoll(task, attempt) {
  const delay = Math.min(4000 * Math.pow(2, attempt), 15000)
  const timer = setTimeout(() => pollTask(task, attempt), delay)
  pollers.set(task.task_id, timer)
}

/**
 * 拉一次任务状态；终结后把结果写回消息流
 */
async function pollTask(task, attempt) {
  try {
    const status = await getAsyncTask(task.task_id)

    task.status = status.status || task.status
    task.error = status.error || null
    if (status.content) task.content = status.content
    task.done = Boolean(status.done)

    if (task.done) {
      pollers.delete(task.task_id)
      deliverTaskResult(task)
      return
    }
    schedulePoll(task, attempt + 1)
  } catch (error) {
    console.warn('[App] 轮询异步任务失败，稍后重试:', task.task_id, error)
    schedulePoll(task, attempt + 1)
  }
}

/**
 * 任务完成 → 作为一条助手消息插入当前会话
 *
 * 只在任务所属会话正打开时插入，避免污染其它会话；
 * 用户切回该会话时仍能在任务面板里看到结果。
 */
function deliverTaskResult(task) {
  if (task.thread_id !== currentThreadId.value) return
  if (messages.value.some(m => m.async_task_id === task.task_id)) return

  const header = task.status === 'success'
    ? `异步分析任务完成（${task.agent_name}）`
    : `异步分析任务结束（${task.agent_name}，状态：${task.status}）`
  const body = task.content || task.error || '（无输出内容）'

  messages.value.push({
    id: newId('async-result'),
    role: 'assistant',
    content: `**${header}**\n\n${body}`,
    source: 'async',
    async_task_id: task.task_id
  })
}

/**
 * 展开/收起任务结果
 */
function handleToggleTask(taskId) {
  const task = asyncTasks.value.find(t => t.task_id === taskId)
  if (task) task.expanded = !task.expanded
}

/**
 * 从面板移除任务（停止轮询）
 */
function handleDismissTask(taskId) {
  const timer = pollers.get(taskId)
  if (timer) {
    clearTimeout(timer)
    pollers.delete(taskId)
  }
  asyncTasks.value = asyncTasks.value.filter(t => t.task_id !== taskId)
}
</script>

<style scoped>
.app-container {
  display: flex;
  height: 100vh;
  background: #f8fafc;
  position: relative;
  overflow: hidden;
}

.main-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  width: 100%;
  background: #ffffff;
  position: relative;
  border-left: 1px solid #e2e8f0;
  box-shadow: 0 0 20px rgba(0, 0, 0, 0.05);
}
</style>
