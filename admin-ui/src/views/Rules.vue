<template>
  <div>
    <h2 class="page-title">订阅词</h2>
    <el-card shadow="never">
      <div class="toolbar">
        <el-input v-model="kw" placeholder="关键词，如 中标 / 增持 / 回购" style="width: 260px" />
        <el-input v-model="cat" placeholder="分类（可不填）" style="width: 150px" />
        <el-input v-model="src" placeholder="来源（可不填）" style="width: 150px" />
        <el-button type="primary" @click="add">添加</el-button>
      </div>
      <el-table :data="rules" size="small" v-loading="loading">
        <el-table-column prop="id" label="ID" width="60" />
        <el-table-column prop="keyword" label="关键词" />
        <el-table-column label="分类" width="120">
          <template #default="{ row }">{{ row.category || '不限' }}</template>
        </el-table-column>
        <el-table-column label="来源" width="120">
          <template #default="{ row }">{{ row.source || '不限' }}</template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="row.enabled ? 'success' : 'info'" size="small">{{ row.enabled ? '启用' : '停用' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="160">
          <template #default="{ row }">
            <el-button size="small" text @click="toggle(row)">{{ row.enabled ? '停用' : '启用' }}</el-button>
            <el-button size="small" text type="danger" @click="del(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import http from '../api'

const kw = ref('')
const cat = ref('')
const src = ref('')
const rules = ref([])
const loading = ref(false)

async function load() {
  loading.value = true
  try {
    const r = await http.get('/admin/rules')
    rules.value = r.data.rules
  } finally {
    loading.value = false
  }
}

async function add() {
  const keyword = kw.value.trim()
  if (!keyword) return ElMessage.warning('填关键词')
  await http.post('/admin/rules', { keyword, category: cat.value.trim(), source: src.value.trim() })
  kw.value = cat.value = src.value = ''
  load()
}

async function toggle(row) {
  await http.patch(`/admin/rules/${row.id}`, { enabled: !row.enabled })
  load()
}

async function del(row) {
  await ElMessageBox.confirm('删除这个订阅词？', '确认', { type: 'warning' })
  await http.delete(`/admin/rules/${row.id}`)
  load()
}

onMounted(load)
</script>

<style scoped>
.page-title { margin: 0 0 16px; font-size: 18px; }
.toolbar { display: flex; gap: 8px; margin-bottom: 16px; flex-wrap: wrap; }
</style>
