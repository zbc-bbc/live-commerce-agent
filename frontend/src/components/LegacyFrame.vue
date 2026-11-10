<script setup>
import { ref } from 'vue'

defineProps({
  src: { type: String, required: true },
  title: { type: String, default: '业务页面' },
})

const loading = ref(true)
</script>

<template>
  <div class="legacy-frame">
    <div v-if="loading" class="loading">
      <div class="spinner"></div>
      <div class="loading-text">正在加载{{ title }}…</div>
    </div>
    <iframe
      :src="src"
      :title="title"
      allow="camera; microphone; autoplay; clipboard-write"
      @load="loading = false"
    ></iframe>
  </div>
</template>

<style scoped>
.legacy-frame {
  position: relative;
  width: 100%;
  height: 100vh;
  height: 100dvh;
  overflow: hidden;
  background: #f3f4f6;
}

.legacy-frame iframe {
  display: block;
  width: 100%;
  height: 100%;
  border: 0;
}

.loading {
  position: absolute;
  inset: 0;
  z-index: 2;
  display: flex;
  flex-direction: column;
  gap: 12px;
  align-items: center;
  justify-content: center;
  color: #64748b;
  background: #f3f4f6;
}

.spinner {
  width: 28px;
  height: 28px;
  border: 3px solid #dbeafe;
  border-top-color: #2563eb;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

.loading-text {
  font-size: 13px;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
