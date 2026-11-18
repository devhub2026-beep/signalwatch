<template>
  <div>
    <h2 class="page-title">首页 · 时间线</h2>
    <el-card shadow="never">
      <div class="toolbar">
        <el-input v-model="q" placeholder="搜索标题 / 公司 / 代码" clearable style="width: 260px" @keyup.enter="load" />
        <el-button type="primary" @click="load">搜索</el-button>
        <el-button @click="q = ''; load()">重置</el-button>
      </div>
      <div v-loading="loading">
        <div v-for="it in items" :key="it.id" class="feed-item">
          <div class="feed-head">
            <span class="src">{{ it.source }}</span>
            <span class="time">{{ fmtTime(it.published_at) }}</span>
            <span v-if="it.starred" class="starred">★</span>
          </div>
          <div class="feed-title">{{ it.title }}</div>
          <div v-if="it.summary" class="feed-summary">{{ it.summary }}</div>
          <div class="feed-actions">
            <el-button size="small" text type="primary" @click="toggleBody(it)">{{ it.open ? '收起' : '看正文' }}</el-button>
            <el-button size="small" text @click="toggleStar(it)">{{ it.starred ? '取消收藏' : '收藏' }}</el-button>
            <el-button size="small" text @click="openUrl(it.url)">原文</el-button>
          </div>
          <div v-if="it.open" class="feed-body">{{ it.body || '（这条没抠到正文）' }}</div>
        </div>
        <el-empty v-if="!loading && !items.length" description="没有条目" />
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import http from '../api'
import { fmtTime } from '../utils/format'

const q = ref('')
const items = ref([])
const loading = ref(false)

async function load() {
  loading.value = true
  try {
    const r = await http.get('/items', { params: { limit: 100, q: q.value || undefined } })
    items.value = r.data.items.map((it) => ({ ...it, open: false, body: '' }))
  } finally {
    loading.value = false
  }
}

async function toggleBody(it) {
  if (it.open) { it.open = false; return }
  if (!it.body) {
    const r = await http.get(`/items/${it.id}/text`)
    it.body = r.data.text || ''
  }
  it.open = true
}

async function toggleStar(it) {
  const r = await http.post(`/items/${it.id}/star`)
  it.starred = r.data.starred
  ElMessage.success(r.data.starred ? '已收藏' : '已取消收藏')
}

function openUrl(url) {
  if (url) window.open(url, '_blank', 'noopener')
}

onMounted(load)
</script>

<style scoped>
.page-title { margin: 0 0 16px; font-size: 18px; }
.toolbar { display: flex; gap: 8px; margin-bottom: 16px; }
.feed-item { padding: 12px 0; border-bottom: 1px solid #f0f0f0; }
.feed-head { display: flex; align-items: center; gap: 8px; font-size: 12px; color: #9ca3af; }
.src { color: #2563eb; }
.starred { color: #f59e0b; }
.feed-title { font-size: 15px; font-weight: 600; margin: 6px 0; }
.feed-summary { font-size: 13px; color: #6b7280; }
.feed-actions { margin-top: 6px; }
.feed-body { margin-top: 8px; padding: 12px; background: #f9fafb; border-radius: 6px; font-size: 13px; white-space: pre-wrap; max-height: 400px; overflow: auto; }
</style>
