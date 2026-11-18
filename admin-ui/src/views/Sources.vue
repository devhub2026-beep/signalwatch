<template>
  <div>
    <h2 class="page-title">数据源</h2>
    <el-card shadow="never">
      <el-table :data="sources" size="small" v-loading="loading">
        <el-table-column prop="name" label="源" width="120" />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="row.paused ? 'warning' : 'success'" size="small">{{ row.paused ? '已暂停' : '运行中' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="间隔(秒)" width="140">
          <template #default="{ row }">
            <el-input-number v-model="row.interval_seconds" :min="60" size="small" />
          </template>
        </el-table-column>
        <el-table-column label="上次运行" width="150">
          <template #default="{ row }">{{ fmtTime(row.last_run_at) }}</template>
        </el-table-column>
        <el-table-column prop="fail_count" label="失败数" width="80" />
        <el-table-column label="操作" width="160">
          <template #default="{ row }">
            <el-button size="small" text type="primary" @click="save(row)">保存间隔</el-button>
            <el-button size="small" text @click="toggle(row)">{{ row.paused ? '恢复' : '暂停' }}</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import http from '../api'
import { fmtTime } from '../utils/format'

const sources = ref([])
const loading = ref(false)

async function load() {
  loading.value = true
  try {
    const r = await http.get('/sources')
    sources.value = r.data.sources
  } finally {
    loading.value = false
  }
}

async function save(row) {
  await http.patch(`/sources/${encodeURIComponent(row.name)}`, { interval_seconds: row.interval_seconds })
  ElMessage.success('已保存')
}

async function toggle(row) {
  await http.patch(`/sources/${encodeURIComponent(row.name)}`, { enabled: row.paused })
  load()
}

onMounted(load)
</script>

<style scoped>
.page-title { margin: 0 0 16px; font-size: 18px; }
</style>
