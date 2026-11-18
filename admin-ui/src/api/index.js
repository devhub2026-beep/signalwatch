import axios from 'axios'

const http = axios.create({
  baseURL: '/api',
  withCredentials: true,
  timeout: 20000,
})

function getCookie(name) {
  const m = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'))
  return m ? decodeURIComponent(m[1]) : ''
}

// 写请求自动带双提交 CSRF 头（与后端 _check_csrf 匹配）
http.interceptors.request.use((cfg) => {
  const csrf = getCookie('sw_csrf')
  const method = (cfg.method || 'get').toLowerCase()
  if (csrf && !['get', 'head', 'options'].includes(method)) {
    cfg.headers['x-csrf-token'] = csrf
  }
  return cfg
})

// 401 跳登录；其它错误统一 toast 文案
http.interceptors.response.use(
  (r) => r,
  (err) => {
    const status = err.response?.status
    if (status === 401 && !location.hash.includes('/login')) {
      location.hash = '#/login'
    }
    const detail = err.response?.data?.detail || err.message || '请求失败'
    err.userMessage = typeof detail === 'string' ? detail : '请求失败'
    return Promise.reject(err)
  },
)

export default http
