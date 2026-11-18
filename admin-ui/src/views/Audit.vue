<template>
  <div>
    <h2 class="page-title">操作审计</h2>
    <el-card shadow="never">
      <div class="toolbar">
        <el-button @click="exportCsv">导出 CSV</el-button>
        <el-button type="danger" @click="prune">清理 90 天前</el-button>
      </div>
      <el-table :data="rows" size="small" v-loading="loading">
        <el-table-column label="时间" width="150">
          <template #default="{ row }">{{ fmtTime(row.at) }}</template>
        </el-table-column>
        <el-table-column prop="username" label="人" width="120" />
        <el-table-column prop="action" label="动作" width="160" />
        <el-table-column prop="target" label="对象" width="160" />
        <el-table-column prop="detail" label="说明" show-overflow-tooltip />
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import http from '../api'
import { fmtTime } from '../utils/format'

const rows = ref([])
const loading = ref(false)

async function load() {
  loading.value = true
  try {
    const r = await http.get('/audit', { params: { limit: 100 } })
    rows.value = r.data.rows
  } finally {
    loading.value = false
  }
}

function exportCsv() {
  window.open('/api/admin/audit.csv', '_blank')
}

async function prune() {
  await ElMessageBox.confirm('删掉 90 天前的审计记录？删了不能恢复', '确认', { type: 'warning' })
  const r = await http.post('/admin/audit/prune', { days: 90 })
  ElMessage.success(`删了 ${r.data.removed} 行，剩 ${r.data.left} 行`)
  load()
}

onMounted(load)
</script>

<style scoped>
.page-title { margin: 0 0 16px; font-size: 18px; }
.toolbar { display: flex; gap: 8px; margin-bottom: 16px; }
</style>
