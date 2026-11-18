<template>
  <div class="login-wrap">
    <el-card class="login-card">
      <h2 class="title">找回口令</h2>

      <el-steps :active="step" simple>
        <el-step title="填账号" />
        <el-step title="收验证码" />
        <el-step title="设新口令" />
      </el-steps>

      <div v-if="step === 0" class="pane">
        <el-input v-model="who" placeholder="用户名或注册时填的邮箱" />
        <el-button type="primary" class="w100 mt" :loading="loading" @click="onSend">发送验证码</el-button>
      </div>

      <div v-else class="pane">
        <div v-if="hint" class="hint">{{ hint }}</div>
        <el-input v-model="code" placeholder="6 位验证码" maxlength="6" />
        <el-input v-model="password" type="password" placeholder="新口令（至少 6 位）" show-password class="mt" />
        <el-button type="primary" class="w100 mt" :loading="loading" @click="onReset">改口令并登录</el-button>
        <el-button text class="w100 mt" @click="onSend">没收到？重新发一条</el-button>
      </div>

      <div v-if="err" class="err">{{ err }}</div>
      <div class="links">
        <router-link to="/login">回登录</router-link>
        <span class="sep">·</span>
        <router-link to="/register">注册新账号</router-link>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { auth } from '../stores/auth'
import http from '../api'

const router = useRouter()
const step = ref(0)
const who = ref('')
const code = ref('')
const password = ref('')
const loading = ref(false)
const err = ref('')
const hint = ref('')

async function onSend() {
  err.value = ''
  hint.value = ''
  if (!who.value) {
    err.value = '先填用户名或邮箱'
    return
  }
  loading.value = true
  try {
    const r = await http.post('/auth/forgot', { who: who.value })
    if (r.data.sent) {
      step.value = 1
      hint.value = `验证码已发到 ${r.data.to || '你的邮箱'}，10 分钟内有效`
    } else {
      err.value = r.data.hint || '没发出去，找管理员确认账号里有没有邮箱'
    }
  } catch (e) {
    err.value = e.userMessage || '发送失败'
  } finally {
    loading.value = false
  }
}

async function onReset() {
  err.value = ''
  if (!code.value || !password.value) {
    err.value = '验证码和新口令都要填'
    return
  }
  loading.value = true
  try {
    const r = await http.post('/auth/reset', { who: who.value, code: code.value, password: password.value })
    // 改完直接登录，省一次输入
    await auth.login(r.data.username, password.value)
    router.push('/overview')
  } catch (e) {
    err.value = e.userMessage || '改口令失败'
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-wrap { min-height: 100vh; display: flex; align-items: center; justify-content: center; background: #f3f4f6; padding: 24px 0; }
.login-card { width: 400px; }
.title { text-align: center; margin: 0 0 16px; }
.pane { margin-top: 14px; }
.w100 { width: 100%; }
.mt { margin-top: 10px; }
.err { color: #dc2626; font-size: 13px; margin-top: 10px; }
.hint { color: #059669; font-size: 13px; margin-bottom: 8px; }
.links { margin-top: 12px; font-size: 13px; }
.sep { margin: 0 6px; color: #d1d5db; }
</style>
