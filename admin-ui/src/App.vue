<template>
  <router-view v-if="$route.meta.public" />
  <el-container v-else class="layout">
    <el-aside width="200px" class="aside">
      <div class="logo">SignalWatch</div>
      <el-menu :default-active="$route.path" router class="menu">
        <el-menu-item v-for="m in menus" :key="m.path" :index="m.path">
          <el-icon><component :is="m.icon" /></el-icon>
          <span>{{ m.label }}</span>
        </el-menu-item>
      </el-menu>
    </el-aside>
    <el-container>
      <el-header class="header">
        <span class="flex1"></span>
        <el-dropdown @command="onCmd">
          <span class="user">
            {{ auth.user?.username || '…' }}
            <el-icon><ArrowDown /></el-icon>
          </span>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="me">我的</el-dropdown-item>
              <el-dropdown-item command="logout" divided>退出登录</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </el-header>
      <el-main class="main">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import { ArrowDown, Odometer, HomeFilled, Star, Document, Folder,
         Timer, TrendCharts, User, Connection, Filter, Setting, List, UserFilled } from '@element-plus/icons-vue'
import { auth } from './stores/auth'
import { useRouter } from 'vue-router'

const router = useRouter()

const menus = [
  { path: '/overview', label: '概览', icon: Odometer },
  { path: '/home', label: '首页', icon: HomeFilled },
  { path: '/starred', label: '收藏', icon: Star },
  { path: '/items', label: '条目', icon: Document },
  { path: '/categories', label: '分类', icon: Folder },
  { path: '/jobs', label: '任务', icon: Timer },
  { path: '/report', label: '报表', icon: TrendCharts },
  { path: '/users', label: '用户', icon: User },
  { path: '/sources', label: '数据源', icon: Connection },
  { path: '/rules', label: '订阅词', icon: Filter },
  { path: '/settings', label: '设置', icon: Setting },
  { path: '/audit', label: '审计', icon: List },
  { path: '/me', label: '我的', icon: UserFilled },
]

async function onCmd(cmd) {
  if (cmd === 'me') {
    router.push('/me')
  } else if (cmd === 'logout') {
    await auth.logout()
    router.push('/login')
  }
}
</script>

<style scoped>
.layout { height: 100vh; }
.aside { background: #1f2937; color: #fff; }
.logo { height: 60px; line-height: 60px; text-align: center; font-weight: 700; font-size: 18px; color: #fff; }
.menu { border-right: none; background: transparent; --el-menu-text-color: #cbd5e1; --el-menu-hover-bg-color: #374151; --el-menu-active-color: #fff; }
.menu :deep(.el-menu-item.is-active) { background: #2563eb; }
.header { display: flex; align-items: center; border-bottom: 1px solid #e5e7eb; background: #fff; }
.flex1 { flex: 1; }
.user { display: inline-flex; align-items: center; gap: 4px; cursor: pointer; color: #374151; }
.main { background: #f3f4f6; padding: 16px; }
</style>
