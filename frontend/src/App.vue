<script setup lang="ts">
/**
 * Application shell.
 *
 * T00 only needs a project that type-checks and builds. The real workspace
 * layout (history sidebar, chat and tool trace, approval panel) is implemented
 * in T14 against the SSE contract in docs/plan/contracts/chat.md.
 */
const shellName = '采购助手工作台'
</script>

<template>
  <main class="shell">
    <h1>{{ shellName }}</h1>
    <p class="hint">工程骨架已就绪；对话与审批界面在 T14 实现。</p>
  </main>
</template>

<style scoped>
.shell {
  display: flex;
  min-height: 100vh;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
}

h1 {
  margin: 0;
  font-size: 1.5rem;
}

.hint {
  margin: 0;
  color: #5b6473;
}
</style>
