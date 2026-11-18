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

    <el-card shadow="never" class="mt16">
      <template #header>出站 Webhook（企业集成）</template>
      <el-form inline class="mt8">
        <el-form-item label="名称"><el-input v-model="w.name" placeholder="如 企业微信机器人" style="width:160px" /></el-form-item>
        <el-form-item label="URL"><el-input v-model="w.url" placeholder="https://..." style="width:280px" /></el-form-item>
        <el-form-item label="密钥"><el-input v-model="w.secret" placeholder="HMAC 签名，可留空" style="width:170px" /></el-form-item>
        <el-form-item label="事件"><el-input v-model="w.events" placeholder="new_items,source_failed" style="width:200px" /></el-form-item>
        <el-button type="primary" @click="addWebhook">添加</el-button>
      </el-form>
      <el-table :data="webhooks" class="mt16" size="small" empty-text="还没有 webhook">
        <el-table-column prop="name" label="名称" width="160" />
        <el-table-column prop="url" label="URL" show-overflow-tooltip />
        <el-table-column prop="events" label="事件" width="200" />
        <el-table-column label="最近结果" width="220">
          <template #default="{ row }">
            <span v-if="row.last_ok" style="color:#16a34a">{{ new Date(row.last_ok).toLocaleString() }} · 成功</span>
            <span v-else-if="row.last_error" style="color:#dc2626" :title="row.last_error">{{ row.last_error.slice(0, 30) }}</span>
            <span v-else style="color:#9ca3af">未发送过</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="80">
          <template #default="{ row }">
            <el-button link type="danger" @click="delWebhook(row.id)">删除</el-button>
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

const webhooks = ref([])
const w = ref({ name: '', url: '', secret: '', events: 'new_items' })

async function loadWebhooks() {
  const r = await http.get('/admin/webhooks')
  webhooks.value = r.data.webhooks || []
}

async function addWebhook() {
  if (!w.value.url.trim()) { ElMessage.warning('填 URL'); return }
  try {
    await http.post('/admin/webhooks', { ...w.value })
    w.value = { name: '', url: '', secret: '', events: 'new_items' }
    ElMessage.success('已添加')
    await loadWebhooks()
  } catch (e) { ElMessage.error(e.userMessage || '添加失败') }
}

async function delWebhook(id) {
  await http.delete(`/admin/webhooks/${id}`)
  ElMessage.success('已删除')
  await loadWebhooks()
}

onMounted(() => { loadSettings(); loadNotify(); loadWebhooks() })
</script>

<style scoped>
.page-title { margin: 0 0 16px; font-size: 18px; }
.set-row { display: flex; align-items: center; gap: 10px; padding: 6px 0; }
.mt16 { margin-top: 16px; }
.ntf-state { font-size: 13px; }
</style>
