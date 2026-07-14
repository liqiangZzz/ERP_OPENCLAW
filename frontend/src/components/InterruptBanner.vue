<template>
  <div class="interrupt-banner" :class="bannerClass">
    <!-- ============================================================ -->
    <!-- 类型 1：数据补充中断（request_order_info） -->
    <!-- ============================================================ -->
    <template v-if="interruptData.interrupt_type === 'order_info_supplement'">
      <div class="interrupt-header">
        <span class="interrupt-icon">📋</span>
        <span class="interrupt-title">订单数据不完整，请补充以下信息</span>
      </div>

      <div class="interrupt-body">
        <div class="info-block missing-fields">
          <div class="info-label">缺少字段</div>
          <div class="info-value">{{ interruptData.missing_fields }}</div>
        </div>
        <div class="info-block collected-data" v-if="interruptData.collected_data">
          <div class="info-label">已收集数据</div>
          <pre class="info-pre">{{ formatCollectedData(interruptData.collected_data) }}</pre>
        </div>
      </div>

      <div class="interrupt-input-area">
        <textarea
          ref="supplementInputRef"
          v-model="supplementText"
          placeholder="请用自然语言描述要补充的信息，例如：物料ID=100、数量=50、单价25.5元、预计6月15日交货..."
          class="supplement-textarea"
          rows="3"
          @keydown.enter.exact.prevent="handleSupplement"
        ></textarea>
        <button
          class="submit-btn supplement-btn"
          @click="handleSupplement"
          :disabled="!supplementText.trim() || submitting"
        >
          {{ submitting ? '提交中...' : '提交补充信息' }}
        </button>
      </div>
      <div class="interrupt-hint">按 Enter 提交，Shift + Enter 换行</div>
    </template>

    <!-- ============================================================ -->
    <!-- 类型 2：HITL 审批中断（order_create / order_update） -->
    <!-- ============================================================ -->
    <template v-else-if="interruptData.interrupt_type === 'hitl_approval'">
      <div class="interrupt-header">
        <span class="interrupt-icon">⚠️</span>
        <span class="interrupt-title">需要人工确认以下操作</span>
      </div>

      <div class="interrupt-body">
        <div
          v-for="(action, idx) in interruptData.action_requests"
          :key="idx"
          class="action-card"
        >
          <div class="action-header">
            <span class="action-type">{{ formatActionName(action.name) }}</span>
          </div>
          <div class="action-details">
            <table class="order-table" v-if="action.args">
              <tbody>
                <template v-for="(val, key) in displayArgs(action.args)" :key="key">
                  <tr v-if="key !== 'orderDetail'">
                    <td class="field-label">{{ formatFieldName(key) }}</td>
                    <td class="field-value">{{ formatFieldValue(key, val) }}</td>
                  </tr>
                </template>
              </tbody>
            </table>
            <!-- 订单明细子表 -->
            <div v-if="action.args && action.args.orderDetail && action.args.orderDetail.length" class="detail-table-wrap">
              <div class="detail-label">订单明细 ({{ action.args.orderDetail.length }} 项)</div>
              <table class="detail-table">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>物料ID</th>
                    <th>数量</th>
                    <th>单价</th>
                    <th>小计</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(item, di) in action.args.orderDetail" :key="di">
                    <td>{{ di + 1 }}</td>
                    <td>{{ item.partId || '-' }}</td>
                    <td>{{ item.quantity || '-' }}</td>
                    <td>{{ item.unitPrice != null ? item.unitPrice : '-' }}</td>
                    <td>{{ item.subtotal || '-' }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>

      <div class="interrupt-actions">
        <button
          class="submit-btn approve-btn"
          @click="handleDecision('approve')"
          :disabled="submitting"
        >
          {{ submitting ? '处理中...' : '✓ 同意' }}
        </button>
        <button
          class="submit-btn reject-btn"
          @click="handleDecision('reject')"
          :disabled="submitting"
        >
          {{ submitting ? '处理中...' : '✗ 拒绝' }}
        </button>
      </div>
    </template>

    <!-- ============================================================ -->
    <!-- 未知中断类型（透传） -->
    <!-- ============================================================ -->
    <template v-else>
      <div class="interrupt-header">
        <span class="interrupt-icon">⏸️</span>
        <span class="interrupt-title">Agent 已暂停，等待人工介入</span>
      </div>
      <div class="interrupt-body">
        <pre class="info-pre">{{ JSON.stringify(interruptData, null, 2) }}</pre>
      </div>
    </template>
  </div>
</template>

<script setup>
import {ref} from 'vue'

const props = defineProps({
  interruptData: { type: Object, required: true },
  submitting: { type: Boolean, default: false }
})

const emit = defineEmits(['resume'])

const supplementText = ref('')
const supplementInputRef = ref(null)

// ============================================================
// 数据补充 — 提交
// ============================================================

function handleSupplement() {
  const text = supplementText.value.trim()
  if (!text || props.submitting) return
  emit('resume', { supplement: text })
}

// ============================================================
// HITL 审批 — 提交决策
// ============================================================

function handleDecision(decisionType) {
  if (props.submitting) return
  emit('resume', { decisions: [{ type: decisionType }] })
}

// ============================================================
// 格式化辅助函数
// ============================================================

function formatCollectedData(data) {
  if (!data) return ''
  try {
    const parsed = typeof data === 'string' ? JSON.parse(data) : data
    return JSON.stringify(parsed, null, 2)
  } catch {
    return String(data)
  }
}

function formatActionName(name) {
  const map = {
    'order_create': '新建采购订单',
    'order_update': '修改采购订单'
  }
  return map[name] || name
}

function displayArgs(args) {
  // 过滤掉 orderDetail（单独展示），排除内部字段
  const skip = ['orderDetail']
  const result = {}
  for (const [k, v] of Object.entries(args)) {
    if (!skip.includes(k)) {
      result[k] = v
    }
  }
  return result
}

function formatFieldName(key) {
  const map = {
    'orderNumber': '订单编号',
    'totalAmount': '总金额',
    'status': '状态',
    'expectedDeliveryDate': '预计交货',
    'remark': '备注',
    'supplierId': '供应商ID',
    'supplierName': '供应商名称'
  }
  return map[key] || key
}

function formatFieldValue(key, val) {
  if (key === 'status') {
    const map = { 1: '待审核', 2: '已审核', 3: '采购中', 4: '已入库', 5: '已取消' }
    return map[val] != null ? map[val] : String(val)
  }
  if (val === null || val === undefined) return '-'
  return String(val)
}

// CSS class
const bannerClass = {
  'banner-supplement': props.interruptData.interrupt_type === 'order_info_supplement',
  'banner-approval': props.interruptData.interrupt_type === 'hitl_approval'
}
</script>

<style scoped>
.interrupt-banner {
  margin: 16px 24px;
  border-radius: 16px;
  overflow: hidden;
  box-shadow: 0 4px 24px rgba(0, 0, 0, 0.08);
  animation: slideUp 0.3s ease-out;
}

@keyframes slideUp {
  from { opacity: 0; transform: translateY(16px); }
  to { opacity: 1; transform: translateY(0); }
}

.banner-supplement {
  border: 2px solid #f59e0b;
  background: #fffbeb;
}

.banner-approval {
  border: 2px solid #ef4444;
  background: #fef2f2;
}

/* 头部 */
.interrupt-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 16px 20px;
  border-bottom: 1px solid rgba(0, 0, 0, 0.06);
}

.interrupt-icon { font-size: 22px; }

.interrupt-title {
  font-size: 16px;
  font-weight: 700;
  color: #1e293b;
}

/* 内容区 */
.interrupt-body {
  padding: 16px 20px;
}

.info-block {
  margin-bottom: 12px;
}

.info-block:last-child { margin-bottom: 0; }

.info-label {
  font-size: 12px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: #64748b;
  margin-bottom: 4px;
}

.missing-fields .info-value {
  font-size: 15px;
  color: #b45309;
  font-weight: 600;
  background: #fef3c7;
  padding: 8px 14px;
  border-radius: 8px;
}

.info-pre {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  padding: 10px 14px;
  border-radius: 8px;
  font-size: 13px;
  font-family: 'Monaco', 'Menlo', 'JetBrains Mono', monospace;
  overflow-x: auto;
  white-space: pre-wrap;
  margin: 0;
  line-height: 1.6;
}

/* 输入区 */
.interrupt-input-area {
  padding: 0 20px 16px;
  display: flex;
  gap: 10px;
  align-items: flex-end;
}

.supplement-textarea {
  flex: 1;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  padding: 10px 14px;
  font-size: 14px;
  line-height: 1.6;
  resize: none;
  outline: none;
  font-family: inherit;
  min-height: 60px;
  transition: border-color 0.2s;
}

.supplement-textarea:focus {
  border-color: #f59e0b;
  box-shadow: 0 0 0 3px rgba(245, 158, 11, 0.1);
}

.interrupt-hint {
  padding: 0 20px 12px;
  font-size: 12px;
  color: #94a3b8;
}

/* 操作卡片 */
.action-card {
  background: #ffffff;
  border: 1px solid #fecaca;
  border-radius: 12px;
  padding: 16px;
  margin-bottom: 12px;
}

.action-card:last-child { margin-bottom: 0; }

.action-header { margin-bottom: 12px; }

.action-type {
  font-size: 15px;
  font-weight: 700;
  color: #b91c1c;
  background: #fef2f2;
  padding: 4px 14px;
  border-radius: 6px;
}

/* 订单详情表 */
.order-table {
  width: 100%;
  border-collapse: collapse;
}

.order-table td {
  padding: 6px 0;
  font-size: 14px;
}

.field-label {
  width: 100px;
  color: #64748b;
  font-weight: 600;
}

.field-value {
  color: #1e293b;
}

.detail-table-wrap {
  margin-top: 12px;
}

.detail-label {
  font-size: 13px;
  font-weight: 600;
  color: #64748b;
  margin-bottom: 6px;
}

.detail-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.detail-table th {
  background: #fef2f2;
  padding: 8px 10px;
  text-align: left;
  font-weight: 600;
  color: #b91c1c;
  border-bottom: 1px solid #fecaca;
}

.detail-table td {
  padding: 8px 10px;
  border-bottom: 1px solid #f1f5f9;
}

/* 按钮 */
.submit-btn {
  padding: 12px 24px;
  border: none;
  border-radius: 12px;
  font-size: 14px;
  font-weight: 700;
  cursor: pointer;
  transition: all 0.2s ease;
}

.submit-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.supplement-btn {
  flex-shrink: 0;
  background: linear-gradient(135deg, #f59e0b 0%, #d97706 100%);
  color: #fff;
  box-shadow: 0 2px 8px rgba(245, 158, 11, 0.3);
}

.approve-btn {
  background: linear-gradient(135deg, #10b981 0%, #059669 100%);
  color: #fff;
  box-shadow: 0 2px 8px rgba(16, 185, 129, 0.3);
}

.reject-btn {
  background: linear-gradient(135deg, #ef4444 0%, #dc2626 100%);
  color: #fff;
  box-shadow: 0 2px 8px rgba(239, 68, 68, 0.3);
}

.interrupt-actions {
  display: flex;
  gap: 12px;
  padding: 0 20px 16px;
  justify-content: center;
}
</style>
