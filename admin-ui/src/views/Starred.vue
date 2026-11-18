<template>
  <div>
    <h2 class="page-title">收藏</h2>
    <el-card shadow="never">
      <div v-loading="loading">
        <el-table :data="starred" size="small">
          <el-table-column label="收藏时间" width="150">
            <template #default="{ row }">{{ fmtTime(row.starred_at) }}</template>
          </el-table-column>
          <el-table-column prop="source" label="来源" width="100" />
          <el-table-column prop="title" label="标题" />
          <el-table-column label="操作" width="160">
            <template #default="{ row }">
              <el-button size="small" text type="primary" @click="openBody(row)">看正文</el-button>
              <el-button size="small" text @click="unstar(row)">取消收藏</el-button>
            </template>
          </el-table-column>
        </el-table>
        <el-empty v-if="!loading && !starred.length" description="还没有收藏" />
      </div>
      <el-dialog v-model="bodyDialog" title="正文" width="70%">
        <div class="body-text">{{ bodyText || '（这条没抠到正文）' }}</div>
      </el-dialog>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import http from '../api'
import { fmtTime } from '../utils/format'

const starred = ref([])
const loading = ref(false)
const bodyDialog = ref(false)
const bodyText = ref('')

async function load() {
  loading.value = true
  try {
    const r = await http.get('/items', { params: { limit: 500 } })
    starred.value = r.data.items.filter((it) => it.starred)
  } finally {
    loading.value = false
  }
}

async function openBody(row) {
  const r = await http.get(`/items/${row.id}/text`)
  bodyText.value = r.data.text || ''
  bodyDialog.value = true
}

async function unstar(row) {
  await http.post(`/items/${row.id}/star`)
  ElMessage.success('已取消收藏')
  load()
}

onMounted(load)
</script>

<style scoped>
.page-title { margin: 0 0 16px; font-size: 18px; }
.body-text { white-space: pre-wrap; max-height: 60vh; overflow: auto; font-size: 14px; line-height: 1.7; }
</style>
