<template>
  <div class="login-wrap">
    <el-card class="login-card">
      <h2 class="title">注册 SignalWatch</h2>
      <el-form @submit.prevent="onRegister">
        <el-form-item>
          <el-input v-model="username" placeholder="用户名（3–32 位字母数字）" autocomplete="username" />
        </el-form-item>
        <el-form-item>
          <el-input v-model="password" type="password" placeholder="口令（至少 6 位）" show-password
                    autocomplete="new-password" />
        </el-form-item>
        <el-form-item>
          <el-input v-model="email" placeholder="邮箱（找回口令要用，可后补）" autocomplete="email" />
        </el-form-item>
        <el-button type="primary" class="w100" :loading="loading" @click="onRegister">注册并登录</el-button>
        <div v-if="err" class="err">{{ err }}</div>
        <div v-if="ok" class="ok">{{ ok }}</div>
        <div class="muted">新账号是「只读」角色，管理员可以在「用户」页提权。</div>
        <div class="links">
          <router-link to="/login">已有账号，去登录</router-link>
        </div>
      </el-form>
    </el-card>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { auth } from '../stores/auth'
import http from '../api'

const router = useRouter()
const username = ref('')
const password = ref('')
const email = ref('')
const loading = ref(false)
const err = ref('')
const ok = ref('')

async function onRegister() {
  err.value = ''
  ok.value = ''
  if (!username.value || !password.value) {
    err.value = '用户名和口令都要填'
    return
  }
  loading.value = true
  try {
    const r = await http.post('/auth/register', {
      username: username.value,
      password: password.value,
      email: email.value,
    })
    auth.user = r.data.user
    ok.value = '注册成功，正在进入…'
    router.push('/overview')
  } catch (e) {
    err.value = e.userMessage || '注册失败'
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-wrap { min-height: 100vh; display: flex; align-items: center; justify-content: center; background: #f3f4f6; padding: 24px 0; }
.login-card { width: 380px; }
.title { text-align: center; margin: 0 0 20px; }
.w100 { width: 100%; }
.err { color: #dc2626; font-size: 13px; margin-top: 8px; }
.ok { color: #059669; font-size: 13px; margin-top: 8px; }
.muted { color: #9ca3af; font-size: 12px; margin-top: 8px; }
.links { margin-top: 10px; font-size: 13px; }
</style>
