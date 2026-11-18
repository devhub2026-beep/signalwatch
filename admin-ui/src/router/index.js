import { createRouter, createWebHashHistory } from 'vue-router'
import { auth } from '../stores/auth'

const routes = [
  { path: '/login', component: () => import('../views/Login.vue'), meta: { public: true, title: '登录' } },
  { path: '/', redirect: '/overview' },
  { path: '/overview', component: () => import('../views/Overview.vue'), meta: { title: '概览' } },
  { path: '/home', component: () => import('../views/HomeFeed.vue'), meta: { title: '首页' } },
  { path: '/starred', component: () => import('../views/Starred.vue'), meta: { title: '收藏' } },
  { path: '/items', component: () => import('../views/Items.vue'), meta: { title: '条目' } },
  { path: '/categories', component: () => import('../views/Categories.vue'), meta: { title: '分类' } },
  { path: '/jobs', component: () => import('../views/Jobs.vue'), meta: { title: '任务' } },
  { path: '/report', component: () => import('../views/Report.vue'), meta: { title: '报表' } },
  { path: '/users', component: () => import('../views/Users.vue'), meta: { title: '用户' } },
  { path: '/sources', component: () => import('../views/Sources.vue'), meta: { title: '数据源' } },
  { path: '/rules', component: () => import('../views/Rules.vue'), meta: { title: '订阅词' } },
  { path: '/settings', component: () => import('../views/Settings.vue'), meta: { title: '设置' } },
  { path: '/audit', component: () => import('../views/Audit.vue'), meta: { title: '审计' } },
  { path: '/me', component: () => import('../views/Me.vue'), meta: { title: '我的' } },
]

const router = createRouter({
  history: createWebHashHistory(),
  routes,
})

router.beforeEach(async (to) => {
  if (to.meta.public) return true
  if (!auth.loaded) await auth.load()
  if (!auth.user) return { path: '/login', query: { redirect: to.fullPath } }
  return true
})

export default router
