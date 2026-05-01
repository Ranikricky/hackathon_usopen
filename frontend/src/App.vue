<template>
  <router-view />
</template>

<script setup>
// 使用 Vue Router 来管理页面
</script>

<style>
/* Global reset and Horizon XL visual system */
* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

:root {
  --hx-bg: #f6f4ef;
  --hx-bg-soft: #fbfaf7;
  --hx-panel: rgba(255, 255, 255, 0.78);
  --hx-panel-strong: rgba(255, 255, 255, 0.94);
  --hx-ink: #111316;
  --hx-muted: #777b84;
  --hx-faint: #dcd7cc;
  --hx-line: rgba(17, 19, 22, 0.1);
  --hx-line-strong: rgba(17, 19, 22, 0.18);
  --hx-accent: #176b87;
  --hx-accent-2: #c08b5c;
  --hx-good: #1e7f5c;
  --hx-warn: #b76e22;
  --hx-danger: #b54646;
  --hx-radius-xl: 28px;
  --hx-radius-lg: 20px;
  --hx-radius-md: 14px;
  --hx-shadow: 0 24px 80px rgba(34, 31, 25, 0.12);
  --hx-shadow-soft: 0 14px 44px rgba(34, 31, 25, 0.08);
  --hx-font-display: 'Space Grotesk', 'Noto Sans SC', system-ui, sans-serif;
  --hx-font-body: 'Space Grotesk', 'Noto Sans SC', system-ui, sans-serif;
  --hx-font-mono: 'JetBrains Mono', monospace;
}

html {
  background: var(--hx-bg);
}

body {
  min-height: 100vh;
  background:
    radial-gradient(circle at 10% -10%, rgba(23, 107, 135, 0.13), transparent 34rem),
    radial-gradient(circle at 92% 6%, rgba(192, 139, 92, 0.15), transparent 30rem),
    linear-gradient(180deg, #fbfaf7 0%, #f4f1ea 100%);
}

#app {
  font-family: var(--hx-font-body);
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  color: var(--hx-ink);
  background: transparent;
}

::-webkit-scrollbar {
  width: 10px;
  height: 10px;
}

::-webkit-scrollbar-track {
  background: rgba(17, 19, 22, 0.04);
}

::-webkit-scrollbar-thumb {
  background: rgba(17, 19, 22, 0.2);
  border: 3px solid transparent;
  border-radius: 999px;
  background-clip: padding-box;
}

::-webkit-scrollbar-thumb:hover {
  background: rgba(17, 19, 22, 0.34);
  border: 3px solid transparent;
  background-clip: padding-box;
}

button {
  font-family: inherit;
}

button,
a,
textarea,
input {
  -webkit-tap-highlight-color: transparent;
}

::selection {
  background: rgba(23, 107, 135, 0.18);
}
</style>
