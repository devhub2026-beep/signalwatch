<template>
  <div>
    <h2 class="page-title">用户</h2>
    <el-card shadow="never">
      <div class="toolbar">
        <el-input v-model="nu" placeholder="新用户名" style="width: 160px" />
        <el-input v-model="np" type="password" placeholder="口令（≥6）" style="width: 160px" />
        <el-select v-model="nr" style="width: 160px">
          <el-option value="viewer" label="viewer 只读" />
          <el-option value="analyst" label="analyst 可操作" />
          <el-option value="admin" label="admin 管理" />
        </el-select>
        <el-button type="primary" @click="add">加用户</el-button>
      </div>
      <el-table :data="users" size="small" v-loading="loading">
        <el-table-column prop="id" label="ID" width="60" />
        <el-table-column prop="username" label="用户名" />
        <el-table-column label="邮箱" width="220">
          <template #default="{ row }">
            <el-input v-model="row.email" size="small" placeholder="填了才收摘要" />
          </template>
        </el-table-column>
        <el-table-column label="角色" width="150">
          <template #default="{ row }">
            <el-select v-model="row.role" size="small" @change="saveRole(row)">
              <el-option v-for="r in roles" :key="r" :value="r" :label="r" />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="row.enabled ? 'success' : 'danger'" size="small">{{ row.enabled ? '启用' : '停用' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="220" fixed="right">
          <template #default="{ row }">
            <el-button size="small" text type="primary" @click="saveEmail(row)">存邮箱</el-button>
            <el-button size="small" text @click="toggle(row)">{{ row.enabled ? '停用' : '启用' }}</el-button>
            <el-button size="small" text @click="chpwd(row)">改口令</el-button>
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

const roles = ['viewer', 'auditor', 'analyst', 'admin']
const nu = ref('')
const np = ref('')
const nr = ref('viewer')
const users = ref([])
const loading = ref(false)

async function load() {
  loading.value = true
  try {
    const r = await http.get('/users')
    users.value = r.data.users
  } finally {
    loading.value = false
  }
}

async function add() {
  if (!nu.value.trim() || !np.value) return ElMessage.warning('填用户名和口令')
  await http.post('/users', { username: nu.value.trim(), password: np.value, role: nr.value })
  nu.value = ''; np.value = ''
  ElMessage.success('已添加')
  load()
}

async function saveEmail(row) {
  await http.patch(`/users/${encodeURIComponent(row.username)}`, { email: (row.email || '').trim() })
  ElMessage.success('已保存')
}

async function saveRole(row) {
  await http.patch(`/users/${encodeURIComponent(row.username)}`, { role: row.role })
  ElMessage.success('已改角色')
}

async function toggle(row) {
  await http.patch(`/users/${encodeURIComponent(row.username)}`, { enabled: !row.enabled })
  load()
}

async function chpwd(row) {
  const { value } = await ElMessageBox.prompt(`给 ${row.username} 设新口令（≥6 位）`, '改口令', { inputType: 'password' })
  if (!value) return
  await http.patch(`/users/${encodeURIComponent(row.username)}`, { password: value })
  ElMessage.success('已改')
}

async function del(row) {
  await ElMessageBox.confirm(`删除 ${row.username}？`, '确认', { type: 'warning' })
  await http.delete(`/users/${encodeURIComponent(row.username)}`)
  load()
}

onMounted(load)
</script>

<style scoped>
.page-title { margin: 0 0 16px; font-size: 18px; }
.toolbar { display: flex; gap: 8px; margin-bottom: 16px; flex-wrap: wrap; }
</style>
