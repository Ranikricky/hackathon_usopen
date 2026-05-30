<template>
  <div class="main-view">
    <!-- Header -->
    <header class="app-header">
      <div class="header-left">
        <div class="brand" @click="router.push('/')"><BrandMark /></div>
      </div>
      
      <div class="header-center">
        <div class="view-switcher">
          <button 
            v-for="mode in ['graph', 'split', 'workbench']" 
            :key="mode"
            class="switch-btn"
            :class="{ active: viewMode === mode }"
            @click="viewMode = mode"
          >
            {{ { graph: $t('main.layoutGraph'), split: $t('main.layoutSplit'), workbench: $t('main.layoutWorkbench') }[mode] }}
          </button>
        </div>
      </div>

      <div class="header-right">
        <LanguageSwitcher />
        <div class="step-divider"></div>
        <div class="workflow-step">
          <span class="step-num">Step {{ currentStep }}/5</span>
          <span class="step-name">{{ $tm('main.stepNames')[currentStep - 1] }}</span>
        </div>
        <div class="step-divider"></div>
        <span class="status-indicator" :class="statusClass">
          <span class="dot"></span>
          {{ statusText }}
        </span>
      </div>
    </header>

    <!-- Main Content Area -->
    <main class="content-area">
      <!-- Left Panel: Graph -->
      <div class="panel-wrapper left" :style="leftPanelStyle">
        <GraphPanel 
          :graphData="graphData"
          :loading="graphLoading"
          :currentPhase="currentPhase"
          @refresh="refreshGraph"
          @toggle-maximize="toggleMaximize('graph')"
        />
      </div>

      <!-- Right Panel: Step Components -->
      <div class="panel-wrapper right" :style="rightPanelStyle">
        <!-- Step 1: Graph Build -->
        <Step1GraphBuild 
          v-if="currentStep === 1"
          :currentPhase="currentPhase"
          :projectData="projectData"
          :ontologyProgress="ontologyProgress"
          :buildProgress="buildProgress"
          :graphData="graphData"
          :error="error"
          :systemLogs="systemLogs"
          @approve-domain-contract="approveDomainContract"
          @next-step="handleNextStep"
          @retry="retryCurrentStep"
        />
        <!-- Step 2: Horizon XL Structured Workbench -->
        <HorizonStructuredWorkbench
          v-else-if="currentStep === 2"
          :projectData="projectData"
          :graphData="graphData"
          @add-log="addLog"
        />
      </div>
    </main>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import GraphPanel from '../components/GraphPanel.vue'
import Step1GraphBuild from '../components/Step1GraphBuild.vue'
import HorizonStructuredWorkbench from '../components/HorizonStructuredWorkbench.vue'
import { generateOntology, getProject, buildGraph, getTaskStatus, getGraphData, resetProject } from '../api/graph'
import { planStructuredSimulation } from '../api/simulation'
import { getPendingUpload, clearPendingUpload, getLastPrompt } from '../store/pendingUpload'
import LanguageSwitcher from '../components/LanguageSwitcher.vue'
import BrandMark from '../components/BrandMark.vue'

const route = useRoute()
const router = useRouter()
const { t, tm } = useI18n()

// Layout State
const viewMode = ref('split') // graph | split | workbench

// Step State
const currentStep = ref(1) // 1: Graph Build, 2: Environment Setup, 3: Simulation, 4: Report, 5: Interaction
const stepNames = computed(() => tm('main.stepNames'))

// Data State
const currentProjectId = ref(route.params.projectId)
const loading = ref(false)
const graphLoading = ref(false)
const error = ref('')
const projectData = ref(null)
const graphData = ref(null)
const currentPhase = ref(-1) // -1: Upload, 0: Ontology, 1: Build, 2: Complete
const ontologyProgress = ref(null)
const buildProgress = ref(null)
const systemLogs = ref([])

// Polling timers
let pollTimer = null
let graphPollTimer = null
let staleTaskRecoveryAttempted = false

// --- Computed Layout Styles ---
const leftPanelStyle = computed(() => {
  if (viewMode.value === 'graph') return { width: '100%', opacity: 1, transform: 'translateX(0)' }
  if (viewMode.value === 'workbench') return { width: '0%', opacity: 0, transform: 'translateX(-20px)' }
  if (currentStep.value === 1 && currentPhase.value === 1) return { width: '54%', opacity: 1, transform: 'translateX(0)' }
  if (currentStep.value === 1 && currentPhase.value === 0) return { width: '46%', opacity: 1, transform: 'translateX(0)' }
  return { width: '50%', opacity: 1, transform: 'translateX(0)' }
})

const rightPanelStyle = computed(() => {
  if (viewMode.value === 'workbench') return { width: '100%', opacity: 1, transform: 'translateX(0)' }
  if (viewMode.value === 'graph') return { width: '0%', opacity: 0, transform: 'translateX(20px)' }
  if (currentStep.value === 1 && currentPhase.value === 1) return { width: '46%', opacity: 1, transform: 'translateX(0)' }
  if (currentStep.value === 1 && currentPhase.value === 0) return { width: '54%', opacity: 1, transform: 'translateX(0)' }
  return { width: '50%', opacity: 1, transform: 'translateX(0)' }
})

// --- Status Computed ---
const statusClass = computed(() => {
  if (error.value) return 'error'
  if (currentPhase.value >= 2) return 'completed'
  return 'processing'
})

const statusText = computed(() => {
  if (error.value) return 'Error'
  if (currentPhase.value >= 2) return 'Ready'
  if (currentPhase.value === 1) return 'Building Graph'
  if (currentPhase.value === 0) return 'Generating Ontology'
  if (currentPhase.value === -1) return 'Reviewing Contract'
  return 'Initializing'
})

// --- Helpers ---
const addLog = (msg) => {
  const time = new Date().toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' }) + '.' + new Date().getMilliseconds().toString().padStart(3, '0')
  systemLogs.value.push({ time, msg })
  // Keep last 100 logs
  if (systemLogs.value.length > 100) {
    systemLogs.value.shift()
  }
}

const getApiErrorMessage = (err, fallback = 'Request failed') => {
  return err?.response?.data?.error || err?.message || fallback
}

// --- Layout Methods ---
const toggleMaximize = (target) => {
  if (viewMode.value === target) {
    viewMode.value = 'split'
  } else {
    viewMode.value = target
  }
}

const handleNextStep = (params = {}) => {
  if (currentStep.value < 5) {
    currentStep.value++
    addLog(t('log.enterStep', { step: currentStep.value, name: stepNames.value[currentStep.value - 1] }))
    
    // If Step 2 moves into Step 3, record the simulation round configuration.
    if (currentStep.value === 3 && params.maxRounds) {
      addLog(t('log.customSimRounds', { rounds: params.maxRounds }))
    }
  }
}

const handleGoBack = () => {
  if (currentStep.value > 1) {
    currentStep.value--
    addLog(t('log.returnToStep', { step: currentStep.value, name: stepNames.value[currentStep.value - 1] }))
  }
}

const retryCurrentStep = async () => {
  const projectId = currentProjectId.value
  error.value = ''
  ontologyProgress.value = null
  buildProgress.value = null
  stopPolling()
  stopGraphPolling()

  if (projectId === 'new') {
    await handleNewProject()
  } else {
    try {
      addLog(`Resetting project ${projectId} before retry...`)
      const res = await resetProject(projectId)
      if (res.success) {
        projectData.value = res.data
        currentPhase.value = res.data.ontology ? 1 : 0
        addLog('Project reset. Restarting graph build...')
        if (res.data.ontology) {
          await startBuildGraph()
        } else {
          await loadProject()
        }
      } else {
        error.value = res.error || 'Project reset failed'
        addLog(`Project reset failed: ${error.value}`)
      }
    } catch (err) {
      const msg = getApiErrorMessage(err, 'Project reset failed')
      error.value = msg
      addLog(`Exception in retry: ${msg}`)
    }
  }
}

// --- Data Logic ---

const initProject = async () => {
  addLog('Project view initialized.')
  if (currentProjectId.value === 'new') {
    await handleNewProject()
  } else {
    await loadProject()
  }
}

const handleNewProject = async () => {
  const pending = getPendingUpload()
  const queryPrompt = Array.isArray(route.query.prompt) ? route.query.prompt[0] : route.query.prompt
  const simulationRequirement = String(
    pending.simulationRequirement
    || queryPrompt
    || getLastPrompt()
    || ''
  ).trim()
  if (!simulationRequirement) {
    error.value = 'No prompt was handed to Step 1. Returning to the input screen.'
    addLog('Step 1 did not receive a simulation prompt; returning to input screen instead of calling backend.')
    router.replace({ name: 'Home' })
    return
  }
  
  try {
    loading.value = true
    currentPhase.value = -1
    ontologyProgress.value = { message: 'Planning Domain Contract...' }
    addLog('Planning Domain Contract before ontology generation...')

    const res = await planStructuredSimulation({
      prompt: simulationRequirement,
      project_name: 'Horizon XL Simulation',
      create_project: true,
      persist: true,
      approved: false,
      include_external_research: true,
    })
    if (res.success) {
      currentProjectId.value = res.data.project_id || res.data.domain_contract?.project_id
      projectData.value = {
        ...res.data,
        project_id: currentProjectId.value,
        status: 'created',
        simulation_requirement: simulationRequirement,
      }
      router.replace({ name: 'Process', params: { projectId: res.data.project_id } })
      ontologyProgress.value = null
      addLog('Domain Contract ready. Review/approve it to build the signal map.')
    } else {
      error.value = res.error || 'Domain Contract planning failed'
      addLog(`Error planning Domain Contract: ${error.value}`)
    }
  } catch (err) {
    const msg = getApiErrorMessage(err, 'Domain Contract planning failed')
    error.value = msg
    addLog(`Exception in handleNewProject: ${msg}`)
  } finally {
    loading.value = false
  }
}

const approveDomainContract = async () => {
  const pending = getPendingUpload()
  const simulationRequirement = String(
    pending.simulationRequirement
    || projectData.value?.simulation_requirement
    || getLastPrompt()
    || ''
  ).trim()

  if (!simulationRequirement || !currentProjectId.value) {
    error.value = 'Domain Contract approval could not continue because the prompt or project is missing.'
    return
  }

  try {
    loading.value = true
    currentPhase.value = 0
    ontologyProgress.value = { message: 'Generating ontology from approved Domain Contract...' }
    addLog('Domain Contract approved. Generating ontology from approved contract...')

    const formData = new FormData()
    if (Array.isArray(pending.files)) {
      pending.files.forEach(f => formData.append('files', f))
    }
    formData.append('project_id', currentProjectId.value)
    formData.append('simulation_requirement', simulationRequirement)
    if (projectData.value?.domain_contract) {
      formData.append('domain_contract', JSON.stringify({
        ...projectData.value.domain_contract,
        approved: true,
      }))
    }

    const res = await generateOntology(formData)
    if (res.success) {
      clearPendingUpload()
      projectData.value = res.data
      ontologyProgress.value = null
      addLog(`Ontology generated successfully for project ${res.data.project_id}`)
      await startBuildGraph()
    } else {
      error.value = res.error || 'Ontology generation failed'
      addLog(`Error generating ontology: ${error.value}`)
    }
  } catch (err) {
    const msg = getApiErrorMessage(err, 'Ontology generation failed')
    error.value = msg
    addLog(`Exception in approveDomainContract: ${msg}`)
  } finally {
    loading.value = false
  }
}

const loadProject = async () => {
  try {
    loading.value = true
    addLog(`Loading project ${currentProjectId.value}...`)
    const res = await getProject(currentProjectId.value)
    if (res.success) {
      projectData.value = res.data
      updatePhaseByStatus(res.data.status, res.data.error)
      addLog(`Project loaded. Status: ${res.data.status}`)
      
      if (res.data.status === 'created' && res.data.domain_contract && !res.data.ontology) {
        currentPhase.value = -1
        addLog('Loaded saved Domain Contract awaiting approval.')
      } else if (res.data.status === 'ontology_generated' && !res.data.graph_id) {
        await startBuildGraph()
      } else if (res.data.status === 'graph_building' && res.data.graph_build_task_id) {
        currentPhase.value = 1
        startPollingTask(res.data.graph_build_task_id)
        startGraphPolling()
      } else if (res.data.status === 'graph_completed' && res.data.graph_id) {
        currentPhase.value = 2
        await loadGraph(res.data.graph_id)
      }
    } else {
      error.value = res.error
      addLog(`Error loading project: ${res.error}`)
    }
  } catch (err) {
    const msg = getApiErrorMessage(err, 'Failed to load project')
    error.value = msg
    addLog(`Exception in loadProject: ${msg}`)
  } finally {
    loading.value = false
  }
}

const updatePhaseByStatus = (status, projectError = '') => {
  switch (status) {
    case 'created': currentPhase.value = projectData.value?.domain_contract && !projectData.value?.ontology ? -1 : 0; break;
    case 'ontology_generated': currentPhase.value = 0; break;
    case 'graph_building': currentPhase.value = 1; break;
    case 'graph_completed': currentPhase.value = 2; break;
    case 'failed': error.value = projectError || 'Project failed'; break;
  }
}

const startBuildGraph = async () => {
  try {
    currentPhase.value = 1
    buildProgress.value = { progress: 0, message: 'Starting build...' }
    addLog('Initiating graph build...')
    
    const res = await buildGraph({ project_id: currentProjectId.value })
    if (res.success) {
      addLog(`Graph build task started. Task ID: ${res.data.task_id}`)
      startGraphPolling()
      startPollingTask(res.data.task_id)
    } else {
      error.value = res.error
      addLog(`Error starting build: ${res.error}`)
    }
  } catch (err) {
    const msg = getApiErrorMessage(err, 'Failed to start graph build')
    error.value = msg
    addLog(`Exception in startBuildGraph: ${msg}`)
  }
}

const startGraphPolling = () => {
  addLog('Started polling for graph data...')
  fetchGraphData()
  graphPollTimer = setInterval(fetchGraphData, 10000)
}

const fetchGraphData = async () => {
  try {
    // Refresh project info to check for graph_id
    const projRes = await getProject(currentProjectId.value)
    if (projRes.success && projRes.data.graph_id) {
      const gRes = await getGraphData(projRes.data.graph_id)
      if (gRes.success) {
        graphData.value = gRes.data
        const nodeCount = gRes.data.node_count || gRes.data.nodes?.length || 0
        const edgeCount = gRes.data.edge_count || gRes.data.edges?.length || 0
        addLog(`Graph data refreshed. Nodes: ${nodeCount}, Edges: ${edgeCount}`)
      }
    }
  } catch (err) {
    console.warn('Graph fetch error:', err)
  }
}

const startPollingTask = (taskId) => {
  pollTaskStatus(taskId)
  pollTimer = setInterval(() => pollTaskStatus(taskId), 2000)
}

const pollTaskStatus = async (taskId) => {
  try {
    const res = await getTaskStatus(taskId)
    if (res.success) {
      const task = res.data
      
      // Log progress message if it changed
      if (task.message && task.message !== buildProgress.value?.message) {
        addLog(task.message)
      }
      
      buildProgress.value = { progress: task.progress || 0, message: task.message }
      
      if (task.status === 'completed') {
        addLog('Graph build task completed.')
        stopPolling()
        stopGraphPolling() // Stop polling, do final load
        currentPhase.value = 2
        
        // Final load
        const projRes = await getProject(currentProjectId.value)
        if (projRes.success && projRes.data.graph_id) {
            projectData.value = projRes.data
            await loadGraph(projRes.data.graph_id)
        }
      } else if (task.status === 'failed') {
        stopPolling()
        stopGraphPolling()
        error.value = task.error
        addLog(`Graph build task failed: ${task.error}`)
      }
    }
  } catch (e) {
    const status = e?.response?.status
    if (status === 404 && !staleTaskRecoveryAttempted) {
      staleTaskRecoveryAttempted = true
      addLog('Graph task was stale. Rebuilding local graph...')
      stopPolling()
      stopGraphPolling()
      const reset = await resetProject(currentProjectId.value)
      if (reset.success) {
        projectData.value = reset.data
        await startBuildGraph()
        return
      }
    }
    console.error(e)
  }
}

const loadGraph = async (graphId) => {
  graphLoading.value = true
  addLog(`Loading full graph data: ${graphId}`)
  try {
    const res = await getGraphData(graphId)
    if (res.success) {
      graphData.value = res.data
      addLog('Graph data loaded successfully.')
    } else {
      addLog(`Failed to load graph data: ${res.error}`)
    }
  } catch (e) {
    addLog(`Exception loading graph: ${e.message}`)
  } finally {
    graphLoading.value = false
  }
}

const refreshGraph = () => {
  if (projectData.value?.graph_id) {
    addLog('Manual graph refresh triggered.')
    loadGraph(projectData.value.graph_id)
  }
}

const stopPolling = () => {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

const stopGraphPolling = () => {
  if (graphPollTimer) {
    clearInterval(graphPollTimer)
    graphPollTimer = null
    addLog('Graph polling stopped.')
  }
}

onMounted(() => {
  initProject()
})

onUnmounted(() => {
  stopPolling()
  stopGraphPolling()
})
</script>

<style scoped>
.main-view {
  height: 100vh;
  display: flex;
  flex-direction: column;
  background:
    radial-gradient(circle at 14% 0%, rgba(23,107,135,0.12), transparent 28rem),
    radial-gradient(circle at 94% 10%, rgba(192,139,92,0.12), transparent 26rem),
    var(--hx-bg);
  overflow: hidden;
  font-family: var(--hx-font-body);
}

/* Header */
.app-header {
  height: 72px;
  border-bottom: 1px solid var(--hx-line);
  display: grid;
  grid-template-columns: minmax(160px, 0.8fr) auto minmax(300px, 1fr);
  align-items: center;
  gap: 18px;
  padding: 0 26px;
  background: rgba(255,255,255,0.72);
  backdrop-filter: blur(22px) saturate(1.1);
  z-index: 100;
  position: relative;
  box-shadow: 0 12px 34px rgba(34,31,25,0.05);
}

.header-center {
  justify-self: center;
  min-width: 0;
}

.brand {
  font-family: var(--hx-font-display);
  font-weight: 700;
  font-size: 15px;
  letter-spacing: 0.18em;
  cursor: pointer;
}

.brand::before {
  content: '';
  display: inline-block;
  width: 9px;
  height: 9px;
  margin-right: 11px;
  border-radius: 50%;
  background: linear-gradient(135deg, var(--hx-accent), var(--hx-accent-2));
  box-shadow: 0 0 18px rgba(23,107,135,0.4);
}

.view-switcher {
  display: flex;
  background: rgba(17,19,22,0.055);
  padding: 4px;
  border: 1px solid var(--hx-line);
  border-radius: 999px;
  gap: 4px;
}

.switch-btn {
  border: none;
  background: transparent;
  padding: 8px 18px;
  font-size: 12px;
  font-weight: 600;
  color: var(--hx-muted);
  border-radius: 999px;
  cursor: pointer;
  transition: all 0.2s;
}

.switch-btn.active {
  background: rgba(255,255,255,0.88);
  color: var(--hx-ink);
  box-shadow: 0 8px 22px rgba(34,31,25,0.08);
}

.status-indicator {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--hx-muted);
  font-weight: 500;
}

.header-right {
  display: flex;
  align-items: center;
  justify-self: end;
  gap: 12px;
  min-width: 0;
}

.workflow-step {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  white-space: nowrap;
}

.step-num {
  font-family: var(--hx-font-mono);
  font-weight: 700;
  color: var(--hx-muted);
}

.step-name {
  font-weight: 700;
  color: var(--hx-ink);
}

.step-divider {
  width: 1px;
  height: 14px;
  background-color: var(--hx-line);
}

.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--hx-faint);
}

.status-indicator.processing .dot { background: var(--hx-warn); animation: pulse 1s infinite; }
.status-indicator.completed .dot { background: var(--hx-good); }
.status-indicator.error .dot { background: var(--hx-danger); }

@keyframes pulse { 50% { opacity: 0.5; } }

/* Content */
.content-area {
  flex: 1;
  display: flex;
  position: relative;
  overflow: hidden;
}

.panel-wrapper {
  height: 100%;
  overflow: hidden;
  transition: width 0.4s cubic-bezier(0.25, 0.8, 0.25, 1), opacity 0.3s ease, transform 0.3s ease;
  will-change: width, opacity, transform;
}

.panel-wrapper.left {
  border-right: 1px solid var(--hx-line);
}

@media (max-width: 1180px) {
  .app-header {
    grid-template-columns: auto 1fr auto;
    padding: 0 16px;
  }

  .workflow-step,
  .step-divider {
    display: none;
  }

  .switch-btn {
    padding: 7px 13px;
  }
}
</style>
