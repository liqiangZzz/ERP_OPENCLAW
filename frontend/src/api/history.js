/**
 * 历史记录 API 模块
 *
 * 提供会话列表查询、会话消息获取和会话删除接口
 */

const API_BASE = '/api/history'

/**
 * 获取会话列表
 *
 * @param {number} page - 页码，从 1 开始
 * @param {number} limit - 每页数量
 * @returns {Promise} 返回会话列表响应 { sessions, total, page, limit }
 */
export async function getSessions(page = 1, limit = 20) {
  const response = await fetch(`${API_BASE}?page=${page}&limit=${limit}`)

  if (!response.ok) {
    throw new Error('获取会话列表失败')
  }

  return response.json()
}

/**
 * 获取会话消息历史
 *
 * @param {string} threadId - 会话 ID
 * @returns {Promise} 返回会话消息响应 { thread_id, messages }
 */
export async function getMessages(threadId) {
  const response = await fetch(`${API_BASE}/${threadId}/messages`)

  if (!response.ok) {
    throw new Error('获取会话消息失败')
  }

  return response.json()
}

/**
 * 删除会话
 *
 * @param {string} threadId - 会话 ID
 * @returns {Promise} 返回删除结果 { success, message }
 */
export async function deleteSession(threadId) {
  const response = await fetch(`${API_BASE}/${threadId}`, {
    method: 'DELETE'
  })

  if (!response.ok) {
    throw new Error('删除会话失败')
  }

  return response.json()
}

/**
 * 更新会话标题
 *
 * 注意：当前实现中标题是自动生成的，此接口为预留
 *
 * @param {string} threadId - 会话 ID
 * @param {string} title - 新的会话标题
 * @returns {Promise} 返回更新结果
 */
export async function updateSessionTitle(threadId, title) {
  const response = await fetch(`${API_BASE}/${threadId}?title=${encodeURIComponent(title)}`, {
    method: 'PATCH'
  })

  if (!response.ok) {
    throw new Error('更新会话标题失败')
  }

  return response.json()
}
