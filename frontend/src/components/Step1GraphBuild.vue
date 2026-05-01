<template>
  <div class="workbench-panel">
    <div class="scroll-container">
      <!-- Step 01: Ontology -->
      <div class="step-card" :class="{ 'active': currentPhase === 0, 'completed': currentPhase > 0 }">
        <div class="card-header">
          <div class="step-info">
            <span class="step-num">01</span>
            <span class="step-title">{{ $t('step1.ontologyGeneration') }}</span>
          </div>
          <div class="step-status">
            <span v-if="currentPhase > 0" class="badge success">{{ $t('step1.ontologyCompleted') }}</span>
            <span v-else-if="error" class="badge danger">FAILED</span>
            <span v-else-if="currentPhase === 0" class="badge processing">{{ $t('step1.ontologyGenerating') }}</span>
            <span v-else class="badge pending">{{ $t('step1.ontologyPending') }}</span>
          </div>
        </div>
        
        <div class="card-content">
          <p class="api-note">POST /api/graph/ontology/generate</p>
          <p class="description">
            {{ $t('step1.ontologyDesc') }}
          </p>

          <div v-if="error" class="error-callout">
            <div>
              <span class="error-title">Generation failed</span>
              <p class="error-message">{{ error }}</p>
            </div>
            <button class="retry-btn" @click="$emit('retry')">Retry</button>
          </div>

          <!-- Loading / Progress -->
          <div v-if="!error && currentPhase === 0 && ontologyProgress" class="progress-section">
            <div class="spinner-sm"></div>
            <span>{{ ontologyProgress.message || $t('step1.analyzingDocs') }}</span>
          </div>

          <div v-if="!error && currentPhase === 0" class="focus-theatre ontology-theatre">
            <div class="focus-copy">
              <span class="focus-kicker">Live focus</span>
              <strong>Finding the simulation actors</strong>
              <p>The system is reading the prompt, detecting the domain, and turning the context into actor categories.</p>
            </div>
            <div class="phrase-stream">
              <span
                v-for="(phrase, idx) in ontologyPhraseList"
                :key="phrase"
                class="phrase-pill"
                :style="{ '--delay': `${idx * 120}ms` }"
              >
                {{ phrase }}
              </span>
            </div>
          </div>

          <!-- Detail Overlay -->
          <div v-if="selectedOntologyItem" class="ontology-detail-overlay">
            <div class="detail-header">
               <div class="detail-title-group">
                  <span class="detail-type-badge">{{ selectedOntologyItem.itemType === 'entity' ? 'ENTITY' : 'RELATION' }}</span>
                  <span class="detail-name">{{ selectedOntologyItem.name }}</span>
               </div>
               <button class="close-btn" @click="selectedOntologyItem = null">×</button>
            </div>
            <div class="detail-body">
               <div class="detail-desc">{{ selectedOntologyItem.description }}</div>
               
               <!-- Attributes -->
               <div class="detail-section" v-if="selectedOntologyItem.attributes?.length">
                  <span class="section-label">ATTRIBUTES</span>
                  <div class="attr-list">
                     <div v-for="attr in selectedOntologyItem.attributes" :key="attr.name" class="attr-item">
                        <span class="attr-name">{{ attr.name }}</span>
                        <span class="attr-type">({{ attr.type }})</span>
                        <span class="attr-desc">{{ attr.description }}</span>
                     </div>
                  </div>
               </div>

               <!-- Examples (Entity) -->
               <div class="detail-section" v-if="selectedOntologyItem.examples?.length">
                  <span class="section-label">EXAMPLES</span>
                  <div class="example-list">
                     <span v-for="ex in selectedOntologyItem.examples" :key="ex" class="example-tag">{{ ex }}</span>
                  </div>
               </div>

               <!-- Source/Target (Relation) -->
               <div class="detail-section" v-if="selectedOntologyItem.source_targets?.length">
                  <span class="section-label">CONNECTIONS</span>
                  <div class="conn-list">
                     <div v-for="(conn, idx) in selectedOntologyItem.source_targets" :key="idx" class="conn-item">
                        <span class="conn-node">{{ conn.source }}</span>
                        <span class="conn-arrow">→</span>
                        <span class="conn-node">{{ conn.target }}</span>
                     </div>
                  </div>
               </div>
            </div>
          </div>

          <!-- Generated Entity Tags -->
          <div v-if="projectData?.ontology?.entity_types" class="tags-container" :class="{ 'dimmed': selectedOntologyItem }">
            <span class="tag-label">GENERATED ENTITY TYPES</span>
            <div class="tags-list">
              <span 
                v-for="entity in projectData.ontology.entity_types" 
                :key="entity.name" 
                class="entity-tag clickable"
                :style="{ '--delay': `${projectData.ontology.entity_types.indexOf(entity) * 70}ms` }"
                @click="selectOntologyItem(entity, 'entity')"
              >
                {{ entity.name }}
              </span>
            </div>
          </div>

          <!-- Generated Relation Tags -->
          <div v-if="projectData?.ontology?.edge_types" class="tags-container" :class="{ 'dimmed': selectedOntologyItem }">
            <span class="tag-label">GENERATED RELATION TYPES</span>
            <div class="tags-list">
              <span 
                v-for="rel in projectData.ontology.edge_types" 
                :key="rel.name" 
                class="entity-tag clickable"
                :style="{ '--delay': `${projectData.ontology.edge_types.indexOf(rel) * 70}ms` }"
                @click="selectOntologyItem(rel, 'relation')"
              >
                {{ rel.name }}
              </span>
            </div>
          </div>
        </div>
      </div>

      <!-- Step 02: Graph Build -->
      <div class="step-card" :class="{ 'active': currentPhase === 1, 'completed': currentPhase > 1 }">
        <div class="card-header">
          <div class="step-info">
            <span class="step-num">02</span>
            <span class="step-title">{{ $t('step1.graphRagBuild') }}</span>
          </div>
          <div class="step-status">
            <span v-if="currentPhase > 1" class="badge success">{{ $t('step1.ontologyCompleted') }}</span>
            <span v-else-if="error && currentPhase === 1" class="badge danger">FAILED</span>
            <span v-else-if="currentPhase === 1" class="badge processing">{{ buildProgress?.progress || 0 }}%</span>
            <span v-else class="badge pending">{{ $t('step1.ontologyPending') }}</span>
          </div>
        </div>

        <div class="card-content">
          <p class="api-note">POST /api/graph/build</p>
          <p class="description">
            {{ $t('step1.graphRagDesc') }}
          </p>

          <div v-if="currentPhase === 1" class="focus-theatre graph-theatre">
            <div class="focus-copy">
              <span class="focus-kicker">Connection build</span>
              <strong>Wiring actors into graph memory</strong>
              <p>Nodes become participants, relationships become evidence paths, and the graph panel updates as the memory forms.</p>
            </div>
            <div class="connection-mini" aria-label="Graph connection preview">
              <div
                v-for="(node, idx) in connectionPreviewNodes"
                :key="node"
                class="mini-node"
                :class="`mini-node-${idx}`"
              >
                {{ node }}
              </div>
              <span class="mini-link link-a"></span>
              <span class="mini-link link-b"></span>
              <span class="mini-link link-c"></span>
            </div>
          </div>
          
          <!-- Stats Cards -->
          <div class="stats-grid">
            <div class="stat-card">
              <span class="stat-value">{{ graphStats.nodes }}</span>
              <span class="stat-label">{{ $t('step1.entityNodes') }}</span>
            </div>
            <div class="stat-card">
              <span class="stat-value">{{ graphStats.edges }}</span>
              <span class="stat-label">{{ $t('step1.relationEdges') }}</span>
            </div>
            <div class="stat-card">
              <span class="stat-value">{{ graphStats.types }}</span>
              <span class="stat-label">{{ $t('step1.schemaTypes') }}</span>
            </div>
          </div>
        </div>
      </div>

      <!-- Step 03: Complete -->
      <div class="step-card" :class="{ 'active': currentPhase === 2, 'completed': currentPhase >= 2 }">
        <div class="card-header">
          <div class="step-info">
            <span class="step-num">03</span>
            <span class="step-title">{{ $t('step1.buildComplete') }}</span>
          </div>
          <div class="step-status">
            <span v-if="currentPhase >= 2" class="badge accent">{{ $t('step1.inProgress') }}</span>
          </div>
        </div>
        
        <div class="card-content">
          <p class="api-note">POST /api/simulation/create</p>
          <p class="description">{{ $t('step1.buildCompleteDesc') }}</p>
          <div v-if="currentPhase >= 2" class="focus-theatre complete-theatre">
            <div class="focus-copy">
              <span class="focus-kicker">Ready</span>
              <strong>Graph memory established</strong>
              <p>The next step creates the simulation environment and turns graph nodes into participant agents.</p>
            </div>
          </div>
          <button 
            class="action-btn" 
            :disabled="currentPhase < 2 || creatingSimulation"
            @click="handleEnterEnvSetup"
          >
            <span v-if="creatingSimulation" class="spinner-sm"></span>
            {{ creatingSimulation ? $t('step1.creating') : $t('step1.enterEnvSetup') + ' ➝' }}
          </button>
        </div>
      </div>
    </div>

    <!-- Bottom Info / Logs -->
    <div class="system-logs">
      <div class="log-header">
        <span class="log-title">SYSTEM DASHBOARD</span>
        <span class="log-id">{{ projectData?.project_id || 'NO_PROJECT' }}</span>
      </div>
      <div class="log-content" ref="logContent">
        <div class="log-line" v-for="(log, idx) in systemLogs" :key="idx">
          <span class="log-time">{{ log.time }}</span>
          <span class="log-msg">{{ log.msg }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { createSimulation } from '../api/simulation'

const router = useRouter()
const { t } = useI18n()

const props = defineProps({
  currentPhase: { type: Number, default: 0 },
  projectData: Object,
  ontologyProgress: Object,
  buildProgress: Object,
  graphData: Object,
  error: { type: String, default: '' },
  systemLogs: { type: Array, default: () => [] }
})

defineEmits(['next-step', 'retry'])

const selectedOntologyItem = ref(null)
const logContent = ref(null)
const creatingSimulation = ref(false)

const ontologyPhraseList = computed(() => {
  const entityTypes = props.projectData?.ontology?.entity_types || []
  if (entityTypes.length) {
    return entityTypes.slice(0, 8).map((entity) => {
      const name = entity?.name || 'Actor'
      return `Actor type: ${name}`
    })
  }

  return [
    'Reading prompt and source context',
    'Detecting simulation domain',
    'Separating actor categories from agents',
    'Checking files, URLs, and web research',
    'Drafting ontology schema',
    'Preparing graph handoff',
  ]
})

const connectionPreviewNodes = computed(() => {
  const entityTypes = props.projectData?.ontology?.entity_types || []
  const source = entityTypes.length ? entityTypes.map((entity) => entity?.name || 'Actor') : ['Prompt', 'Actors', 'Graph']
  return source.slice(0, 3).map((name) => {
    const words = String(name).replace(/([a-z])([A-Z])/g, '$1 $2').split(/\s+/).filter(Boolean)
    const initials = words.map((word) => word[0]).join('')
    return (initials || name).slice(0, 3).toUpperCase()
  })
})

// Create a simulation and move to environment setup.
const handleEnterEnvSetup = async () => {
  if (!props.projectData?.project_id || !props.projectData?.graph_id) {
    console.error('Missing project or graph information')
    return
  }
  
  creatingSimulation.value = true
  
  try {
    const res = await createSimulation({
      project_id: props.projectData.project_id,
      graph_id: props.projectData.graph_id,
      enable_twitter: true,
      enable_reddit: true
    })
    
    if (res.success && res.data?.simulation_id) {
      // Navigate to the simulation page.
      router.push({
        name: 'Simulation',
        params: { simulationId: res.data.simulation_id }
      })
    } else {
      console.error('Failed to create simulation:', res.error)
      alert(t('step1.createSimulationFailed', { error: res.error || t('common.unknownError') }))
    }
  } catch (err) {
    console.error('Simulation creation exception:', err)
    alert(t('step1.createSimulationException', { error: err.message }))
  } finally {
    creatingSimulation.value = false
  }
}

const selectOntologyItem = (item, type) => {
  selectedOntologyItem.value = { ...item, itemType: type }
}

const graphStats = computed(() => {
  const nodes = props.graphData?.node_count || props.graphData?.nodes?.length || 0
  const edges = props.graphData?.edge_count || props.graphData?.edges?.length || 0
  const types = props.projectData?.ontology?.entity_types?.length || 0
  return { nodes, edges, types }
})

const formatDate = (dateStr) => {
  if (!dateStr) return '--:--:--'
  const d = new Date(dateStr)
  return d.toLocaleTimeString('en-US', { hour12: false }) + '.' + d.getMilliseconds()
}

watch(() => props.systemLogs.length, () => {
  nextTick(() => {
    if (logContent.value) {
      logContent.value.scrollTop = logContent.value.scrollHeight
    }
  })
})
</script>

<style scoped>
.workbench-panel {
  height: 100%;
  background:
    radial-gradient(circle at 100% 0%, rgba(192,139,92,0.1), transparent 24rem),
    var(--hx-bg);
  display: flex;
  flex-direction: column;
  position: relative;
  overflow: hidden;
}

.scroll-container {
  flex: 1;
  overflow-y: auto;
  padding: 28px;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.step-card {
  background: rgba(255,255,255,0.72);
  border-radius: var(--hx-radius-lg);
  padding: 22px;
  box-shadow: 0 16px 44px rgba(34,31,25,0.06);
  border: 1px solid var(--hx-line);
  backdrop-filter: blur(16px);
  transition: transform 0.38s cubic-bezier(0.2, 0.8, 0.2, 1), opacity 0.3s ease, box-shadow 0.3s ease, border-color 0.3s ease;
  position: relative; /* For absolute overlay */
  transform-origin: center top;
  will-change: transform;
}

.step-card:not(.active):not(.completed) {
  opacity: 0.62;
  transform: scale(0.985);
}

.step-card.active {
  border-color: rgba(23,107,135,0.36);
  box-shadow: 0 24px 70px rgba(23,107,135,0.16);
  transform: scale(1.018);
  z-index: 3;
}

.step-card.active::before {
  content: '';
  position: absolute;
  inset: -1px;
  border-radius: inherit;
  pointer-events: none;
  background: linear-gradient(135deg, rgba(23,107,135,0.22), transparent 34%, rgba(192,139,92,0.18));
  opacity: 0.22;
  animation: focusGlow 2.8s ease-in-out infinite;
}

.step-card.completed {
  transform: scale(0.998);
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}

.step-info {
  display: flex;
  align-items: center;
  gap: 12px;
}

.step-num {
  font-family: var(--hx-font-mono);
  font-size: 20px;
  font-weight: 700;
  color: rgba(17,19,22,0.18);
}

.step-card.active .step-num,
.step-card.completed .step-num {
  color: var(--hx-accent);
}

.step-title {
  font-weight: 600;
  font-size: 14px;
  color: var(--hx-ink);
  letter-spacing: -0.01em;
}

.badge {
  font-size: 10px;
  padding: 5px 9px;
  border-radius: 999px;
  font-weight: 600;
  text-transform: uppercase;
}

.badge.success { background: rgba(30,127,92,0.1); color: var(--hx-good); }
.badge.processing { background: rgba(183,110,34,0.12); color: var(--hx-warn); }
.badge.accent { background: rgba(23,107,135,0.12); color: var(--hx-accent); }
.badge.pending { background: rgba(17,19,22,0.06); color: var(--hx-muted); }
.badge.danger { background: rgba(181,70,70,0.1); color: var(--hx-danger); }

.api-note {
  font-family: var(--hx-font-mono);
  font-size: 10px;
  color: var(--hx-muted);
  margin-bottom: 8px;
}

.description {
  font-size: 12px;
  color: var(--hx-muted);
  line-height: 1.5;
  margin-bottom: 16px;
}

.focus-theatre {
  margin: 14px 0 16px;
  padding: 14px;
  border: 1px solid rgba(23,107,135,0.14);
  border-radius: var(--hx-radius-md);
  background:
    linear-gradient(135deg, rgba(255,255,255,0.86), rgba(239,248,247,0.74)),
    radial-gradient(circle at 82% 20%, rgba(192,139,92,0.14), transparent 16rem);
  box-shadow: inset 0 0 0 1px rgba(255,255,255,0.45);
  display: grid;
  gap: 12px;
  overflow: hidden;
}

.focus-copy {
  display: grid;
  gap: 4px;
}

.focus-kicker {
  font-family: var(--hx-font-mono);
  font-size: 9px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--hx-accent);
  font-weight: 700;
}

.focus-copy strong {
  color: var(--hx-ink);
  font-size: 13px;
}

.focus-copy p {
  margin: 0;
  color: var(--hx-muted);
  font-size: 12px;
  line-height: 1.45;
}

.phrase-stream {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.phrase-pill {
  display: inline-flex;
  align-items: center;
  min-height: 28px;
  padding: 6px 10px;
  border-radius: 999px;
  background: rgba(255,255,255,0.78);
  border: 1px solid rgba(23,107,135,0.12);
  color: rgba(17,19,22,0.74);
  font-size: 11px;
  font-weight: 600;
  opacity: 0;
  transform: translateY(10px) scale(0.96);
  animation: phraseRise 0.52s ease forwards, phraseBreath 2.7s ease-in-out infinite;
  animation-delay: var(--delay, 0ms), calc(var(--delay, 0ms) + 600ms);
}

.graph-theatre {
  grid-template-columns: minmax(0, 1fr) 150px;
  align-items: center;
}

.connection-mini {
  position: relative;
  width: 150px;
  height: 106px;
  border-radius: 24px;
  background:
    radial-gradient(circle at 50% 50%, rgba(23,107,135,0.10), transparent 3.6rem),
    radial-gradient(rgba(17,19,22,0.12) 1px, transparent 1px),
    rgba(255,255,255,0.62);
  background-size: auto, 16px 16px, auto;
  overflow: hidden;
}

.mini-node {
  position: absolute;
  z-index: 2;
  width: 42px;
  height: 42px;
  border-radius: 50%;
  background: linear-gradient(135deg, var(--hx-accent), var(--hx-accent-2));
  color: white;
  display: grid;
  place-items: center;
  font-family: var(--hx-font-mono);
  font-size: 11px;
  font-weight: 800;
  box-shadow: 0 12px 28px rgba(23,107,135,0.22);
  animation: nodePulse 2.2s ease-in-out infinite;
}

.mini-node-0 { left: 14px; top: 18px; }
.mini-node-1 { right: 14px; top: 16px; animation-delay: 0.2s; }
.mini-node-2 { left: 54px; bottom: 12px; animation-delay: 0.4s; }

.mini-link {
  position: absolute;
  height: 2px;
  border-radius: 999px;
  background: linear-gradient(90deg, rgba(23,107,135,0.12), rgba(23,107,135,0.62), rgba(192,139,92,0.22));
  transform-origin: left center;
  animation: linkPulse 1.9s ease-in-out infinite;
}

.link-a { width: 74px; left: 48px; top: 38px; transform: rotate(-3deg); }
.link-b { width: 58px; left: 38px; top: 62px; transform: rotate(50deg); animation-delay: 0.18s; }
.link-c { width: 60px; left: 84px; top: 63px; transform: rotate(126deg); animation-delay: 0.32s; }

.complete-theatre {
  background:
    linear-gradient(135deg, rgba(30,127,92,0.08), rgba(255,255,255,0.78)),
    radial-gradient(circle at 86% 18%, rgba(30,127,92,0.12), transparent 12rem);
}

/* Step 01 Tags */
.tags-container {
  margin-top: 12px;
  transition: opacity 0.3s;
}

.tags-container.dimmed {
    opacity: 0.3;
    pointer-events: none;
}

.tag-label {
  display: block;
  font-size: 10px;
  color: var(--hx-muted);
  margin-bottom: 8px;
  font-weight: 600;
}

.tags-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.entity-tag {
  background: rgba(255,255,255,0.56);
  border: 1px solid var(--hx-line);
  padding: 4px 10px;
  border-radius: 4px;
  font-size: 11px;
  color: var(--hx-ink);
  font-family: var(--hx-font-mono);
  transition: all 0.2s;
  animation: riseIn 0.42s ease both;
  animation-delay: var(--delay, 0ms);
}

.entity-tag.clickable {
    cursor: pointer;
}

.entity-tag.clickable:hover {
    background: rgba(23,107,135,0.08);
    border-color: rgba(23,107,135,0.22);
}

/* Ontology Detail Overlay */
.ontology-detail-overlay {
    position: absolute;
    top: 60px; /* Below header roughly */
    left: 20px;
    right: 20px;
    bottom: 20px;
    background: rgba(255, 255, 255, 0.92);
    backdrop-filter: blur(4px);
    z-index: 10;
    border: 1px solid var(--hx-line);
    box-shadow: var(--hx-shadow-soft);
    border-radius: var(--hx-radius-md);
    display: flex;
    flex-direction: column;
    overflow: hidden;
    animation: fadeIn 0.2s ease-out;
}

@keyframes fadeIn { from { opacity: 0; transform: translateY(5px); } to { opacity: 1; transform: translateY(0); } }

.detail-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 12px 16px;
    border-bottom: 1px solid var(--hx-line);
    background: rgba(251,250,247,0.84);
}

.detail-title-group {
    display: flex;
    align-items: center;
    gap: 8px;
}

.detail-type-badge {
    font-size: 9px;
    font-weight: 700;
    color: #FFF;
    background: var(--hx-ink);
    padding: 2px 6px;
    border-radius: 2px;
    text-transform: uppercase;
}

.detail-name {
    font-size: 14px;
    font-weight: 700;
    font-family: var(--hx-font-mono);
}

.close-btn {
    background: none;
    border: none;
    font-size: 18px;
    color: #999;
    cursor: pointer;
    line-height: 1;
}

.close-btn:hover {
    color: var(--hx-ink);
}

.detail-body {
    flex: 1;
    overflow-y: auto;
    padding: 16px;
}

.detail-desc {
    font-size: 12px;
    color: #444;
    line-height: 1.5;
    margin-bottom: 16px;
    padding-bottom: 12px;
    border-bottom: 1px dashed #EAEAEA;
}

.detail-section {
    margin-bottom: 16px;
}

.section-label {
    display: block;
    font-size: 10px;
    font-weight: 600;
    color: #AAA;
    margin-bottom: 8px;
}

.attr-list, .conn-list {
    display: flex;
    flex-direction: column;
    gap: 6px;
}

.attr-item {
    font-size: 11px;
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    align-items: baseline;
    padding: 4px;
    background: #F9F9F9;
    border-radius: 4px;
}

.attr-name {
    font-family: 'JetBrains Mono', monospace;
    font-weight: 600;
    color: #000;
}

.attr-type {
    color: #999;
    font-size: 10px;
}

.attr-desc {
    color: #555;
    flex: 1;
    min-width: 150px;
}

.example-list {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
}

.example-tag {
    font-size: 11px;
    background: #FFF;
    border: 1px solid #E0E0E0;
    padding: 3px 8px;
    border-radius: 12px;
    color: #555;
}

.conn-item {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 11px;
    padding: 6px;
    background: #F5F5F5;
    border-radius: 4px;
    font-family: 'JetBrains Mono', monospace;
}

.conn-node {
    font-weight: 600;
    color: #333;
}

.conn-arrow {
    color: #BBB;
}

/* Step 02 Stats */
.stats-grid {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: 12px;
  background: rgba(17,19,22,0.035);
  padding: 16px;
  border-radius: 6px;
}

.stat-card {
  text-align: center;
}

.stat-value {
  display: block;
  font-size: 20px;
  font-weight: 700;
  color: var(--hx-ink);
  font-family: var(--hx-font-mono);
}

.stat-label {
  font-size: 9px;
  color: var(--hx-muted);
  text-transform: uppercase;
  margin-top: 4px;
  display: block;
}

/* Step 03 Button */
.action-btn {
  width: 100%;
  background: linear-gradient(135deg, #111316, #193845);
  color: #FFF;
  border: none;
  padding: 14px;
  border-radius: var(--hx-radius-md);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition: opacity 0.2s;
}

.action-btn:hover:not(:disabled) {
  opacity: 0.8;
}

.action-btn:disabled {
  background: rgba(17,19,22,0.12);
  cursor: not-allowed;
}

.progress-section {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 12px;
  color: var(--hx-accent);
  margin-bottom: 12px;
}

.spinner-sm {
  width: 14px;
  height: 14px;
  border: 2px solid rgba(23,107,135,0.18);
  border-top-color: var(--hx-accent);
  border-radius: 50%;
  animation: spin 1s linear infinite;
}

.error-callout {
  margin: 14px 0;
  padding: 14px;
  border: 1px solid rgba(181,70,70,0.22);
  border-radius: var(--hx-radius-md);
  background: rgba(181,70,70,0.06);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.error-title {
  display: block;
  font-size: 12px;
  font-weight: 800;
  color: var(--hx-danger);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  margin-bottom: 4px;
}

.error-message {
  margin: 0;
  color: #643331;
  font-size: 13px;
  line-height: 1.4;
}

.retry-btn {
  border: none;
  border-radius: 6px;
  background: var(--hx-danger);
  color: white;
  font-weight: 800;
  padding: 8px 12px;
  cursor: pointer;
  white-space: nowrap;
}

@keyframes spin { to { transform: rotate(360deg); } }

@keyframes focusGlow {
  0%, 100% { opacity: 0.14; }
  50% { opacity: 0.34; }
}

@keyframes riseIn {
  from { opacity: 0; transform: translateY(8px); }
  to { opacity: 1; transform: translateY(0); }
}

@keyframes phraseRise {
  to { opacity: 1; transform: translateY(0) scale(1); }
}

@keyframes phraseBreath {
  0%, 100% { box-shadow: 0 0 0 rgba(23,107,135,0); }
  50% { box-shadow: 0 10px 24px rgba(23,107,135,0.08); }
}

@keyframes nodePulse {
  0%, 100% { transform: scale(1); }
  50% { transform: scale(1.08); }
}

@keyframes linkPulse {
  0%, 100% { opacity: 0.42; }
  50% { opacity: 1; }
}

/* System Logs */
.system-logs {
  background: #111316;
  color: #d7d4cc;
  padding: 16px;
  font-family: var(--hx-font-mono);
  border-top: 1px solid rgba(255,255,255,0.08);
  flex-shrink: 0;
}

.log-header {
  display: flex;
  justify-content: space-between;
  border-bottom: 1px solid rgba(255,255,255,0.1);
  padding-bottom: 8px;
  margin-bottom: 8px;
  font-size: 10px;
  color: rgba(255,255,255,0.42);
}

.log-content {
  display: flex;
  flex-direction: column;
  gap: 4px;
  height: 80px; /* Approx 4 lines visible */
  overflow-y: auto;
  padding-right: 4px;
}

.log-content::-webkit-scrollbar {
  width: 4px;
}

.log-content::-webkit-scrollbar-thumb {
  background: rgba(255,255,255,0.18);
  border-radius: 2px;
}

.log-line {
  font-size: 11px;
  display: flex;
  gap: 12px;
  line-height: 1.5;
}

.log-time {
  color: rgba(255,255,255,0.34);
  min-width: 75px;
}

.log-msg {
  color: rgba(255,255,255,0.72);
  word-break: break-all;
}
</style>
