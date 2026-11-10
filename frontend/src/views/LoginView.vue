<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()

const tab = ref('login')
const loginUser = ref('')
const loginPass = ref('')
const regUser = ref('')
const regEmail = ref('')
const regPass = ref('')
const regPass2 = ref('')

const showLoginPass = ref(false)
const showRegPass = ref(false)
const showRegPass2 = ref(false)

const loginLoading = ref(false)
const registerLoading = ref(false)

const errors = reactive({
  loginUser: '',
  loginPass: '',
  regUser: '',
  regEmail: '',
  regPass: '',
  regPass2: '',
})

const toast = reactive({ show: false, msg: '', type: 'err' })
let toastTimer = 0

const statusText = ref('检测服务连接中...')
const sessionUser = ref('')

function showToast(msg, type = 'err') {
  toast.msg = msg
  toast.type = type
  toast.show = true
  clearTimeout(toastTimer)
  toastTimer = setTimeout(() => {
    toast.show = false
  }, 3500)
}

function clearErrors() {
  Object.keys(errors).forEach((k) => {
    errors[k] = ''
  })
}

function switchTab(next) {
  tab.value = next
  clearErrors()
}

function goHost() {
  const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : ''
  router.replace(redirect || '/host')
}

async function doLogin() {
  clearErrors()
  let bad = false
  if (!loginUser.value.trim()) {
    errors.loginUser = '请输入用户名'
    bad = true
  }
  if (!loginPass.value) {
    errors.loginPass = '请输入密码'
    bad = true
  }
  if (bad) return

  loginLoading.value = true
  try {
    await auth.login(loginUser.value.trim(), loginPass.value)
    showToast('登录成功！欢迎回来 👋', 'ok')
    setTimeout(goHost, 500)
  } catch (e) {
    const msg = e.message || '登录失败'
    errors.loginUser = msg
    if (msg.includes('密码')) errors.loginPass = msg
    showToast(msg, 'err')
  } finally {
    loginLoading.value = false
  }
}

async function doRegister() {
  clearErrors()
  let bad = false
  if (!regUser.value || regUser.value.trim().length < 3) {
    errors.regUser = '用户名至少3个字符'
    bad = true
  }
  if (!regEmail.value || !regEmail.value.includes('@')) {
    errors.regEmail = '请输入有效的邮箱地址'
    bad = true
  }
  if (!regPass.value || regPass.value.length < 6) {
    errors.regPass = '密码至少6个字符'
    bad = true
  }
  if (regPass.value !== regPass2.value) {
    errors.regPass2 = '两次密码不一致'
    bad = true
  }
  if (bad) return

  registerLoading.value = true
  try {
    await auth.register(regUser.value.trim(), regEmail.value.trim(), regPass.value)
    showToast('注册成功！请登录 🎉', 'ok')
    tab.value = 'login'
    loginUser.value = regUser.value.trim()
  } catch (e) {
    const msg = e.message || '注册失败'
    if (msg.includes('已被注册')) errors.regUser = msg
    showToast(msg, 'err')
  } finally {
    registerLoading.value = false
  }
}

onMounted(async () => {
  try {
    const resp = await fetch('/auth/check')
    const data = await resp.json()
    if (data.first_run) {
      statusText.value = '🆕 首次使用，请注册一个管理员账号'
    } else {
      statusText.value = `✅ 服务正常 · 已有 ${data.user_count} 个用户`
    }
  } catch {
    statusText.value = '❌ 无法连接到服务，请确认服务已启动'
  }

  // 与旧版一致：是否已登录由后端 HttpOnly cookie 判定，localStorage 不作数。
  const ok = await auth.ensureSession()
  if (ok) sessionUser.value = auth.username || '已登录'
})
</script>

<template>
  <div class="login-page">
    <div class="toast-global" :class="[toast.type, { show: toast.show }]">
      {{ toast.msg }}
    </div>

    <div class="login-wrap">
      <div class="login-header">
        <div class="icon">📺</div>
        <h1>直播电商运营台</h1>
        <p>主播端登录 · Host Console</p>
      </div>

      <div class="login-body">
        <div class="login-tabs">
          <button
            class="login-tab"
            :class="{ active: tab === 'login' }"
            @click="switchTab('login')"
          >
            登录
          </button>
          <button
            class="login-tab"
            :class="{ active: tab === 'register' }"
            @click="switchTab('register')"
          >
            注册
          </button>
        </div>

        <form v-show="tab === 'login'" @submit.prevent="doLogin">
          <div class="form-group">
            <label>账号</label>
            <input
              v-model="loginUser"
              class="inp"
              :class="{ err: errors.loginUser }"
              placeholder="输入用户名"
              autocomplete="username"
            />
            <div class="err-msg" :class="{ show: errors.loginUser }">
              {{ errors.loginUser }}
            </div>
          </div>

          <div class="form-group">
            <label>密码</label>
            <div class="password-toggle">
              <input
                v-model="loginPass"
                class="inp"
                :class="{ err: errors.loginPass }"
                :type="showLoginPass ? 'text' : 'password'"
                placeholder="输入密码"
                autocomplete="current-password"
              />
              <span class="toggle-eye" @click="showLoginPass = !showLoginPass">
                {{ showLoginPass ? '🙈' : '👁️' }}
              </span>
            </div>
            <div class="err-msg" :class="{ show: errors.loginPass }">
              {{ errors.loginPass }}
            </div>
          </div>

          <button class="login-btn pri" type="submit" :disabled="loginLoading">
            {{ loginLoading ? '登录中...' : '登 录' }}
          </button>
          <div class="switch-tip">
            还没有账号？
            <a @click="switchTab('register')">立即注册</a>
          </div>
        </form>

        <form v-show="tab === 'register'" @submit.prevent="doRegister">
          <div class="form-group">
            <label>用户名</label>
            <input
              v-model="regUser"
              class="inp"
              :class="{ err: errors.regUser }"
              placeholder="至少3个字符"
              autocomplete="username"
            />
            <div class="err-msg" :class="{ show: errors.regUser }">
              {{ errors.regUser }}
            </div>
          </div>

          <div class="form-group">
            <label>邮箱</label>
            <input
              v-model="regEmail"
              class="inp"
              :class="{ err: errors.regEmail }"
              type="email"
              placeholder="your@email.com"
              autocomplete="email"
            />
            <div class="err-msg" :class="{ show: errors.regEmail }">
              {{ errors.regEmail }}
            </div>
          </div>

          <div class="form-group">
            <label>密码</label>
            <div class="password-toggle">
              <input
                v-model="regPass"
                class="inp"
                :class="{ err: errors.regPass }"
                :type="showRegPass ? 'text' : 'password'"
                placeholder="至少6个字符"
                autocomplete="new-password"
              />
              <span class="toggle-eye" @click="showRegPass = !showRegPass">
                {{ showRegPass ? '🙈' : '👁️' }}
              </span>
            </div>
            <div class="err-msg" :class="{ show: errors.regPass }">
              {{ errors.regPass }}
            </div>
          </div>

          <div class="form-group">
            <label>确认密码</label>
            <div class="password-toggle">
              <input
                v-model="regPass2"
                class="inp"
                :class="{ err: errors.regPass2 }"
                :type="showRegPass2 ? 'text' : 'password'"
                placeholder="再次输入密码"
              />
              <span class="toggle-eye" @click="showRegPass2 = !showRegPass2">
                {{ showRegPass2 ? '🙈' : '👁️' }}
              </span>
            </div>
            <div class="err-msg" :class="{ show: errors.regPass2 }">
              {{ errors.regPass2 }}
            </div>
          </div>

          <button class="login-btn pri" type="submit" :disabled="registerLoading">
            {{ registerLoading ? '注册中...' : '注 册' }}
          </button>
          <div class="switch-tip">
            已有账号？<a @click="switchTab('login')">去登录</a>
          </div>
        </form>
      </div>

      <div class="login-footer">
        <template v-if="sessionUser">
          已登录（{{ sessionUser }}） |
          <a @click="goHost">进入控制台 →</a>
        </template>
        <template v-else>{{ statusText }}</template>
      </div>
    </div>
  </div>
</template>

<style scoped>
.login-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
  background: linear-gradient(135deg, #0f172a 0%, #1e293b 50%, #0f172a 100%);
}

.login-wrap {
  width: 420px;
  max-width: 100%;
  overflow: hidden;
  background: #fff;
  border-radius: 16px;
  box-shadow: 0 25px 80px rgba(0, 0, 0, 0.3);
  animation: fadeIn 0.4s ease;
}

@keyframes fadeIn {
  from {
    opacity: 0;
    transform: translateY(20px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.login-header {
  padding: 28px 30px 22px;
  text-align: center;
  color: #fff;
  background: linear-gradient(135deg, #1e40af, #2563eb);
}

.login-header .icon {
  margin-bottom: 6px;
  font-size: 42px;
}

.login-header h1 {
  font-size: 20px;
  font-weight: 700;
  letter-spacing: 0.02em;
}

.login-header p {
  margin-top: 4px;
  font-size: 12px;
  opacity: 0.8;
}

.login-body {
  padding: 28px 30px 24px;
}

.login-tabs {
  display: flex;
  margin-bottom: 22px;
  padding: 3px;
  background: #f1f5f9;
  border-radius: 8px;
}

.login-tab {
  flex: 1;
  padding: 8px 0;
  font-size: 13px;
  font-weight: 600;
  color: #64748b;
  text-align: center;
  cursor: pointer;
  background: transparent;
  border: none;
  border-radius: 6px;
  transition: 0.2s;
}

.login-tab.active {
  color: #1e40af;
  background: #fff;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08);
}

.form-group {
  margin-bottom: 16px;
}

.form-group label {
  display: block;
  margin-bottom: 4px;
  font-size: 12px;
  font-weight: 600;
  color: #475569;
}

.form-group .inp {
  width: 100%;
  padding: 10px 12px;
  font-size: 13px;
  background: #fafbfc;
  border: 1.5px solid #e2e8f0;
  border-radius: 8px;
  outline: none;
  transition: 0.2s;
}

.form-group .inp:focus {
  background: #fff;
  border-color: #2563eb;
  box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.1);
}

.form-group .inp.err {
  background: #fef2f2;
  border-color: #ef4444;
}

.form-group .err-msg {
  display: none;
  margin-top: 4px;
  font-size: 11px;
  color: #ef4444;
}

.form-group .err-msg.show {
  display: block;
}

.login-btn {
  width: 100%;
  padding: 11px;
  margin-top: 4px;
  font-size: 14px;
  font-weight: 700;
  cursor: pointer;
  border: none;
  border-radius: 8px;
  transition: 0.2s;
}

.login-btn.pri {
  color: #fff;
  background: #2563eb;
}

.login-btn.pri:hover {
  background: #1d4ed8;
}

.login-btn.pri:disabled {
  background: #93c5fd;
  cursor: not-allowed;
}

.switch-tip {
  margin-top: 10px;
  font-size: 12px;
  color: #94a3b8;
  text-align: center;
}

.switch-tip a,
.login-footer a {
  color: #2563eb;
  cursor: pointer;
}

.login-footer {
  padding: 16px 30px;
  font-size: 11px;
  color: #94a3b8;
  text-align: center;
  border-top: 1px solid #f1f5f9;
}

.password-toggle {
  position: relative;
}

.password-toggle .toggle-eye {
  position: absolute;
  top: 50%;
  right: 10px;
  font-size: 16px;
  color: #94a3b8;
  cursor: pointer;
  user-select: none;
  transform: translateY(-50%);
}

.toast-global {
  position: fixed;
  top: 24px;
  left: 50%;
  z-index: 2000;
  display: none;
  padding: 10px 22px;
  font-size: 13px;
  font-weight: 500;
  color: #fff;
  background: #ef4444;
  border-radius: 8px;
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.2);
  transform: translateX(-50%);
}

.toast-global.ok {
  background: #10b981;
}

.toast-global.show {
  display: block;
  animation: slideDown 0.3s ease;
}

@keyframes slideDown {
  from {
    opacity: 0;
    transform: translate(-50%, -20px);
  }
  to {
    opacity: 1;
    transform: translate(-50%, 0);
  }
}
</style>
