/**
 * 对话 API 模块
 *
 * 提供流式对话、中断恢复和会话状态查询接口
 */

const API_BASE = '/api/chat'

/**
 * 获取或创建本地用户 ID
 *
 * 使用 localStorage 持久化一个随机 UUID，作为无登录场景下的用户标识
 */
function getUserId() {
  const KEY = 'erp_user_id'
  let userId = localStorage.getItem(KEY)
  if (!userId) {
    userId = crypto.randomUUID()
    localStorage.setItem(KEY, userId)
  }
  return userId
}

/**
 * 流式对话
 *
 * 使用 fetch + ReadableStream 接收流式响应（支持 POST 请求）
 *
 * @param {string} message - 用户消息
 * @param {string|null} threadId - 会话 ID，为空则创建新会话
 * @param {Object} callbacks - 回调函数对象
 * @param {Function} callbacks.onToken - 接收到 token 时的回调 (content: string, source: string) => void
 * @param {Function} callbacks.onToolStart - 工具开始调用时的回调 (tool: object) => void
 * @param {Function} callbacks.onToolArgs - 工具参数片段的回调 (args: string, source: string) => void
 * @param {Function} callbacks.onToolResult - 工具结果返回时的回调 (tool: object) => void
 * @param {Function} callbacks.onToolEnd - 工具调用结束时的回调 (tool: object) => void
 * @param {Function} callbacks.onInterrupt - 检测到中断时的回调 (interruptData: object) => void
 * @param {Function} callbacks.onDone - 流结束时的回调 (data: object) => void
 * @param {Function} callbacks.onError - 发生错误时的回调 (error: Error) => void
 * @param {AbortSignal} signal - 可选的 AbortSignal，用于取消请求
 * @returns {Promise} 返回包含 thread_id, content, tool_calls 的结果
 */
export async function streamChat(message, threadId = null, callbacks = {}, signal = null) {
  // 用于收集完整内容
  let fullContent = ''
  let toolCalls = []
  // 工具调用栈，支持嵌套工具调用（主代理调 task → 子代理调工具）
  let toolStack = []

  try {
    // 使用 fetch 发送 POST 请求，支持 AbortController
    const response = await fetch(`${API_BASE}/stream`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        message: message,
        thread_id: threadId,
        user_id: getUserId()
      }),
      signal: signal  // 支持取消请求
    })

    if (!response.ok) {
      throw new Error(`请求失败: ${response.status} ${response.statusText}`)
    }

    return await _processStream(response, threadId, callbacks, fullContent, toolCalls, toolStack)

  } catch (error) {
    if (error.name === 'AbortError') {
      // 用户主动取消，返回已收集的内容
      console.log('[ChatAPI] 请求已被用户取消')
      const result = {
        thread_id: threadId,
        content: fullContent,
        tool_calls: toolCalls,
        aborted: true
      }
      callbacks.onDone?.(result)
      return result
    }
    console.error('[ChatAPI] 流式请求失败:', error)
    callbacks.onError?.(error)
    throw error
  }
}

/**
 * 流式中断恢复
 *
 * 当中断发生后，前端收集用户决策/补充数据，通过此函数恢复 Agent 执行。
 *
 * @param {string} threadId - 会话 ID
 * @param {Object} resumeData - 恢复数据，格式取决于中断类型：
 *   - 数据补充: { supplement: "用户输入的补充信息" }
 *   - HITL 审批: { decisions: [{ type: "approve" }] } 或 [{ type: "reject" }]
 * @param {Object} callbacks - 回调函数对象（与 streamChat 相同）
 * @param {AbortSignal} signal - 可选的 AbortSignal，用于取消请求
 * @returns {Promise} 返回包含 thread_id, content, tool_calls 的结果
 */
export async function resumeChat(threadId, resumeData, callbacks = {}, signal = null) {
  let fullContent = ''
  let toolCalls = []
  let toolStack = []

  try {
    const response = await fetch(`${API_BASE}/${threadId}/resume`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ resume: resumeData }),
      signal: signal
    })

    if (!response.ok) {
      throw new Error(`恢复请求失败: ${response.status} ${response.statusText}`)
    }

    return await _processStream(response, threadId, callbacks, fullContent, toolCalls, toolStack)

  } catch (error) {
    if (error.name === 'AbortError') {
      console.log('[ChatAPI] 恢复请求已被用户取消')
      const result = {
        thread_id: threadId,
        content: fullContent,
        tool_calls: toolCalls,
        aborted: true
      }
      callbacks.onDone?.(result)
      return result
    }
    console.error('[ChatAPI] 恢复请求失败:', error)
    callbacks.onError?.(error)
    throw error
  }
}

/**
 * 处理流式响应的内部函数（streamChat 和 resumeChat 共用）
 */
async function _processStream(response, threadId, callbacks, fullContent, toolCalls, toolStack) {
  // 获取 reader 来读取流
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()

    if (done) {
      break
    }

    // 解码数据
    buffer += decoder.decode(value, { stream: true })

    // 处理缓冲区中的数据（按行分割）
    const lines = buffer.split('\n')
    buffer = lines.pop() // 保留未完成的行

    for (const line of lines) {
      if (!line.trim() || !line.startsWith('data:')) {
        continue
      }

      let data
      try {
        data = JSON.parse(line.slice(5).trim())
      } catch (parseError) {
        // 仅忽略真正的 JSON 解析错误，业务错误必须继续向上抛出
        console.warn('[ChatAPI] 解析 SSE 数据失败:', parseError)
        continue
      }

      switch (data.type) {
          case 'token':
            // AI 生成的文本片段
            fullContent += data.content
            callbacks.onToken?.(data.content, data.source)
            break

          case 'tool_start':
            // 工具开始调用，压入栈
            const newTool = {
              id: data.tool_call_id,
              name: data.tool_name,
              args: '',
              source: data.source,
              text: '',
              images: []
            }
            toolStack.push(newTool)
            callbacks.onToolStart?.(newTool)
            break

          case 'tool_args':
            // 工具参数片段，更新栈顶工具
            if (toolStack.length > 0) {
              toolStack[toolStack.length - 1].args += data.args
            }
            callbacks.onToolArgs?.(data.args, data.source)
            break

          case 'tool_result':
            // 工具执行结果，弹出栈顶工具并更新
            if (toolStack.length > 0) {
              const finished = toolStack.pop()
              finished.text = data.text || ''
              finished.images = data.images || []
              toolCalls.push({ ...finished })
              callbacks.onToolResult?.(finished)
            }
            break

          case 'tool_end':
            // 工具调用结束
            callbacks.onToolEnd?.({
              id: data.tool_call_id,
              name: data.tool_name,
              source: data.source
            })
            break

          case 'interrupt':
            // ★ Human-in-the-Loop 中断检测
            console.log('[ChatAPI] 检测到中断:', data.interrupt_type)
            callbacks.onInterrupt?.(data)
            // 中断事件后流会结束，返回当前已收集的内容
            return {
              thread_id: data.thread_id || threadId,
              content: fullContent,
              tool_calls: toolCalls,
              interrupted: true,
              interrupt_data: data
            }

          case 'done':
            // 流结束
            const result = {
              thread_id: data.thread_id,
              content: data.content || fullContent,
              tool_calls: toolCalls,
              interrupted: data.interrupted || false
            }
            callbacks.onDone?.(result)
            return result

          case 'error':
            // 错误
            throw new Error(data.message)

        default:
          break
      }
    }
  }

  // 如果没有收到 done 事件，返回已收集的内容
  return {
    thread_id: threadId,
    content: fullContent,
    tool_calls: toolCalls
  }
}

/**
 * 获取会话状态
 *
 * @param {string} threadId - 会话 ID
 * @returns {Promise} 返回会话状态对象
 */
export async function getChatState(threadId) {
  const response = await fetch(`${API_BASE}/${threadId}`)

  if (!response.ok) {
    throw new Error('获取会话状态失败')
  }

  return response.json()
}

/**
 * 获取会话历史状态列表
 *
 * @param {string} threadId - 会话 ID
 * @param {number} limit - 返回的最大状态数量
 * @returns {Promise} 返回状态历史列表
 */
export async function getChatHistory(threadId, limit = 50) {
  const response = await fetch(`${API_BASE}/${threadId}/history?limit=${limit}`)

  if (!response.ok) {
    throw new Error('获取会话历史失败')
  }

  return response.json()
}
