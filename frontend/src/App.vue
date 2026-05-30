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
  --hx-bg: #f3eadb;
  --hx-bg-soft: #fffaf0;
  --hx-panel: rgba(255, 250, 240, 0.86);
  --hx-panel-strong: rgba(255, 252, 245, 0.96);
  --hx-ink: #17130d;
  --hx-muted: #776d5e;
  --hx-faint: #d9cbb5;
  --hx-line: rgba(42, 32, 21, 0.16);
  --hx-line-strong: rgba(42, 32, 21, 0.42);
  --hx-accent: #7f1d1d;
  --hx-accent-2: #9c6a2f;
  --hx-good: #276449;
  --hx-warn: #9b5a18;
  --hx-danger: #9f2d2d;
  --hx-radius-xl: 6px;
  --hx-radius-lg: 4px;
  --hx-radius-md: 3px;
  --hx-shadow: 0 18px 44px rgba(45, 32, 18, 0.16);
  --hx-shadow-soft: 0 10px 24px rgba(45, 32, 18, 0.1);
  --hx-font-display: 'Georgia', 'Times New Roman', 'Noto Serif', serif;
  --hx-font-body: 'Georgia', 'Times New Roman', 'Noto Serif', serif;
  --hx-font-mono: 'JetBrains Mono', monospace;
}

html {
  background: var(--hx-bg);
}

body {
  min-height: 100vh;
  background:
    linear-gradient(rgba(69, 49, 28, 0.035) 1px, transparent 1px),
    linear-gradient(90deg, rgba(69, 49, 28, 0.025) 1px, transparent 1px),
    radial-gradient(circle at 12% -8%, rgba(127, 29, 29, 0.08), transparent 34rem),
    radial-gradient(circle at 92% 6%, rgba(156, 106, 47, 0.1), transparent 30rem),
    linear-gradient(180deg, #fffaf0 0%, #f1e4cf 100%);
  background-size: 28px 28px, 28px 28px, auto, auto, auto;
}

body::before {
  content: '';
  position: fixed;
  inset: 0;
  pointer-events: none;
  opacity: 0.18;
  mix-blend-mode: multiply;
  background-image:
    repeating-linear-gradient(0deg, rgba(42, 32, 21, 0.06) 0 1px, transparent 1px 3px),
    radial-gradient(circle at 20% 10%, rgba(42, 32, 21, 0.08), transparent 20rem);
  z-index: 0;
}

#app {
  position: relative;
  z-index: 1;
  font-family: var(--hx-font-body);
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  color: var(--hx-ink);
  background: transparent;
}

.navbar,
.app-header,
.process-page .navbar {
  border-top: 1px solid var(--hx-line-strong) !important;
  border-bottom: 4px double var(--hx-line-strong) !important;
  background:
    linear-gradient(90deg, rgba(255,250,240,0.96), rgba(249,238,219,0.92)) !important;
  box-shadow: none !important;
  backdrop-filter: none !important;
}

.main-view,
.process-page,
.home-container,
.workbench-panel {
  background:
    linear-gradient(rgba(69, 49, 28, 0.035) 1px, transparent 1px),
    linear-gradient(90deg, rgba(69, 49, 28, 0.025) 1px, transparent 1px),
    var(--hx-bg) !important;
  background-size: 28px 28px, 28px 28px, auto !important;
}

.step-card,
.console-box,
.metrics-row .metric-card,
.steps-container,
.panel-wrapper,
.graph-container,
.left-panel,
.right-panel,
.report-card,
.modal-content {
  border-radius: var(--hx-radius-lg) !important;
  border: 1px solid var(--hx-line-strong) !important;
  background: var(--hx-panel) !important;
  box-shadow: 3px 3px 0 rgba(42, 32, 21, 0.08) !important;
}

.main-title,
.section-title,
.step-title,
.card-title,
.header-title,
.console-label,
.step-name {
  font-family: var(--hx-font-display) !important;
  letter-spacing: -0.015em;
}

.main-title,
.section-title {
  font-weight: 700 !important;
  text-shadow: 0 1px 0 rgba(255, 250, 240, 0.8);
}

.view-switcher,
.switch-btn,
.start-engine-btn,
.action-btn,
.retry-btn,
.badge,
.entity-tag,
.relation-tag {
  border-radius: 2px !important;
}

.start-engine-btn,
.switch-btn.active {
  background: var(--hx-ink) !important;
  color: var(--hx-bg-soft) !important;
  box-shadow: none !important;
}

.switch-btn:not(.active),
.retry-btn {
  background: transparent !important;
  border: 1px solid var(--hx-line-strong) !important;
}

.tag-label,
.api-note,
.console-meta,
.step-num,
.panel-header,
.status-indicator {
  font-family: var(--hx-font-mono) !important;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.brand-mark {
  font-family: var(--hx-font-display) !important;
  color: var(--hx-ink) !important;
}

.badge {
  border: 1px solid var(--hx-line-strong) !important;
  background: rgba(255, 250, 240, 0.72) !important;
  color: var(--hx-ink) !important;
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
