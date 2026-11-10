import { defineStore } from 'pinia'

const TOKEN_KEY = 'lc_auth_token'
const USER_KEY = 'lc_auth_user'

function readUser() {
  try {
    return JSON.parse(localStorage.getItem(USER_KEY) || 'null')
  } catch {
    return null
  }
}

function messageOf(detail, fallback) {
  if (!detail) return fallback
  if (typeof detail === 'string') {
    if (detail === 'USER_NOT_FOUND') return '用户不存在，请检查用户名'
    if (detail === 'WRONG_PASSWORD') return '密码错误，请重试'
    if (detail.startsWith('WRONG_')) return '用户名或密码错误'
    return detail
  }
  return fallback
}

export const useAuthStore = defineStore('auth', {
  state: () => ({
    token: localStorage.getItem(TOKEN_KEY) || '',
    user: readUser(),
    username: '',
    authenticated: false,
    sessionChecked: false,
  }),

  actions: {
    persist(data) {
      if (data.access_token) {
        this.token = data.access_token
        localStorage.setItem(TOKEN_KEY, data.access_token)
      }
      if (data.user) {
        this.user = data.user
        this.username = data.user.username || this.username
        localStorage.setItem(USER_KEY, JSON.stringify(data.user))
      }
    },

    async login(username, password) {
      const resp = await fetch('/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      })
      const data = await resp.json().catch(() => ({}))
      if (!resp.ok) {
        throw new Error(messageOf(data.detail, '登录失败'))
      }
      this.persist(data)
      this.username = data.user?.username || username
      this.authenticated = true
      this.sessionChecked = true
      return data
    },

    async register(username, email, password) {
      const resp = await fetch('/auth/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, email, password }),
      })
      const data = await resp.json().catch(() => ({}))
      if (!resp.ok) {
        let msg = messageOf(data.detail, '注册失败')
        if (msg.includes('already exists') || msg.includes('已存在')) {
          msg = '用户名或邮箱已被注册'
        }
        throw new Error(msg)
      }
      return data
    },

    async ensureSession() {
      if (this.sessionChecked) return this.authenticated
      try {
        const resp = await fetch('/auth/session', { credentials: 'same-origin' })
        const data = await resp.json().catch(() => ({}))
        this.authenticated = !!data.authenticated
        this.username = data.username || this.username
      } catch {
        this.authenticated = false
      } finally {
        this.sessionChecked = true
      }
      return this.authenticated
    },

    logout() {
      this.token = ''
      this.user = null
      this.username = ''
      this.authenticated = false
      this.sessionChecked = true
      localStorage.removeItem(TOKEN_KEY)
      localStorage.removeItem(USER_KEY)
    },
  },
})
