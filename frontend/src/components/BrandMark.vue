<template>
  <span class="brand-mark" aria-label="Horizon XL">
    <span class="brand-mark__hero">H</span><span class="brand-mark__body">orizon</span>
    <span class="brand-mark__xl">XL</span>
  </span>
</template>

<style scoped>
.brand-mark {
  display: inline-flex;
  align-items: baseline;
  gap: 0.16em;
  font-family: var(--hx-font-display);
  font-weight: 800;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  line-height: 1.18;
  padding: 0.08em 0.08em 0.1em 0;
  overflow: visible;
  white-space: nowrap;
}

.brand-mark__hero,
.brand-mark__xl {
  display: inline-block;
  font-size: 1em;
  letter-spacing: 0.1em;
}

.brand-mark__body {
  display: inline-block;
  font-size: 0.68em;
  letter-spacing: 0.28em;
  transform: translateY(-0.07em);
}

.brand-mark__xl {
  padding-right: 0.08em;
}
</style>
