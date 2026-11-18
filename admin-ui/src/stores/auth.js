import { reactive } from 'vue'
import http from '../api'

export const auth = reactive({
  user: null,
  loaded: false,

  async load() {
    try {
      const r = await http.get('/auth/me')
      this.user = r.data.user
    } catch {
      this.user = null
    } finally {
      this.loaded = true
    }
  },

  async login(username, password) {
    const r = await http.post('/auth/login', { username, password })
    this.user = r.data.user
    return r.data
  },

  /** 免密进入：只在服务端开了 SIGNALWATCH_AUTO_LOGIN 时可用，个人单机部署用。 */
  async autoLogin() {
    const r = await http.post('/auth/auto')
    this.user = r.data.user
    return r.data
  },

  async logout() {
    try {
      await http.post('/auth/logout')
    } catch {
      /* 忽略退出失败 */
    }
    this.user = null
  },
})
