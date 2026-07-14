<template>
  <div class="markdown-renderer" v-html="renderedContent"></div>
</template>

<script setup>
import {onMounted, ref, watch} from 'vue'
import MarkdownIt from 'markdown-it'
import hljs from 'highlight.js'

/**
 * Markdown 渲染组件
 *
 * 图片样式采用内联 style 方式注入到 img 标签中，
 * 因为 v-html 渲染的内容不受 Vue scoped CSS 影响。
 */

const props = defineProps({
  content: {
    type: String,
    default: ''
  }
})

const renderedContent = ref('')

let md = null

// 图片内联样式（确保在任何容器中都能正确缩放）
const IMG_INLINE_STYLE = 'max-width:100%;max-height:360px;width:auto;height:auto;object-fit:contain;display:block;border-radius:12px;margin:14px 0;box-shadow:0 2px 8px rgba(0,0,0,0.08);'

function isKnownImageHost(url) {
  const imageHosts = [
    'mdn.alipayobjects.com',
    'img.alicdn.com',
    'gw.alipayobjects.com',
    'zos.alipayobjects.com',
  ]
  try {
    const hostname = new URL(url).hostname
    return imageHosts.some(h => hostname.includes(h))
  } catch {
    return false
  }
}

function shouldRenderAsImage(src) {
  if (/\.(png|jpg|jpeg|gif|webp|svg|bmp)(\?.*)?$/i.test(src)) return true
  if (/^data:image\//i.test(src)) return true
  if (isKnownImageHost(src)) return true
  return false
}

function buildImgTag(src, alt) {
  return `<img src="${src}" alt="${alt || '图片'}" class="markdown-image" loading="lazy" style="${IMG_INLINE_STYLE}" />`
}

onMounted(() => {
  md = new MarkdownIt({
    html: true,
    xhtmlOut: true,
    breaks: true,
    linkify: true,
    typographer: true,
    highlight: function (str, lang) {
      if (lang && hljs.getLanguage(lang)) {
        try {
          return hljs.highlight(str, { language: lang }).value
        } catch (__) {}
      }
      return ''
    }
  })

  md.renderer.rules.image = function (tokens, idx, options, env, self) {
    const token = tokens[idx]
    const src = token.attrs[token.attrIndex('src')][1]
    const alt = token.content

    if (shouldRenderAsImage(src)) {
      return buildImgTag(src, alt)
    }

    return `<a href="${src}" target="_blank" rel="noopener noreferrer" class="image-link">🖼️ ${alt || src}</a>`
  }

  const defaultTextRenderer = md.renderer.rules.text || function (tokens, idx) {
    return md.utils.escapeHtml(tokens[idx].content)
  }

  md.renderer.rules.text = function (tokens, idx, options, env, self) {
    const content = tokens[idx].content
    // 检测 data:image base64
    if (/data:image\/[^;]+;base64,/.test(content)) {
      const imgMatch = content.match(/(data:image\/[^;]+;base64,[A-Za-z0-9+/=]+)/)
      if (imgMatch) {
        return buildImgTag(imgMatch[1], '图片')
      }
    }
    // 检测图片 CDN 域名
    for (const host of ['mdn.alipayobjects.com', 'img.alicdn.com', 'gw.alipayobjects.com']) {
      const regex = new RegExp(`(https?://[^\\s]*${host.replace(/\./g, '\\.')}[^\\s]*)`)
      const match = content.match(regex)
      if (match) {
        return buildImgTag(match[1], '图片')
      }
    }
    return defaultTextRenderer(tokens, idx, options, env, self)
  }

  renderContent()
})

function renderContent() {
  if (!md || !props.content) {
    renderedContent.value = escapeHtml(props.content || '')
    return
  }

  try {
    renderedContent.value = md.render(props.content)
  } catch (error) {
    console.error('[MarkdownRenderer] 渲染失败:', error)
    renderedContent.value = escapeHtml(props.content)
  }
}

function escapeHtml(text) {
  const div = document.createElement('div')
  div.textContent = text
  return div.innerHTML
}

watch(() => props.content, () => {
  renderContent()
})
</script>

<!-- scoped 块：仅作用于组件根元素及模板中的 DOM -->
<style scoped>
.markdown-renderer {
  font-size: 15px;
  line-height: 1.85;
  color: #1e293b;
  word-break: break-word;
  overflow-wrap: break-word;
  /* 关键：防止任何子元素溢出容器 */
  overflow: hidden;
}
</style>

<!-- 非 scoped 块：作用于 v-html 动态内容（这些元素没有 data-v-xxx 属性） -->
<style>
/* ============ 标题样式 ============ */
.markdown-renderer h1 {
  font-size: 26px;
  font-weight: 700;
  margin: 24px 0 14px;
  padding-bottom: 10px;
  border-bottom: 2px solid #e2e8f0;
  color: #0f172a;
}

.markdown-renderer h2 {
  font-size: 22px;
  font-weight: 600;
  margin: 20px 0 12px;
  color: #1e293b;
}

.markdown-renderer h3 {
  font-size: 18px;
  font-weight: 600;
  margin: 18px 0 10px;
  color: #334155;
}

.markdown-renderer h4,
.markdown-renderer h5,
.markdown-renderer h6 {
  font-size: 16px;
  font-weight: 600;
  margin: 16px 0 8px;
  color: #475569;
}

/* ============ 段落样式 ============ */
.markdown-renderer p {
  margin: 12px 0;
}

/* ============ 列表样式 ============ */
.markdown-renderer ul,
.markdown-renderer ol {
  margin: 12px 0;
  padding-left: 30px;
}

.markdown-renderer li {
  margin: 8px 0;
}

.markdown-renderer ul li {
  list-style-type: disc;
  color: #334155;
}

.markdown-renderer ol li {
  list-style-type: decimal;
  color: #334155;
}

/* ============ 代码样式 ============ */
.markdown-renderer code {
  padding: 3px 8px;
  background: #f1f5f9;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  font-family: 'Monaco', 'Menlo', 'JetBrains Mono', monospace;
  font-size: 14px;
  color: #0ea5e9;
}

.markdown-renderer pre {
  margin: 14px 0;
  padding: 18px;
  background: #1e293b;
  border-radius: 12px;
  overflow-x: auto;
}

.markdown-renderer pre code {
  padding: 0;
  background: none;
  border: none;
  color: #e2e8f0;
  font-size: 14px;
  line-height: 1.7;
}

.markdown-renderer .hljs {
  background: transparent;
}

.markdown-renderer .hljs-keyword,
.markdown-renderer .hljs-selector-tag,
.markdown-renderer .hljs-built_in,
.markdown-renderer .hljs-name,
.markdown-renderer .hljs-tag {
  color: #60a5fa;
}

.markdown-renderer .hljs-string,
.markdown-renderer .hljs-title,
.markdown-renderer .hljs-section,
.markdown-renderer .hljs-attribute,
.markdown-renderer .hljs-literal,
.markdown-renderer .hljs-template-tag,
.markdown-renderer .hljs-template-variable,
.markdown-renderer .hljs-type,
.markdown-renderer .hljs-addition {
  color: #f59e0b;
}

.markdown-renderer .hljs-comment,
.markdown-renderer .hljs-quote,
.markdown-renderer .hljs-deletion,
.markdown-renderer .hljs-meta {
  color: #6ee7b7;
}

.markdown-renderer .hljs-number,
.markdown-renderer .hljs-regexp,
.markdown-renderer .hljs-selector-attr,
.markdown-renderer .hljs-selector-pseudo,
.markdown-renderer .hljs-variable {
  color: #94a3b8;
}

.markdown-renderer .hljs-function {
  color: #facc15;
}

.markdown-renderer .hljs-class .hljs-title {
  color: #34d399;
}

/* ============ 链接样式 ============ */
.markdown-renderer a {
  color: #0ea5e9;
  text-decoration: none;
  border-bottom: 1px solid transparent;
  transition: all 0.2s ease;
}

.markdown-renderer a:hover {
  border-bottom-color: #0ea5e9;
}

/* ============ 图片样式（兜底，内联 style 已处理主要约束）============ */
.markdown-renderer img.markdown-image {
  max-width: 100% !important;
  max-height: 360px;
  width: auto;
  height: auto;
  object-fit: contain;
  border-radius: 12px;
  margin: 14px 0;
  display: block;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
}

.markdown-renderer a.image-link {
  display: inline-block;
  padding: 10px 16px;
  background: #f1f5f9;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  margin: 10px 0;
  color: #0ea5e9;
  transition: all 0.2s ease;
}

.markdown-renderer a.image-link:hover {
  background: #e0f2fe;
  border-color: #0ea5e9;
}

/* ============ 表格样式 ============ */
.markdown-renderer table {
  width: 100%;
  border-collapse: collapse;
  margin: 14px 0;
  font-size: 14px;
}

.markdown-renderer th,
.markdown-renderer td {
  padding: 12px 14px;
  border: 1px solid #e2e8f0;
  text-align: left;
}

.markdown-renderer th {
  background: #f8fafc;
  font-weight: 600;
  color: #1e293b;
}

.markdown-renderer tr:nth-child(even) {
  background: #f8fafc;
}

.markdown-renderer tr:hover {
  background: #f1f5f9;
}

/* ============ 引用样式 ============ */
.markdown-renderer blockquote {
  margin: 14px 0;
  padding: 14px 18px;
  background: #f8fafc;
  border-left: 4px solid #0ea5e9;
  border-radius: 0 8px 8px 0;
  color: #64748b;
  font-style: italic;
}

/* ============ 分割线样式 ============ */
.markdown-renderer hr {
  border: none;
  height: 1px;
  background: linear-gradient(90deg, transparent, #e2e8f0, transparent);
  margin: 24px 0;
}

/* ============ 任务列表样式 ============ */
.markdown-renderer input[type="checkbox"] {
  margin-right: 10px;
  vertical-align: middle;
  accent-color: #0ea5e9;
}

.markdown-renderer input[type="checkbox"]:checked + span {
  text-decoration: line-through;
  color: #94a3b8;
}
</style>
