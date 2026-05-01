<template>
  <span class="brand-mark" aria-label="Horizon XL">
    <span class="brand-mark__hero">H</span><span class="brand-mark__body">orizon</span>
    <span class="brand-mark__xl">XL</span>
  </span>
</template>

<style scoped>
.brand-mark {
  display: inline-grid;
  grid-auto-flow: column;
  grid-auto-columns: max-content;
  align-items: baseline;
  column-gap: 0.14em;
  font-family: var(--hx-font-display);
  font-weight: 800;
  letter-spacing: 0;
  text-transform: uppercase;
  line-height: 1.22;
  inline-size: max-content;
  max-inline-size: none;
  padding: 0.12em 0.42em 0.14em 0;
  box-sizing: content-box;
  overflow: visible;
  white-space: nowrap;
  contain: none;
}

.brand-mark__hero,
.brand-mark__xl {
  display: inline-block;
  flex: none;
  font-size: 1em;
  letter-spacing: 0.06em;
  line-height: 1.05;
}

.brand-mark__body {
  display: inline-block;
  flex: none;
  font-size: 0.68em;
  letter-spacing: 0.24em;
  line-height: 1.05;
  transform: translateY(-0.07em);
}

.brand-mark__xl {
  margin-left: 0.04em;
  padding-right: 0.18em;
}
</style>
