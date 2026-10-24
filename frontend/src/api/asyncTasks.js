/**
 * 异步子 Agent 后台任务 API
 *
 * 主 Agent 通过 start_async_task 把耗时分析派给 Agent Protocol 上的子 Agent。
 * 由于子 Agent 在另一个 deployment（AsyncSubAgent spec 带 url），deepagents 会
 * 丢弃父 Agent 的 assistant_id，「任务完成自动唤醒父 Agent」在本地 FastAPI 部署
 * 下不成立，因此结果必须由前端轮询 /api/async-tasks/{task_id} 拿回来。
 */

const API_BASE = '/api'

/**
 * 查询单个异步任务状态
 *
 * @param {string} taskId - start_async_task 返回的 task_id（即子 Agent 的 thread_id）
 * @returns {Promise<Object>} { task_id, status, done, content, error, run_id, updated_at }
 */
export async function getAsyncTask(taskId) {
  const response = await fetch(`${API_BASE}/async-tasks/${encodeURIComponent(taskId)}`)

  if (!response.ok) {
    throw new Error(`查询异步任务失败: ${response.status} ${response.statusText}`)
  }

  return response.json()
}

/**
 * 从 start_async_task 的工具返回文本中解析 task_id
 *
 * deepagents 返回的固定格式：`Launched async subagent. task_id: <uuid>`
 * （见 site-packages/deepagents/middleware/async_subagents.py 中 astart_async_task）
 *
 * @param {string} toolName - 工具名
 * @param {string} text - 工具返回文本
 * @returns {string|null} 解析出的 task_id，非异步任务工具或解析失败返回 null
 */
export function extractAsyncTaskId(toolName, text) {
  if (toolName !== 'start_async_task' || !text) return null
  const matched = /task_id:\s*([0-9a-zA-Z-]+)/.exec(text)
  return matched ? matched[1] : null
}

/**
 * 任务是否已终结（不再需要轮询）
 *
 * @param {Object} status - getAsyncTask 返回值
 * @returns {boolean}
 */
export function isTaskTerminal(status) {
  return Boolean(status && status.done)
}
