<template>
  <div class="login-wrap">
    <el-card class="login-card">
      <h2 class="title">SignalWatch 管理台</h2>
      <el-form @submit.prevent="onLogin">
        <el-form-item>
          <el-input v-model="username" placeholder="用户名" autocomplete="username" />
        </el-form-item>
        <el-form-item>
          <el-input v-model="password" type="password" placeholder="口令" show-password
                    autocomplete="current-password" @keyup.enter="onLogin" />
        </el-form-item>
        <el-button type="primary" class="w100" :loading="loading" @click="onLogin">登录</el-button>
        <div v-if="err" class="err">{{ err }}</div>
        <div class="links">
          <router-link to="/forgot">忘记口令？</router-link>
          <span class="sep">·</span>
          <router-link to="/register">注册新账号</router-link>
        </div>
      </el-form>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { auth } from '../stores/auth'
import http from '../api'

const router = useRouter()
const route = useRoute()
const username = ref('admin')
const password = ref('')
const loading = ref(false)
const err = ref('')
const autoTried = ref(false)

async function onLogin() {
  if (!username.value || !password.value) {
    err.value = '请输入用户名和口令'
    return
  }
  loading.value = true
  err.value = ''
  try {
    await auth.login(username.value, password.value)
    router.push(route.query.redirect || '/overview')
  } catch (e) {
    err.value = e.userMessage || '登录失败'
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-wrap { height: 100vh; display: flex; align-items: center; justify-content: center; background: #f3f4f6; }
.login-card { width: 360px; }
.title { text-align: center; margin: 0 0 20px; }
.w100 { width: 100%; }
.err { color: #dc2626; font-size: 13px; margin-top: 8px; }
.muted { color: #9ca3af; font-size: 12px; margin-top: 8px; }
.links { margin-top: 10px; font-size: 13px; text-align: center; }
.sep { margin: 0 6px; color: #d1d5db; }
</style>
