<template>
  <div>
    <h2 class="page-title">我的</h2>
    <el-card shadow="never">
      <template #header>账号信息</template>
      <el-descriptions :column="1" border size="small">
        <el-descriptions-item label="账号">{{ auth.user?.username || '—' }}</el-descriptions-item>
        <el-descriptions-item label="角色">{{ fmtRole(auth.user?.role) }}</el-descriptions-item>
        <el-descriptions-item label="上次登录">{{ fmtTime(auth.user?.last_login_at) }}</el-descriptions-item>
        <el-descriptions-item label="我建的订阅词">{{ myRules }} 条</el-descriptions-item>
      </el-descriptions>
    </el-card>

    <el-card shadow="never" class="mt16">
      <template #header>改口令</template>
      <el-form label-width="100px" style="max-width: 420px">
        <el-form-item label="原口令"><el-input v-model="old" type="password" /></el-form-item>
        <el-form-item label="新口令"><el-input v-model="next" type="password" placeholder="≥6 位" /></el-form-item>
        <el-form-item>
          <el-button type="primary" @click="save">保存</el-button>
        </el-form-item>
      </el-form>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { useRouter } from 'vue-router'
import http from '../api'
import { auth } from '../stores/auth'
import { fmtTime, fmtRole } from '../utils/format'

const router = useRouter()
const old = ref('')
const next = ref('')
const myRules = ref(0)

onMounted(async () => {
  try {
    const r = await http.get('/admin/rules')
    myRules.value = r.data.rules.length
  } catch { /* 无权限忽略 */ }
})

async function save() {
  if (!old.value || !next.value) return ElMessage.warning('填原口令和新口令')
  await http.post('/auth/password', { old: old.value, new: next.value })
  ElMessage.success('口令已改，请重新登录')
  await auth.logout()
  router.push('/login')
}
</script>

<style scoped>
.page-title { margin: 0 0 16px; font-size: 18px; }
.mt16 { margin-top: 16px; }
</style>
