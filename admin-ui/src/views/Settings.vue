<template>
  <div>
    <h2 class="page-title">设置</h2>
    <el-card shadow="never">
      <template #header>通用开关</template>
      <div v-for="(row, i) in setRows" :key="i" class="set-row">
        <el-switch v-model="row.on" />
        <span>{{ row.label }}</span>
      </div>
      <el-button type="primary" class="mt16" @click="saveSettings">保存</el-button>
    </el-card>

    <el-card shadow="never" class="mt16">
      <template #header>推送（邮件）</template>
      <div class="ntf-state" v-html="ntfState"></div>
      <el-form label-width="110px" class="mt16">
        <el-form-item label="SMTP 服务器"><el-input v-model="n.host" placeholder="smtp.qq.com" /></el-form-item>
        <el-form-item label="端口"><el-input v-model="n.port" placeholder="465" style="width: 200px" /></el-form-item>
        <el-form-item label="发件邮箱"><el-input v-model="n.user" /></el-form-item>
        <el-form-item label="授权码"><el-input v-model="n.password" type="password" :placeholder="n.has_password ? '已保存，留空不改' : '授权码（不是登录密码）'" /></el-form-item>
        <el-form-item label="收件邮箱"><el-input v-model="n.to" placeholder="默认为发件人自己" /></el-form-item>
        <el-form-item label="自动发摘要"><el-switch v-model="n.enabled" /></el-form-item>
      </el-form>
      <el-button @click="saveNotify(false)">保存</el-button>
      <el-button type="primary" @click="saveNotify(true)">保存并发测试邮件</el-button>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import http from '../api'

const setRows = ref([
  { key: 'notify_toast', label: '新条目录屏提示', on: false },
  { key: 'notify_sound', label: '提示音', on: false },
  { key: 'notify_rules_only', label: '只提醒命中订阅词的条目', on: false },
  { key: 'notify.alert', label: '源连续失败时发邮件告警', on: false },
  { key: 'enrich.auto', label: '新条目自动抠正文', on: false },
])

const n = ref({ host: '', port: '', user: '', to: '', password: '', enabled: false, has_password: false })
const ntfState = ref('读取中…')

async function loadSettings() {
  const r = await http.get('/admin/settings')
  for (const row of setRows.value) row.on = r.data.values[row.key] === '1'
}

async function saveSettings() {
  const values = {}
  for (const row of setRows.value) values[row.key] = row.on ? '1' : '0'
  await http.patch('/admin/settings', { values })
  ElMessage.success('已保存')
}

async function loadNotify() {
  const r = await http.get('/admin/notify')
  const d = r.data
  n.value.host = d.host || ''
  n.value.port = d.port || ''
  n.value.user = d.user || ''
  n.value.to = d.to || ''
  n.value.enabled = String(d.enabled) === '1'
  n.value.has_password = !!d.has_password
  const last = d.last && d.last.at ? `上次发送 ${new Date(d.last.at).toLocaleString()} · ${d.last.ok ? '成功' : '失败'}` : '还没发过'
  ntfState.value = `${d.ready ? '<span style="color:#16a34a">已配置</span>' : '<span style="color:#9ca3af">未配置</span>'} · 收件 ${d.to || '-'} · ${last}`
}

async function saveNotify(sendTest) {
  const body = {
    host: n.value.host.trim(), port: n.value.port.trim(),
    user: n.value.user.trim(), to: n.value.to.trim(),
    enabled: n.value.enabled, send_test: sendTest,
  }
  if (n.value.password) body.password = n.value.password
  try {
    const r = await http.post('/admin/notify', body)
    n.value.password = ''
    if (r.data.error) ntfState.value = `<span style="color:#dc2626">${r.data.error}</span>`
    else if (r.data.sent_to) ntfState.value = `<span style="color:#16a34a">测试信已发到 ${r.data.sent_to}</span>`
    await loadNotify()
  } catch (e) {
    ntfState.value = `<span style="color:#dc2626">${e.userMessage}</span>`
  }
}

onMounted(() => { loadSettings(); loadNotify() })
</script>

<style scoped>
.page-title { margin: 0 0 16px; font-size: 18px; }
.set-row { display: flex; align-items: center; gap: 10px; padding: 6px 0; }
.mt16 { margin-top: 16px; }
.ntf-state { font-size: 13px; }
</style>
