<template>
  <div>
    <h2 class="page-title">分类</h2>
    <el-card shadow="never">
      <div class="toolbar">
        <el-input v-model="name" placeholder="新分类名" style="width: 220px" @keyup.enter="add" />
        <el-button type="primary" @click="add">新增</el-button>
      </div>
      <el-table :data="cats" size="small" v-loading="loading">
        <el-table-column prop="name" label="分类" />
        <el-table-column prop="items" label="条目" width="100" />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="row.enabled ? 'success' : 'info'" size="small">{{ row.enabled ? '启用' : '停用' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="120">
          <template #default="{ row }">
            <el-button size="small" text @click="toggle(row)">{{ row.enabled ? '停用' : '启用' }}</el-button>
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

const name = ref('')
const cats = ref([])
const loading = ref(false)

async function load() {
  loading.value = true
  try {
    const r = await http.get('/admin/categories')
    cats.value = r.data.categories
  } finally {
    loading.value = false
  }
}

async function add() {
  const n = name.value.trim()
  if (!n) return
  await http.post('/admin/categories', { name: n })
  name.value = ''
  ElMessage.success('已新增')
  load()
}

async function toggle(row) {
  await http.patch(`/admin/categories/${row.id}`, { enabled: !row.enabled })
  load()
}

onMounted(load)
</script>

<style scoped>
.page-title { margin: 0 0 16px; font-size: 18px; }
.toolbar { display: flex; gap: 8px; margin-bottom: 16px; }
</style>
