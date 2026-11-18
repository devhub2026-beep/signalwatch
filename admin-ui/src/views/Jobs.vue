<template>
  <div>
    <h2 class="page-title">抓取任务</h2>
    <el-card shadow="never">
      <div class="toolbar">
        <el-button type="primary" @click="enqueue('all', '')">全部抓一轮</el-button>
        <el-button v-for="s in ['cninfo', 'ithome', 'sspai', 'arxiv']" :key="s" @click="enqueue('source', s)">{{ s }}</el-button>
        <el-button @click="load">刷新</el-button>
        <span class="counts">{{ countsText }}</span>
      </div>
      <el-table :data="jobs" size="small" v-loading="loading">
        <el-table-column prop="id" label="#" width="60" />
        <el-table-column prop="kind" label="类型" width="100" />
        <el-table-column label="对象" width="100">
          <template #default="{ row }">{{ row.target || '全部' }}</template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="statusType(row.status)">{{ statusText[row.status] || row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="created_by" label="发起人" width="100" />
        <el-table-column label="结果" show-overflow-tooltip>
          <template #default="{ row }">{{ (row.result || row.error || '').slice(0, 120) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="160" fixed="right">
          <template #default="{ row }">
            <el-button v-if="row.status === 'failed'" size="small" text @click="act(row, 'retry')">重试</el-button>
            <el-button v-if="row.status === 'running'" size="small" text @click="act(row, 'pause')">暂停</el-button>
            <el-button v-if="row.status === 'paused'" size="small" text @click="act(row, 'resume')">继续</el-button>
            <el-button v-if="['queued','running','paused'].includes(row.status)" size="small" text type="danger" @click="act(row, 'cancel')">取消</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import http from '../api'

const jobs = ref([])
const counts = ref({})
const loading = ref(false)

const statusText = { queued: '排队', running: '运行中', paused: '已暂停', done: '已完成', failed: '失败', cancelled: '已取消' }
const statusType = (s) => ({ queued: 'info', running: 'primary', paused: 'warning', done: 'success', failed: 'danger', cancelled: 'info' }[s] || 'info')

const countsText = computed(() => {
  const c = counts.value
  return `排队 ${c.queued || 0} · 运行 ${c.running || 0} · 暂停 ${c.paused || 0} · 完成 ${c.done || 0} · 失败 ${c.failed || 0} · 取消 ${c.cancelled || 0}`
})

async function load() {
  loading.value = true
  try {
    const r = await http.get('/admin/jobs')
    jobs.value = r.data.jobs
    counts.value = r.data.counts
  } finally {
    loading.value = false
  }
}

async function enqueue(kind, target) {
  await http.post('/admin/jobs', { kind, target, priority: 5 })
  ElMessage.success('已排队')
  load()
}

async function act(row, action) {
  await http.post(`/admin/jobs/${row.id}/${action}`)
  load()
}

onMounted(load)
</script>

<style scoped>
.page-title { margin: 0 0 16px; font-size: 18px; }
.toolbar { display: flex; gap: 8px; margin-bottom: 16px; flex-wrap: wrap; align-items: center; }
.counts { margin-left: auto; color: #6b7280; font-size: 13px; }
</style>
