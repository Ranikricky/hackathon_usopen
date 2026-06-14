<template>
  <div class="horizon-workbench">
    <div class="workbench-hero">
      <div>
        <span class="eyebrow">Horizon XL Dispatch</span>
        <h2>Forecast desk</h2>
        <p>
          Domain contract, evidence, agent society, debate readiness, forecast
          ledger, then a publishable report. Raw transcripts stay in the audit drawer.
        </p>
      </div>
      <div class="status-chip" :class="validation?.passed ? 'passed' : 'pending'">
        {{ validation?.passed ? 'Validation passed' : activeStageLabel }}
      </div>
    </div>

    <section v-if="forecastThesis || disputes.length || assumptions.length" class="thesis-strip">
      <div>
        <span class="eyebrow">Current thesis</span>
        <h3>{{ forecastThesis?.statement || 'Thesis will appear after the structured run.' }}</h3>
        <p v-if="forecastThesis?.core_drivers?.length">
          Drivers: {{ forecastThesis.core_drivers.slice(0, 4).join(' · ') }}
        </p>
      </div>
      <div class="thesis-metrics">
        <span>{{ assumptions.length }} assumptions</span>
        <span>{{ disputes.length }} disputes</span>
        <span>{{ readinessLabel }}</span>
      </div>
    </section>

    <div v-if="error" class="error-panel">
      <strong>Workbench stopped</strong>
      <p>{{ error }}</p>
    </div>

    <div class="stage-grid">
      <section class="stage-card" :class="{ active: activeStage === 'plan', done: domainPlan }">
        <div class="stage-head">
          <span>01</span>
          <strong>Domain Contract</strong>
        </div>
        <p class="stage-desc">
          Separate evidence, instructions, targets, actors, horizon, scenarios, and engine mode before graph build.
        </p>
        <div class="stage-action">
          <button class="primary-btn" :disabled="busy" @click="createPlan">
            {{ domainPlan ? 'Re-plan' : 'Create plan' }}
          </button>
        </div>

        <div v-if="domainPlan" class="result-block">
          <div class="kv">
            <span>Domain</span>
            <strong>{{ domainPlan.domain }}</strong>
          </div>
          <div class="kv">
            <span>Engine</span>
            <strong>{{ domainPlan.engine_mode || domainPlan.domain_contract?.engine_mode || 'structured_forecast' }}</strong>
          </div>
          <div class="kv">
            <span>Horizon</span>
            <strong>{{ horizonLabel }}</strong>
          </div>
          <div class="mini-list">
            <span
              v-for="target in domainPlan.target_variables || []"
              :key="target.name"
              class="pill"
            >
              {{ target.name }} · {{ target.unit }}
            </span>
          </div>
        </div>
      </section>

      <section class="stage-card" :class="{ active: activeStage === 'agents', done: agents.length }">
        <div class="stage-head">
          <span>02</span>
          <strong>Agent Society</strong>
        </div>
        <p class="stage-desc">
          Generate causal actors, ground-truth participants, research/data roles, moderator, mediator, and quant roles.
        </p>
        <div class="stage-action">
          <button class="primary-btn" :disabled="busy || !domainPlan" @click="createAgents">
            {{ agents.length ? 'Regenerate agents' : 'Generate agents' }}
          </button>
        </div>

        <div v-if="agents.length" class="agent-list">
          <article v-for="agent in visibleAgents" :key="agent.agent_id || agent.name" class="agent-card">
            <strong>{{ agent.name }}</strong>
            <span>{{ agent.role || agent.causal_role }}</span>
            <small>{{ numericRequired(agent) ? 'numeric' : 'qualitative' }}</small>
          </article>
          <div v-if="agents.length > visibleAgents.length" class="more-note">
            +{{ agents.length - visibleAgents.length }} more agents
          </div>
        </div>
      </section>

      <section class="stage-card" :class="{ active: activeStage === 'run', done: simulationId }">
        <div class="stage-head">
          <span>03</span>
          <strong>Debate Readiness</strong>
        </div>
        <p class="stage-desc">
          Build thesis, assumptions, disputes, then run time-pocket debate only if the setup is coherent.
        </p>
        <div class="stage-action">
          <button class="primary-btn" :disabled="busy || !domainPlan || !agents.length" @click="runStructured">
            Run structured simulation
          </button>
        </div>

        <div v-if="simulationId" class="result-block">
          <div class="kv">
            <span>Simulation</span>
            <strong class="mono">{{ simulationId }}</strong>
          </div>
          <div class="kv">
            <span>Outputs</span>
            <strong>{{ runSummary.agent_output_count || state?.agent_outputs?.length || 0 }}</strong>
          </div>
          <div class="kv">
            <span>Debate turns</span>
            <strong>{{ state?.discussion_transcript?.length || 0 }}</strong>
          </div>
          <div class="kv">
            <span>Readiness</span>
            <strong>{{ readinessLabel }}</strong>
          </div>
          <ul v-if="readinessIssues.length" class="issue-list">
            <li v-for="issue in readinessIssues.slice(0, 4)" :key="issue">{{ issue }}</li>
          </ul>
        </div>
      </section>

      <section class="stage-card" :class="{ active: activeStage === 'validation', done: validation?.passed }">
        <div class="stage-head">
          <span>04</span>
          <strong>Forecast Ledger</strong>
        </div>
        <p class="stage-desc">
          Validate the cleaned forecast ledger before any report can become polished prose.
        </p>
        <div class="stage-action">
          <button class="primary-btn" :disabled="busy || !simulationId" @click="refreshState">
            Refresh validation
          </button>
        </div>

        <div v-if="validation" class="validation-box" :class="{ passed: validation.passed }">
          <strong>{{ validation.passed ? 'Passed' : 'Blocked' }}</strong>
          <span>Quality: {{ validation.numeric_quality_score ?? 0 }}</span>
          <ul v-if="validation.errors?.length">
            <li v-for="item in validation.errors.slice(0, 5)" :key="item">{{ item }}</li>
          </ul>
        </div>
        <div v-if="ledgerRows.length" class="mini-list ledger-list">
          <span v-for="row in ledgerRows.slice(0, 6)" :key="row.target_name || row.target_id" class="pill">
            {{ row.target_name || row.target_id }} · {{ row.confidence || row.validation_status || 'tracked' }}
          </span>
        </div>
      </section>
    </div>

    <div class="output-area">
      <section class="output-card">
        <div class="output-head">
          <div>
            <span class="eyebrow">Simulation room</span>
            <h3>Live room by time pocket</h3>
          </div>
          <div class="room-actions">
            <button class="secondary-btn" :disabled="busy || !simulationId" @click="refreshState">
              Refresh room
            </button>
            <button class="secondary-btn" :disabled="busy || !simulationId" @click="loadTranscript">
              Audit transcript
            </button>
          </div>
        </div>
        <div v-if="roomPockets.length" class="room-stage">
          <section
            v-for="pocket in roomPockets"
            :key="pocket.key"
            class="room-pocket"
          >
            <header>
              <span>{{ pocket.label }}</span>
              <strong>{{ pocket.turns.length }} turns</strong>
            </header>
            <article
              v-for="turn in pocket.turns"
              :key="turn.turn_id || `${turn.pocket_id}-${turn.speaker_name}-${turn.index}`"
              class="room-turn"
              :class="turnClass(turn)"
            >
              <div class="room-avatar">{{ speakerInitials(turn) }}</div>
              <div class="room-bubble">
                <div class="room-meta">
                  <strong>{{ turn.speaker_name || turn.speaker_id || 'Agent' }}</strong>
                  <span>{{ readableTurnType(turn.turn_type) }}</span>
                </div>
                <p>{{ cleanRoomMessage(turn.message) }}</p>
              </div>
            </article>
          </section>
        </div>
        <p v-else class="empty">Run the structured simulation to open the room.</p>
        <details v-if="transcript" class="audit-transcript">
          <summary>Open raw audit transcript</summary>
          <article
            class="transcript transcript-readable"
            v-html="renderMarkdown(transcript)"
          ></article>
        </details>
      </section>

      <section class="output-card">
        <div class="output-head">
          <div>
            <span class="eyebrow">Final state</span>
            <h3>Forecast summary</h3>
          </div>
          <button class="secondary-btn publish-btn" :disabled="busy || !validation?.passed" @click="startReport">
            Publish full report
          </button>
        </div>

        <div v-if="finalOutcome" class="forecast-summary">
          <div v-if="finalOutcome.projected_winner" class="winner-strip">
            <span>Projected winner/plurality</span>
            <strong>{{ finalOutcome.projected_winner }}</strong>
          </div>

          <table v-if="finalForecastRows.length" class="forecast-table">
            <thead>
              <tr>
                <th>Forecast item</th>
                <th>Value</th>
                <th>Unit</th>
                <th>Point</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in finalForecastRows" :key="`${row.label}-${row.unit}`">
                <td>{{ row.label }}</td>
                <td>{{ row.value }}</td>
                <td>{{ row.unit }}</td>
                <td>{{ row.date || 'final base case' }}</td>
              </tr>
            </tbody>
          </table>

          <pre v-else>{{ JSON.stringify(finalOutcome, null, 2) }}</pre>
        </div>
        <p v-else class="empty">
          Forecast summary appears after the structured state is saved.
        </p>

        <div class="adapter-actions">
          <button
            v-for="type in outputTypes"
            :key="type"
            class="secondary-btn compact"
            :disabled="busy || !validation?.passed"
            @click="loadOutput(type)"
          >
            {{ type.replace('_', ' ') }}
          </button>
        </div>

        <article v-if="adapterOutput" class="adapter-output">{{ adapterOutput }}</article>
      </section>

      <section class="output-card ask-state-card">
        <div class="output-head">
          <div>
            <span class="eyebrow">Ask State</span>
            <h3>Question the saved simulation</h3>
          </div>
          <span class="state-source-chip">read-only</span>
        </div>
        <p class="empty compact-copy">
          Ask about the bottom line, forecast numbers, assumptions, evidence, or agent disagreement.
          Answers come from the structured state, not a new freeform report.
        </p>
        <div class="ask-state-form">
          <input
            v-model="askQuestion"
            :disabled="busy || !simulationId"
            type="text"
            placeholder="Example: what is the bottom line and what evidence drove it?"
            @keyup.enter="askState"
          />
          <button class="secondary-btn" :disabled="busy || !simulationId || !askQuestion.trim()" @click="askState">
            Ask
          </button>
        </div>
        <article v-if="askAnswer" class="ask-answer">
          <div v-html="renderMarkdown(askAnswer)"></div>
          <table v-if="askFacts.length" class="forecast-table ask-facts-table">
            <thead>
              <tr>
                <th>Saved item</th>
                <th>Value</th>
                <th>Unit</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="fact in askFacts" :key="`${fact.label}-${fact.value}-${fact.status}`">
                <td>{{ fact.label }}</td>
                <td>{{ fact.value ?? '—' }}</td>
                <td>{{ fact.unit || '—' }}</td>
                <td>{{ fact.status || fact.confidence || fact.date || 'saved' }}</td>
              </tr>
            </tbody>
          </table>
          <div v-if="askSources.length" class="source-row">
            <span v-for="source in askSources" :key="source">{{ source }}</span>
          </div>
        </article>
      </section>
    </div>

    <section v-if="disputes.length" class="dispute-board">
      <div class="visual-briefing-head">
        <span class="eyebrow">Debate map</span>
        <h3>Disputes the room had to resolve</h3>
      </div>
      <div class="dispute-grid">
        <article v-for="dispute in disputes.slice(0, 6)" :key="dispute.dispute_id || dispute.question" class="dispute-card">
          <strong>{{ dispute.question }}</strong>
          <p>{{ dispute.side_a?.claim }}</p>
          <p>{{ dispute.side_b?.claim }}</p>
        </article>
      </div>
    </section>

    <section v-if="reportOutput" id="published-report" class="published-report">
      <div class="report-masthead">
        <span class="eyebrow">Published structured report</span>
        <h2>{{ reportOutput.title || 'Structured Forecast Report' }}</h2>
        <p>{{ reportOutput.summary }}</p>
      </div>

      <div class="report-meta-grid">
        <div>
          <span>Simulation</span>
          <strong class="mono">{{ simulationId }}</strong>
        </div>
        <div>
          <span>Agents</span>
          <strong>{{ agents.length }}</strong>
        </div>
        <div>
          <span>Time pockets</span>
          <strong>{{ state?.time_pockets?.length || 0 }}</strong>
        </div>
        <div>
          <span>Validation</span>
          <strong>{{ validation?.passed ? 'Passed' : 'Blocked' }}</strong>
        </div>
      </div>

      <div v-if="reportOutput.brief_cards?.length" class="report-brief-grid">
        <article
          v-for="card in reportOutput.brief_cards"
          :key="`${card.label}-${card.value}`"
          class="brief-card"
          :class="card.tone"
        >
          <span>{{ card.label }}</span>
          <strong>{{ card.value }}</strong>
          <p>{{ card.detail }}</p>
        </article>
      </div>

      <section v-if="reportOutput.story_panels?.length" class="story-board">
        <div class="visual-briefing-head">
          <span class="eyebrow">Reader narrative</span>
          <h3>The story before the appendix</h3>
        </div>
        <div class="story-grid">
          <article
            v-for="panel in reportOutput.story_panels"
            :key="`${panel.kicker}-${panel.title}`"
            class="story-panel"
            :class="panel.tone"
          >
            <span>{{ panel.kicker }}</span>
            <h4>{{ panel.title }}</h4>
            <p>{{ panel.text }}</p>
          </article>
        </div>
      </section>

      <section v-if="reportOutput.visuals?.length" class="visual-briefing">
        <div class="visual-briefing-head">
          <span class="eyebrow">Visual briefing</span>
          <h3>Scenario shape, disagreement, and debate flow</h3>
        </div>
        <div class="visual-grid">
          <article
            v-for="visual in reportOutput.visuals"
            :key="visual.title"
            class="visual-card"
            :class="visual.type"
          >
            <div class="visual-card-head">
              <h4>{{ visual.title }}</h4>
              <span>{{ visual.subtitle }}</span>
            </div>
            <div class="bar-stack">
              <div
                v-for="bar in visual.bars || []"
                :key="`${visual.title}-${bar.label}`"
                class="bar-row"
              >
                <div class="bar-label">
                  <span>{{ bar.label }}</span>
                  <strong>{{ bar.display }}</strong>
                </div>
                <div class="bar-track">
                  <div class="bar-fill" :style="{ width: `${barPercent(visual, bar)}%` }"></div>
                </div>
              </div>
            </div>
          </article>
        </div>
      </section>

      <section v-if="reportOutput.image_panels?.length" class="image-board">
        <div class="visual-briefing-head">
          <span class="eyebrow">Image desk</span>
          <h3>Editorial frames for the simulation</h3>
        </div>
        <div class="image-grid">
          <article
            v-for="panel in reportOutput.image_panels"
            :key="`${panel.motif}-${panel.title}`"
            class="image-panel"
            :class="panel.motif"
          >
            <div class="image-art" aria-hidden="true">
              <span></span>
              <span></span>
              <span></span>
            </div>
            <div>
              <span>{{ panel.kicker }}</span>
              <h4>{{ panel.title }}</h4>
              <p>{{ panel.caption }}</p>
            </div>
          </article>
        </div>
      </section>

      <section v-if="reportOutput.quote_cards?.length" class="quote-board">
        <div class="visual-briefing-head">
          <span class="eyebrow">Room quotes</span>
          <h3>What would make the report memorable</h3>
        </div>
        <div class="quote-grid">
          <article
            v-for="quote in reportOutput.quote_cards"
            :key="`${quote.speaker}-${quote.role}-${quote.quote}`"
            class="quote-card"
          >
            <p>“{{ quote.quote }}”</p>
            <footer>
              <strong>{{ quote.speaker }}</strong>
              <span>{{ quote.role }} · {{ quote.pocket }}</span>
            </footer>
          </article>
        </div>
      </section>

      <section v-if="reportOutput.social_cards?.length" class="social-board">
        <div class="visual-briefing-head">
          <span class="eyebrow">Social cards</span>
          <h3>Shareable pullouts without pretending they are real tweets</h3>
        </div>
        <div class="social-grid">
          <article
            v-for="card in reportOutput.social_cards"
            :key="`${card.handle}-${card.text}`"
            class="social-card"
          >
            <header>
              <strong>{{ card.handle }}</strong>
              <span>{{ card.label }}</span>
            </header>
            <p>{{ card.text }}</p>
          </article>
        </div>
      </section>

      <nav v-if="reportOutput.sections?.length" class="report-toc" aria-label="Report sections">
        <a
          v-for="(section, index) in reportOutput.sections"
          :key="section.title"
          :href="`#report-section-${index}`"
        >
          {{ String(index + 1).padStart(2, '0') }} {{ section.title }}
        </a>
      </nav>

      <article class="report-paper">
        <section
          v-for="(section, index) in reportOutput.sections || []"
          :id="`report-section-${index}`"
          :key="section.title"
          class="report-section"
        >
          <span class="section-number">{{ String(index + 1).padStart(2, '0') }}</span>
          <h3>{{ section.title }}</h3>
          <div class="report-section-body" v-html="renderMarkdown(section.content)"></div>
        </section>

        <div
          v-if="!reportOutput.sections?.length && reportOutput.markdown"
          class="report-section-body"
          v-html="renderMarkdown(reportOutput.markdown)"
        ></div>
      </article>
    </section>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, ref } from 'vue'
import {
  generateStructuredAgents,
  getStructuredSimulationState,
  getStructuredTranscript,
  getStructuredValidation,
  planStructuredSimulation,
  runStructuredSimulation
} from '../api/simulation'
import { askStructuredState, generateStructuredOutput } from '../api/outputs'

const props = defineProps({
  projectData: { type: Object, default: null },
  graphData: { type: Object, default: null },
  simulationId: { type: String, default: '' },
})

const emit = defineEmits(['add-log'])

const busy = ref(false)
const error = ref('')
const activeStage = ref('plan')
const domainPlan = ref(null)
const agents = ref([])
const simulationId = ref('')
const runSummary = ref({})
const state = ref(null)
const validation = ref(null)
const transcript = ref('')
const adapterOutput = ref('')
const reportOutput = ref(null)
const askQuestion = ref('')
const askAnswer = ref('')
const askFacts = ref([])
const askSources = ref([])
const outputTypes = ['numeric_table', 'charts', 'executive_memo', 'news_article']

const prompt = computed(() => props.projectData?.simulation_requirement || props.projectData?.requirement || '')
const graphId = computed(() => props.projectData?.graph_id || props.graphData?.graph_id || '')
const projectId = computed(() => props.projectData?.project_id || '')

const visibleAgents = computed(() => agents.value.slice(0, 12))
const forecastLedger = computed(() => state.value?.forecast_ledger || state.value?.aggregated_outputs?.forecast_ledger || {})
const forecastThesis = computed(() => state.value?.forecast_thesis || state.value?.aggregated_outputs?.forecast_thesis || forecastLedger.value?.forecast_thesis || null)
const assumptions = computed(() => state.value?.assumption_registry || state.value?.aggregated_outputs?.assumption_registry || forecastLedger.value?.assumption_registry || [])
const disputes = computed(() => state.value?.dispute_registry || state.value?.aggregated_outputs?.dispute_registry || forecastLedger.value?.dispute_registry || [])
const debateReadiness = computed(() => state.value?.debate_readiness || state.value?.aggregated_outputs?.debate_readiness || runSummary.value?.readiness || {})
const readinessIssues = computed(() => [
  ...(debateReadiness.value?.blocking_issues || []),
  ...(debateReadiness.value?.warnings || []),
])
const readinessLabel = computed(() => {
  if (!debateReadiness.value || Object.keys(debateReadiness.value).length === 0) return 'Not checked'
  const score = debateReadiness.value.score ?? 0
  return debateReadiness.value.ready === false ? `Blocked · ${score}/100` : `Ready · ${score}/100`
})
const ledgerRows = computed(() => forecastLedger.value?.targets || forecastLedger.value?.agent_forecast_rows || [])
const finalOutcome = computed(() => state.value?.aggregated_outputs?.final_outcome || null)
const finalForecastRows = computed(() => {
  const final = finalOutcome.value || {}
  const rows = []

  Object.entries(final.vote_share_forecast || {}).forEach(([name, value]) => {
    rows.push({ label: `${name} vote share`, value, unit: '%' })
  })
  Object.entries(final.seat_forecast || {}).forEach(([name, value]) => {
    rows.push({ label: `${name} seats`, value, unit: 'seats' })
  })
  Object.entries(final.target_forecast || {}).forEach(([name, point]) => {
    rows.push({
      label: name,
      value: point?.value,
      unit: point?.unit || '',
      date: point?.date || '',
    })
  })

  return rows.slice(0, 18)
})

const roomPockets = computed(() => {
  const transcriptTurns = state.value?.discussion_transcript || []
  const groups = []
  const byKey = new Map()

  transcriptTurns.forEach((turn, index) => {
    const key = turn.pocket_id || turn.pocket_label || 'room'
    if (!byKey.has(key)) {
      const group = {
        key,
        label: turn.pocket_label || turn.pocket_id || 'Simulation room',
        turns: [],
      }
      byKey.set(key, group)
      groups.push(group)
    }
    byKey.get(key).turns.push({ ...turn, index })
  })

  return groups.map((group) => ({
    ...group,
    turns: group.turns.slice(0, 80),
  }))
})

const activeStageLabel = computed(() => {
  if (busy.value) return 'Running'
  if (!domainPlan.value) return 'Needs plan'
  if (!agents.value.length) return 'Needs agents'
  if (!simulationId.value) return 'Ready to run'
  if (!validation.value?.passed) return 'Needs validation'
  return 'Ready'
})

const horizonLabel = computed(() => {
  const h = domainPlan.value?.forecast_horizon || {}
  return `${h.start || 'auto'} → ${h.end || 'auto'} · ${h.granularity || 'event_triggered'}`
})

const log = (message) => emit('add-log', message)

const escapeHtml = (value = '') => String(value)
  .replace(/&/g, '&amp;')
  .replace(/</g, '&lt;')
  .replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;')
  .replace(/'/g, '&#039;')

const inlineMarkdown = (value = '') => escapeHtml(value)
  .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
  .replace(/`([^`]+)`/g, '<code>$1</code>')

const tableToHtml = (rows) => {
  const parsed = rows
    .map((row) => row.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((cell) => inlineMarkdown(cell.trim())))
    .filter((row) => row.length > 1)
  if (!parsed.length) return ''
  const [header, , ...body] = parsed
  return [
    '<div class="markdown-table-wrap"><table class="markdown-table">',
    '<thead><tr>',
    header.map((cell) => `<th>${cell}</th>`).join(''),
    '</tr></thead><tbody>',
    body.map((row) => `<tr>${row.map((cell) => `<td>${cell}</td>`).join('')}</tr>`).join(''),
    '</tbody></table></div>',
  ].join('')
}

const renderMarkdown = (markdown = '') => {
  const lines = String(markdown || '').split(/\r?\n/)
  const html = []
  let paragraph = []
  let list = []
  let table = []
  let code = []
  let inCode = false

  const flushParagraph = () => {
    if (!paragraph.length) return
    html.push(`<p>${inlineMarkdown(paragraph.join(' '))}</p>`)
    paragraph = []
  }
  const flushList = () => {
    if (!list.length) return
    html.push(`<ul>${list.map((item) => `<li>${inlineMarkdown(item)}</li>`).join('')}</ul>`)
    list = []
  }
  const flushTable = () => {
    if (!table.length) return
    html.push(tableToHtml(table))
    table = []
  }
  const flushAll = () => {
    flushParagraph()
    flushList()
    flushTable()
  }

  for (const line of lines) {
    const trimmed = line.trim()
    if (trimmed.startsWith('```')) {
      if (inCode) {
        html.push(`<pre class="markdown-code"><code>${escapeHtml(code.join('\n'))}</code></pre>`)
        code = []
        inCode = false
      } else {
        flushAll()
        inCode = true
      }
      continue
    }
    if (inCode) {
      code.push(line)
      continue
    }
    if (!trimmed) {
      flushAll()
      continue
    }
    if (/^\|.+\|$/.test(trimmed)) {
      flushParagraph()
      flushList()
      table.push(trimmed)
      continue
    }
    flushTable()
    const heading = trimmed.match(/^(#{1,4})\s+(.+)$/)
    if (heading) {
      flushParagraph()
      flushList()
      const level = Math.min(4, heading[1].length + 2)
      html.push(`<h${level}>${inlineMarkdown(heading[2])}</h${level}>`)
      continue
    }
    const bullet = trimmed.match(/^[-*]\s+(.+)$/)
    if (bullet) {
      flushParagraph()
      list.push(bullet[1])
      continue
    }
    paragraph.push(trimmed)
  }

  if (inCode) {
    html.push(`<pre class="markdown-code"><code>${escapeHtml(code.join('\n'))}</code></pre>`)
  }
  flushAll()
  return html.join('')
}

const barPercent = (visual, bar) => {
  const values = (visual?.bars || [])
    .map((item) => Number(item.value))
    .filter((value) => Number.isFinite(value))
  const max = Math.max(...values.map((value) => Math.abs(value)), 1)
  const value = Number(bar?.value)
  if (!Number.isFinite(value)) return 0
  return Math.max(5, Math.min(100, (Math.abs(value) / max) * 100))
}

const numericRequired = (agent) => {
  return agent?.numeric_capabilities?.must_output_numbers || agent?.numeric_output_required
}

const readableTurnType = (type = '') => String(type || 'turn')
  .replace(/_/g, ' ')
  .replace(/\b\w/g, (char) => char.toUpperCase())

const speakerInitials = (turn = {}) => {
  const name = String(turn.speaker_name || turn.speaker_id || 'Agent')
  const letters = name
    .replace(/[^A-Za-z0-9 ]+/g, ' ')
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join('')
  return letters || 'A'
}

const turnClass = (turn = {}) => {
  const type = String(turn.turn_type || '')
  if (type.includes('moderator')) return 'moderator'
  if (type.includes('research')) return 'research'
  if (type.includes('data') || type.includes('quant')) return 'data'
  if (type.includes('challenge') || type.includes('rebuttal')) return 'debate'
  if (type.includes('revision') || type.includes('mediator')) return 'mediator'
  if (type.includes('audit') || type.includes('graph')) return 'audit'
  return 'agent'
}

const cleanRoomMessage = (message = '') => String(message || '')
  .replace(/`([^`]+)`/g, '$1')
  .replace(/\s+/g, ' ')
  .trim()

const runStep = async (stage, fn) => {
  busy.value = true
  error.value = ''
  activeStage.value = stage
  try {
    await fn()
  } catch (err) {
    error.value = err?.apiError?.error || err?.message || 'Structured workflow failed'
    log(`Structured workflow failed: ${error.value}`)
  } finally {
    busy.value = false
  }
}

const createPlanInternal = async () => {
  log('Creating Horizon XL domain simulation plan...')
  const res = await planStructuredSimulation({
    project_id: projectId.value,
    prompt: prompt.value,
    include_external_research: false,
  })
  domainPlan.value = res.data
  agents.value = []
  validation.value = null
  state.value = null
  transcript.value = ''
  adapterOutput.value = ''
  reportOutput.value = null
  askAnswer.value = ''
  askFacts.value = []
  askSources.value = []
  log(`Domain plan ready: ${domainPlan.value.domain}`)
}

const createPlan = () => runStep('plan', createPlanInternal)

const createAgentsInternal = async () => {
  log('Generating structured causal agents...')
  const res = await generateStructuredAgents({
    project_id: projectId.value,
    domain_plan: domainPlan.value,
    evidence_summary: prompt.value,
    // Keep the interactive lab fast and deterministic. The plan already
    // contains prompt-derived agent archetypes; the LLM can be used later as
    // an optional enhancer, but it should never block the main workflow.
    use_llm: false,
  })
  agents.value = res.data?.agents || []
  log(`Generated ${agents.value.length} structured agents.`)
}

const createAgents = () => runStep('agents', createAgentsInternal)

const executeStructuredRun = async () => {
  log('Running structured time-pocket simulation...')
  const res = await runStructuredSimulation({
    project_id: projectId.value,
    graph_id: graphId.value,
    simulation_id: simulationId.value || props.simulationId || undefined,
    prompt: prompt.value,
    domain_plan: domainPlan.value,
    agents: agents.value,
  })
  runSummary.value = res.data || {}
  simulationId.value = runSummary.value.simulation_id
  validation.value = runSummary.value.validation || null
  await refreshStateInternal()
  log(`Structured simulation saved: ${simulationId.value}`)
}

const runStructured = () => runStep('run', executeStructuredRun)

const refreshStateInternal = async () => {
  if (!simulationId.value) return
  const stateRes = await getStructuredSimulationState(simulationId.value)
  state.value = stateRes.data
  const validationRes = await getStructuredValidation(simulationId.value)
  validation.value = validationRes.data
  log(`Validation ${validation.value.passed ? 'passed' : 'blocked'} with quality ${validation.value.numeric_quality_score}.`)
}

const refreshState = () => runStep('validation', refreshStateInternal)

const loadTranscriptInternal = async () => {
  if (!simulationId.value) return
  const res = await getStructuredTranscript(simulationId.value, { format: 'markdown' })
  transcript.value = typeof res === 'string' ? res : JSON.stringify(res, null, 2)
}

const loadTranscript = () => runStep('transcript', loadTranscriptInternal)

const loadOutput = (outputType) => runStep('output', async () => {
  if (!simulationId.value) return
  const res = await generateStructuredOutput({
    simulation_id: simulationId.value,
    output_type: outputType,
  })
  const output = res.data?.output || res.data
  adapterOutput.value = typeof output === 'string'
    ? output
    : output?.markdown || JSON.stringify(output, null, 2)
  reportOutput.value = null
})

const askState = () => runStep('ask-state', async () => {
  if (!simulationId.value || !askQuestion.value.trim()) return
  const res = await askStructuredState({
    simulation_id: simulationId.value,
    question: askQuestion.value.trim(),
  })
  const payload = res.data || {}
  askAnswer.value = payload.answer || 'No structured answer is available yet.'
  askFacts.value = payload.facts || []
  askSources.value = payload.sources || []
  log(`Ask State answered from: ${askSources.value.join(', ') || 'structured state'}.`)
})

const startReportInternal = async () => {
  if (!simulationId.value) return
  const res = await generateStructuredOutput({
    simulation_id: simulationId.value,
    output_type: 'report',
  })
  const output = res.data?.output || res.data
  reportOutput.value = typeof output === 'string'
    ? { title: 'Structured Forecast Report', summary: 'Generated from validated simulation state.', markdown: output, sections: [] }
    : output
  adapterOutput.value = ''
  log('Structured report generated from validated simulation state.')
  await nextTick()
  document.getElementById('published-report')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

const startReport = () => runStep('report', startReportInternal)

const runFullWorkflow = () => runStep('plan', async () => {
  activeStage.value = 'plan'
  await createPlanInternal()

  activeStage.value = 'agents'
  await createAgentsInternal()

  activeStage.value = 'run'
  await executeStructuredRun()

  activeStage.value = 'transcript'
  await loadTranscriptInternal()

  if (validation.value?.passed) {
    activeStage.value = 'report'
    await startReportInternal()
  }

  activeStage.value = validation.value?.passed ? 'validation' : 'run'
})

onMounted(() => {
  if (props.simulationId) {
    simulationId.value = props.simulationId
    runStep('validation', async () => {
      try {
        const stateRes = await getStructuredSimulationState(props.simulationId)
        state.value = stateRes.data
        domainPlan.value = state.value?.domain_plan || null
        agents.value = state.value?.agents || []
        const validationRes = await getStructuredValidation(props.simulationId)
        validation.value = validationRes.data
        log(`Loaded existing structured state: ${props.simulationId}`)
        if (!state.value?.agent_outputs?.length && domainPlan.value && agents.value.length) {
          activeStage.value = 'run'
          log('Existing state has no debate turns. Completing structured simulation now...')
          await executeStructuredRun()
          await loadTranscriptInternal()
          if (validation.value?.passed) {
            await startReportInternal()
          }
        }
      } catch (err) {
        log(`No existing structured state for ${props.simulationId}; running the structured workflow now.`)
        if (prompt.value) {
          await createPlanInternal()
          await createAgentsInternal()
          await executeStructuredRun()
          await loadTranscriptInternal()
          if (validation.value?.passed) {
            await startReportInternal()
          }
        }
      }
    })
  } else if (prompt.value) {
    runFullWorkflow()
  }
})
</script>

<style scoped>
.horizon-workbench {
  height: 100%;
  overflow: auto;
  padding: 22px;
  color: var(--hx-ink);
  background:
    linear-gradient(rgba(69, 49, 28, 0.032) 1px, transparent 1px),
    linear-gradient(90deg, rgba(69, 49, 28, 0.025) 1px, transparent 1px),
    var(--hx-bg);
  background-size: 28px 28px;
}

.workbench-hero,
.output-card,
.stage-card {
  border: 1px solid var(--hx-line-strong);
  background: rgba(255, 250, 240, 0.9);
  box-shadow: 3px 3px 0 rgba(42, 32, 21, 0.08);
  backdrop-filter: none;
}

.workbench-hero {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: start;
  gap: 20px;
  padding: 22px 24px;
  border-top: 4px double var(--hx-line-strong);
  border-bottom: 4px double var(--hx-line-strong);
  border-radius: 2px;
  margin-bottom: 16px;
}

.eyebrow {
  display: inline-flex;
  margin-bottom: 8px;
  font-size: 11px;
  letter-spacing: 0.22em;
  text-transform: uppercase;
  color: var(--hx-accent);
  font-family: var(--hx-font-mono);
  font-weight: 800;
}

h2,
h3,
p {
  margin: 0;
}

h2 {
  font-family: var(--hx-font-display);
  font-size: clamp(34px, 5vw, 56px);
  line-height: 0.95;
  letter-spacing: -0.045em;
  color: var(--hx-ink);
}

.workbench-hero p,
.stage-desc,
.empty,
.agent-card span {
  color: rgba(23, 33, 27, 0.68);
  line-height: 1.5;
}

.status-chip {
  align-self: flex-start;
  border-radius: 2px;
  border: 1px solid var(--hx-line-strong);
  padding: 10px 12px;
  font-size: 12px;
  font-weight: 700;
  background: rgba(255, 250, 240, 0.92);
  color: var(--hx-ink);
  font-family: var(--hx-font-mono);
  letter-spacing: 0.06em;
  text-transform: uppercase;
}

.status-chip.passed,
.validation-box.passed {
  background: #e6f7df;
  color: #2f6d26;
}

.error-panel {
  border-radius: 18px;
  border: 1px solid rgba(180, 40, 40, 0.18);
  background: #fff1ed;
  color: #8f2a18;
  padding: 16px;
  margin-bottom: 18px;
}

.thesis-strip,
.dispute-board {
  border: 1px solid var(--hx-line-strong);
  border-radius: 2px;
  background: rgba(255, 250, 240, 0.9);
  box-shadow: 3px 3px 0 rgba(42, 32, 21, 0.08);
  padding: 18px;
  margin-bottom: 16px;
}

.thesis-strip {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 16px;
  align-items: start;
  border-left: 5px solid var(--hx-accent);
}

.thesis-strip h3 {
  font-family: var(--hx-font-display);
  font-size: clamp(24px, 3.5vw, 40px);
  line-height: 1.02;
  letter-spacing: -0.03em;
}

.thesis-strip p {
  margin-top: 8px;
  color: rgba(23, 33, 27, 0.66);
}

.thesis-metrics {
  display: grid;
  gap: 8px;
  min-width: 180px;
}

.thesis-metrics span {
  border: 1px solid rgba(42, 32, 21, 0.14);
  background: rgba(246, 239, 226, 0.86);
  padding: 8px 10px;
  font-family: var(--hx-font-mono);
  font-size: 12px;
  font-weight: 800;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.stage-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 12px;
}

.stage-card {
  min-height: 0;
  border-radius: 2px;
  padding: 18px;
  display: flex;
  flex-direction: column;
  gap: 14px;
  align-items: stretch;
}

.stage-card.active {
  outline: 2px solid rgba(127, 29, 29, 0.34);
  background: rgba(255, 247, 231, 0.96);
}

.stage-card.done {
  background: rgba(252, 247, 236, 0.94);
}

.stage-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 0;
}

.stage-head span {
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  border-radius: 2px;
  background: var(--hx-ink);
  color: var(--hx-bg-soft);
  font-size: 12px;
  font-family: var(--hx-font-mono);
}

.stage-head strong {
  font-family: var(--hx-font-display);
  font-size: 21px;
  line-height: 1;
}

.stage-desc {
  font-size: 16px;
  color: rgba(42, 32, 21, 0.72);
  max-width: 62ch;
}

.stage-action {
  display: flex;
  justify-content: flex-start;
}

.primary-btn,
.secondary-btn {
  border: 1px solid var(--hx-line-strong);
  border-radius: 2px;
  padding: 11px 13px;
  margin-top: 0;
  cursor: pointer;
  font-weight: 800;
  font-family: var(--hx-font-mono);
  letter-spacing: 0.04em;
}

.primary-btn {
  background: var(--hx-ink);
  color: var(--hx-bg-soft);
  min-width: 136px;
}

.secondary-btn {
  background: rgba(255, 250, 240, 0.9);
  color: var(--hx-ink);
}

button:disabled {
  opacity: 0.7;
  color: rgba(42, 32, 21, 0.55);
  cursor: not-allowed;
}

.result-block,
.agent-list,
.validation-box {
  margin-top: 0;
}

.kv {
  display: grid;
  grid-template-columns: minmax(110px, 150px) minmax(0, 1fr);
  align-items: start;
  gap: 12px;
  padding: 9px 0;
  border-bottom: 1px solid rgba(42, 32, 21, 0.12);
}

.kv span {
  color: rgba(23, 33, 27, 0.54);
  min-width: 0;
  white-space: nowrap;
}

.kv strong {
  min-width: 0;
  overflow-wrap: anywhere;
  text-align: left;
}

.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}

.mini-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
}

.pill {
  border-radius: 999px;
  padding: 6px 9px;
  background: rgba(23, 33, 27, 0.07);
  font-size: 12px;
}

.agent-list {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 8px;
  max-height: 220px;
  overflow: auto;
}

.agent-card {
  display: grid;
  gap: 4px;
  padding: 10px 11px;
  border-radius: 2px;
  border: 1px solid rgba(42, 32, 21, 0.16);
  background: rgba(255, 250, 240, 0.74);
  min-width: 0;
}

.agent-card strong,
.agent-card span,
.pill,
.mono {
  overflow-wrap: anywhere;
}

.agent-card small {
  width: fit-content;
  border-radius: 999px;
  padding: 3px 7px;
  background: #efe0c2;
  color: #76521d;
}

.more-note {
  font-size: 12px;
  color: rgba(23, 33, 27, 0.6);
}

.validation-box {
  display: grid;
  gap: 6px;
  border-radius: 2px;
  padding: 12px;
  background: #fff3e4;
  color: #7a4618;
}

.validation-box ul {
  margin: 4px 0 0;
  padding-left: 18px;
}

.issue-list {
  margin: 0;
  padding-left: 18px;
  color: #8f2a18;
  font-size: 13px;
}

.ledger-list {
  max-height: 120px;
  overflow: auto;
}

.output-area {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 16px;
  margin-top: 16px;
}

.output-card {
  border-radius: 2px;
  padding: 18px;
  min-height: 240px;
}

.output-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 14px;
  margin-bottom: 14px;
}

.room-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  justify-content: flex-end;
}

.dispute-board {
  margin-top: 16px;
}

.dispute-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 12px;
  margin-top: 12px;
}

.dispute-card {
  border: 1px solid rgba(42, 32, 21, 0.16);
  background: rgba(255, 250, 240, 0.74);
  padding: 14px;
  display: grid;
  gap: 8px;
}

.dispute-card strong {
  font-family: var(--hx-font-display);
  font-size: 19px;
  line-height: 1.1;
}

.dispute-card p {
  color: rgba(23, 33, 27, 0.68);
  line-height: 1.45;
}

.room-stage {
  max-height: 620px;
  overflow: auto;
  display: grid;
  gap: 16px;
  padding: 14px;
  border: 1px solid rgba(42, 32, 21, 0.18);
  border-top: 4px double var(--hx-line-strong);
  background:
    radial-gradient(circle at 10% 0%, rgba(139, 58, 34, 0.08), transparent 28%),
    linear-gradient(rgba(42, 32, 21, 0.028) 1px, transparent 1px),
    rgba(255, 250, 240, 0.82);
  background-size: auto, 100% 30px, auto;
}

.room-pocket {
  display: grid;
  gap: 10px;
}

.room-pocket > header {
  position: sticky;
  top: 0;
  z-index: 1;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 10px;
  border: 1px solid rgba(42, 32, 21, 0.16);
  background: rgba(246, 239, 226, 0.96);
  box-shadow: 0 8px 18px rgba(42, 32, 21, 0.06);
}

.room-pocket > header span,
.room-pocket > header strong,
.room-meta span {
  font-family: var(--hx-font-mono);
  font-size: 11px;
  letter-spacing: 0.1em;
  text-transform: uppercase;
}

.room-pocket > header span {
  color: var(--hx-accent);
}

.room-turn {
  display: grid;
  grid-template-columns: 38px minmax(0, 1fr);
  gap: 10px;
  align-items: start;
}

.room-avatar {
  width: 38px;
  height: 38px;
  display: grid;
  place-items: center;
  border-radius: 999px;
  background: #17211b;
  color: #fff9eb;
  font-family: var(--hx-font-mono);
  font-size: 12px;
  font-weight: 900;
  box-shadow: 2px 2px 0 rgba(42, 32, 21, 0.12);
}

.room-bubble {
  min-width: 0;
  max-width: 920px;
  padding: 12px 14px;
  border: 1px solid rgba(42, 32, 21, 0.15);
  background: rgba(255, 253, 247, 0.94);
  box-shadow: 2px 2px 0 rgba(42, 32, 21, 0.05);
}

.room-meta {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 7px;
}

.room-meta strong {
  font-family: var(--hx-font-display);
  font-size: 18px;
  line-height: 1;
  letter-spacing: -0.02em;
  overflow-wrap: anywhere;
}

.room-meta span {
  color: rgba(42, 32, 21, 0.52);
  white-space: nowrap;
}

.room-bubble p {
  color: rgba(42, 32, 21, 0.82);
  font-size: 14px;
  line-height: 1.6;
}

.room-turn.moderator .room-avatar,
.room-turn.mediator .room-avatar {
  background: #8b3a22;
}

.room-turn.research .room-avatar,
.room-turn.data .room-avatar {
  background: #1f6b8f;
}

.room-turn.audit .room-avatar {
  background: #6b3b7a;
}

.room-turn.debate .room-bubble {
  border-left: 5px solid #8b3a22;
  background: #fff8ec;
}

.room-turn.moderator .room-bubble,
.room-turn.mediator .room-bubble {
  background: #f7efe0;
}

.room-turn.research .room-bubble,
.room-turn.data .room-bubble {
  background: #eef6f4;
}

.audit-transcript {
  margin-top: 12px;
}

.audit-transcript summary {
  cursor: pointer;
  width: fit-content;
  border: 1px solid rgba(42, 32, 21, 0.18);
  padding: 8px 10px;
  background: rgba(255, 250, 240, 0.9);
  font-family: var(--hx-font-mono);
  font-size: 12px;
  font-weight: 800;
  letter-spacing: 0.06em;
  text-transform: uppercase;
}

.transcript,
.forecast-summary pre {
  max-height: 420px;
  overflow: auto;
  border-radius: 2px;
  padding: 14px;
  background: #101713;
  color: #eaf2e8;
  font-size: 12px;
  line-height: 1.55;
  white-space: pre-wrap;
}

.transcript-readable {
  display: grid;
  gap: 10px;
  font-family: var(--hx-font-body);
  background:
    linear-gradient(rgba(255, 255, 255, 0.025) 1px, transparent 1px),
    #101713;
  background-size: 100% 30px;
}

.transcript-readable :deep(h3),
.transcript-readable :deep(h4) {
  margin-top: 12px;
  color: #f4ead9;
  font-family: var(--hx-font-display);
  letter-spacing: -0.02em;
}

.transcript-readable :deep(p),
.transcript-readable :deep(li) {
  color: rgba(234, 242, 232, 0.86);
}

.transcript-readable :deep(ul) {
  margin: 0 0 8px;
  padding-left: 18px;
}

.transcript-readable :deep(code) {
  color: #f4d58d;
  background: rgba(255, 255, 255, 0.08);
  padding: 1px 4px;
  border-radius: 2px;
}

.adapter-output {
  max-height: 640px;
  overflow: auto;
  border-radius: 2px;
  border: 1px solid rgba(42, 32, 21, 0.18);
  border-top: 4px double var(--hx-line-strong);
  padding: 18px;
  background:
    linear-gradient(rgba(42, 32, 21, 0.035) 1px, transparent 1px),
    rgba(255, 250, 240, 0.88);
  background-size: 100% 31px;
  color: var(--hx-ink);
  font-family: var(--hx-font-body);
  font-size: 14px;
  line-height: 1.65;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.ask-state-card {
  min-height: 220px;
}

.state-source-chip {
  border: 1px solid rgba(42, 32, 21, 0.16);
  background: rgba(23, 33, 27, 0.08);
  padding: 7px 10px;
  font-family: var(--hx-font-mono);
  font-size: 11px;
  font-weight: 900;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: rgba(42, 32, 21, 0.7);
}

.compact-copy {
  margin-bottom: 12px;
}

.ask-state-form {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 10px;
  margin: 12px 0;
}

.ask-state-form input {
  min-width: 0;
  border: 1px solid rgba(42, 32, 21, 0.22);
  background: rgba(255, 253, 247, 0.95);
  color: var(--hx-ink);
  padding: 12px 14px;
  font: 600 14px/1.4 var(--hx-font-body);
  outline: none;
  box-shadow: inset 2px 2px 0 rgba(42, 32, 21, 0.04);
}

.ask-state-form input:focus {
  border-color: #8b3a22;
  box-shadow: 0 0 0 3px rgba(139, 58, 34, 0.12);
}

.ask-answer {
  display: grid;
  gap: 12px;
  margin-top: 12px;
  border: 1px solid rgba(42, 32, 21, 0.16);
  border-left: 5px solid #1f6b8f;
  background: rgba(255, 253, 247, 0.92);
  padding: 14px;
}

.ask-answer :deep(p),
.ask-answer :deep(li) {
  color: rgba(42, 32, 21, 0.82);
  line-height: 1.55;
}

.ask-answer :deep(ul) {
  margin: 6px 0 0;
  padding-left: 18px;
}

.ask-facts-table {
  margin-top: 0;
}

.source-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.source-row span {
  border: 1px solid rgba(42, 32, 21, 0.12);
  background: rgba(31, 107, 143, 0.08);
  color: #1f4f66;
  padding: 5px 8px;
  font-family: var(--hx-font-mono);
  font-size: 10px;
  font-weight: 900;
  letter-spacing: 0.06em;
  text-transform: uppercase;
}

.forecast-summary {
  display: grid;
  gap: 14px;
}

.winner-strip {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  border-top: 3px double var(--hx-line-strong);
  border-bottom: 1px solid var(--hx-line-strong);
  padding: 10px 0;
  font-family: var(--hx-font-display);
}

.winner-strip span {
  color: rgba(42, 32, 21, 0.62);
}

.forecast-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 14px;
  background: rgba(255, 250, 240, 0.72);
}

.forecast-table th,
.forecast-table td {
  border-bottom: 1px solid rgba(42, 32, 21, 0.16);
  padding: 9px 8px;
  text-align: left;
  vertical-align: top;
  overflow-wrap: anywhere;
}

.forecast-table th {
  font-family: var(--hx-font-mono);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--hx-accent);
}

.adapter-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 14px;
}

.secondary-btn.compact {
  margin-top: 0;
  padding: 8px 10px;
  font-size: 12px;
  text-transform: capitalize;
}

.publish-btn {
  background: var(--hx-ink);
  color: var(--hx-bg-soft);
  box-shadow: 2px 2px 0 rgba(42, 32, 21, 0.12);
}

.published-report {
  margin-top: 18px;
  border: 1px solid var(--hx-line-strong);
  border-top: 6px double var(--hx-line-strong);
  background:
    linear-gradient(rgba(42, 32, 21, 0.03) 1px, transparent 1px),
    rgba(255, 250, 240, 0.96);
  background-size: 100% 34px;
  box-shadow: 4px 4px 0 rgba(42, 32, 21, 0.08);
}

.report-masthead {
  padding: 28px clamp(20px, 4vw, 42px) 20px;
  border-bottom: 4px double var(--hx-line-strong);
}

.report-masthead h2 {
  max-width: 980px;
  font-size: clamp(38px, 5.5vw, 74px);
  letter-spacing: -0.055em;
  line-height: 0.92;
}

.report-masthead p {
  max-width: 980px;
  margin-top: 14px;
  font-size: 17px;
  line-height: 1.55;
  color: rgba(42, 32, 21, 0.72);
}

.report-meta-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  border-bottom: 1px solid var(--hx-line-strong);
}

.report-meta-grid > div {
  display: grid;
  gap: 5px;
  padding: 14px clamp(16px, 3vw, 28px);
  border-right: 1px solid rgba(42, 32, 21, 0.18);
  min-width: 0;
}

.report-meta-grid > div:last-child {
  border-right: 0;
}

.report-meta-grid span,
.section-number {
  font-family: var(--hx-font-mono);
  font-size: 11px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--hx-accent);
}

.report-meta-grid strong {
  overflow-wrap: anywhere;
}

.report-brief-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  border-bottom: 1px solid rgba(42, 32, 21, 0.16);
  background:
    radial-gradient(circle at 10% 10%, rgba(140, 52, 35, 0.08), transparent 28%),
    rgba(246, 239, 226, 0.74);
}

.brief-card {
  display: grid;
  gap: 8px;
  min-height: 150px;
  padding: 18px clamp(16px, 2.2vw, 28px);
  border-right: 1px solid rgba(42, 32, 21, 0.16);
  border-bottom: 1px solid rgba(42, 32, 21, 0.12);
}

.brief-card:nth-child(4n) {
  border-right: 0;
}

.brief-card span,
.visual-card-head span,
.quote-card footer span {
  font-family: var(--hx-font-mono);
  font-size: 11px;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--hx-accent);
}

.brief-card strong {
  font-family: var(--hx-font-display);
  font-size: clamp(26px, 3.4vw, 46px);
  line-height: 0.98;
  letter-spacing: -0.05em;
}

.brief-card p {
  color: rgba(42, 32, 21, 0.64);
  line-height: 1.35;
}

.brief-card.warning {
  background: rgba(255, 232, 202, 0.72);
}

.brief-card.positive {
  background: rgba(224, 244, 214, 0.64);
}

.story-board,
.image-board,
.social-board,
.visual-briefing,
.quote-board {
  padding: 24px clamp(18px, 4vw, 42px);
  border-bottom: 1px solid rgba(42, 32, 21, 0.16);
}

.visual-briefing-head {
  display: flex;
  justify-content: space-between;
  align-items: end;
  gap: 18px;
  margin-bottom: 16px;
}

.visual-briefing-head h3 {
  max-width: 720px;
  font-family: var(--hx-font-display);
  font-size: clamp(26px, 3vw, 42px);
  line-height: 1;
  letter-spacing: -0.045em;
}

.visual-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}

.story-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px;
}

.story-panel {
  min-height: 210px;
  padding: 20px;
  border: 1px solid rgba(42, 32, 21, 0.18);
  background:
    linear-gradient(rgba(255, 250, 240, 0.86), rgba(255, 250, 240, 0.92)),
    radial-gradient(circle at 20% 0%, rgba(139, 58, 34, 0.12), transparent 34%);
  box-shadow: 2px 2px 0 rgba(42, 32, 21, 0.06);
}

.story-panel.headline {
  grid-column: span 2;
  background:
    radial-gradient(circle at 15% 5%, rgba(28, 38, 31, 0.12), transparent 30%),
    rgba(255, 250, 240, 0.94);
}

.story-panel.warning {
  background:
    radial-gradient(circle at 90% 0%, rgba(139, 58, 34, 0.15), transparent 38%),
    rgba(255, 243, 224, 0.9);
}

.story-panel.quote {
  border-left: 6px solid #101713;
}

.story-panel span,
.image-panel span,
.social-card header span {
  font-family: var(--hx-font-mono);
  font-size: 11px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--hx-accent);
}

.story-panel h4 {
  margin: 12px 0 10px;
  font-family: var(--hx-font-display);
  font-size: clamp(26px, 3vw, 42px);
  line-height: 0.98;
  letter-spacing: -0.045em;
}

.story-panel p {
  color: rgba(42, 32, 21, 0.72);
  font-size: 15px;
  line-height: 1.55;
}

.visual-card {
  border: 1px solid rgba(42, 32, 21, 0.18);
  background:
    linear-gradient(135deg, rgba(255, 250, 240, 0.96), rgba(238, 248, 231, 0.7)),
    var(--hx-bg-soft);
  padding: 16px;
  box-shadow: 2px 2px 0 rgba(42, 32, 21, 0.06);
}

.visual-card.disagreement_bars {
  background: linear-gradient(135deg, rgba(255, 250, 240, 0.96), rgba(255, 231, 219, 0.72));
}

.visual-card.debate_flow {
  background: linear-gradient(135deg, rgba(255, 250, 240, 0.96), rgba(226, 240, 255, 0.72));
}

.visual-card-head {
  display: flex;
  justify-content: space-between;
  align-items: start;
  gap: 12px;
  margin-bottom: 14px;
}

.visual-card-head h4 {
  font-family: var(--hx-font-display);
  font-size: 22px;
  line-height: 1.04;
  letter-spacing: -0.03em;
}

.bar-stack {
  display: grid;
  gap: 10px;
}

.bar-label {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 5px;
  font-size: 13px;
}

.bar-label span {
  overflow-wrap: anywhere;
}

.bar-label strong {
  white-space: nowrap;
  font-family: var(--hx-font-mono);
}

.bar-track {
  height: 10px;
  background: rgba(42, 32, 21, 0.1);
  border: 1px solid rgba(42, 32, 21, 0.12);
}

.bar-fill {
  height: 100%;
  min-width: 8px;
  background: linear-gradient(90deg, #17211b, #8b3a22);
}

.image-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 14px;
}

.image-panel {
  overflow: hidden;
  border: 1px solid rgba(42, 32, 21, 0.18);
  background: rgba(255, 250, 240, 0.92);
  box-shadow: 2px 2px 0 rgba(42, 32, 21, 0.06);
}

.image-art {
  position: relative;
  height: 160px;
  overflow: hidden;
  background:
    radial-gradient(circle at 25% 25%, rgba(139, 58, 34, 0.25), transparent 18%),
    radial-gradient(circle at 75% 30%, rgba(21, 72, 112, 0.22), transparent 20%),
    linear-gradient(135deg, #fbf3e4, #e7efe2);
  border-bottom: 1px solid rgba(42, 32, 21, 0.16);
}

.image-art span {
  position: absolute;
  display: block;
  border: 1px solid rgba(42, 32, 21, 0.22);
  background: rgba(255, 250, 240, 0.6);
  transform: rotate(-8deg);
}

.image-art span:nth-child(1) {
  width: 92px;
  height: 60px;
  left: 18px;
  top: 30px;
}

.image-art span:nth-child(2) {
  width: 120px;
  height: 12px;
  right: 18px;
  bottom: 34px;
}

.image-art span:nth-child(3) {
  width: 44px;
  height: 44px;
  right: 40px;
  top: 35px;
  border-radius: 999px;
}

.image-panel.fork .image-art {
  background:
    linear-gradient(45deg, transparent 47%, rgba(16, 23, 19, 0.28) 48%, rgba(16, 23, 19, 0.28) 52%, transparent 53%),
    linear-gradient(-45deg, transparent 47%, rgba(139, 58, 34, 0.26) 48%, rgba(139, 58, 34, 0.26) 52%, transparent 53%),
    #f7efe0;
}

.image-panel.map .image-art {
  background:
    radial-gradient(circle at 30% 40%, rgba(16, 23, 19, 0.3) 0 5px, transparent 6px),
    radial-gradient(circle at 65% 35%, rgba(139, 58, 34, 0.28) 0 5px, transparent 6px),
    radial-gradient(circle at 48% 68%, rgba(31, 107, 154, 0.28) 0 5px, transparent 6px),
    repeating-linear-gradient(90deg, rgba(42, 32, 21, 0.05) 0 1px, transparent 1px 30px),
    #fbf6ea;
}

.image-panel > div:last-child {
  padding: 16px;
}

.image-panel h4 {
  margin: 7px 0;
  font-family: var(--hx-font-display);
  font-size: 24px;
  line-height: 1;
  letter-spacing: -0.035em;
}

.image-panel p {
  color: rgba(42, 32, 21, 0.68);
  line-height: 1.45;
}

.quote-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}

.quote-card {
  position: relative;
  padding: 20px;
  border: 1px solid rgba(42, 32, 21, 0.2);
  border-left: 5px solid #8b3a22;
  background:
    linear-gradient(rgba(42, 32, 21, 0.03) 1px, transparent 1px),
    rgba(255, 250, 240, 0.9);
  background-size: 100% 28px;
}

.quote-card p {
  font-family: var(--hx-font-display);
  font-size: clamp(20px, 2vw, 30px);
  line-height: 1.1;
  letter-spacing: -0.025em;
}

.quote-card footer {
  display: grid;
  gap: 4px;
  margin-top: 16px;
}

.social-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 14px;
}

.social-card {
  min-height: 190px;
  padding: 18px;
  border: 1px solid rgba(42, 32, 21, 0.2);
  border-radius: 22px;
  background: #101713;
  color: #fff9eb;
  box-shadow: 0 16px 35px rgba(16, 23, 19, 0.12);
}

.social-card header {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 18px;
}

.social-card header strong {
  font-family: var(--hx-font-mono);
  color: #fff9eb;
}

.social-card header span {
  color: rgba(255, 249, 235, 0.58);
}

.social-card p {
  font-family: var(--hx-font-display);
  font-size: clamp(20px, 2vw, 28px);
  line-height: 1.08;
  letter-spacing: -0.025em;
}

.report-toc {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  padding: 14px clamp(16px, 3vw, 28px);
  border-bottom: 1px solid rgba(42, 32, 21, 0.16);
}

.report-toc a {
  color: var(--hx-ink);
  text-decoration: none;
  border: 1px solid rgba(42, 32, 21, 0.18);
  background: rgba(255, 250, 240, 0.78);
  padding: 7px 9px;
  font-family: var(--hx-font-mono);
  font-size: 11px;
  letter-spacing: 0.04em;
}

.report-paper {
  padding: clamp(18px, 4vw, 42px);
}

.report-section {
  display: grid;
  grid-template-columns: 54px minmax(0, 1fr);
  gap: 0 18px;
  padding: 26px 0;
  border-bottom: 1px solid rgba(42, 32, 21, 0.16);
}

.report-section:last-child {
  border-bottom: 0;
}

.report-section h3 {
  font-family: var(--hx-font-display);
  font-size: clamp(25px, 3vw, 38px);
  line-height: 1.02;
  letter-spacing: -0.035em;
  margin-bottom: 14px;
}

.report-section-body {
  grid-column: 2;
  min-width: 0;
  font-size: 15px;
  line-height: 1.7;
  color: rgba(42, 32, 21, 0.86);
}

.report-section-body :deep(p) {
  margin: 0 0 14px;
}

.report-section-body :deep(ul) {
  margin: 0 0 16px;
  padding-left: 20px;
}

.report-section-body :deep(li) {
  margin: 6px 0;
}

.report-section-body :deep(code) {
  padding: 1px 5px;
  border-radius: 2px;
  background: rgba(42, 32, 21, 0.08);
  font-family: var(--hx-font-mono);
  font-size: 0.92em;
}

.report-section-body :deep(.markdown-code) {
  overflow: auto;
  padding: 14px;
  background: #101713;
  color: #eaf2e8;
  border-radius: 2px;
  font-size: 12px;
}

.report-section-body :deep(.markdown-table-wrap) {
  overflow-x: auto;
  margin: 12px 0 18px;
  border: 1px solid rgba(42, 32, 21, 0.2);
  background: rgba(255, 250, 240, 0.84);
}

.report-section-body :deep(.markdown-table) {
  width: 100%;
  border-collapse: collapse;
  min-width: 680px;
  font-size: 13px;
}

.report-section-body :deep(.markdown-table th),
.report-section-body :deep(.markdown-table td) {
  border-bottom: 1px solid rgba(42, 32, 21, 0.14);
  border-right: 1px solid rgba(42, 32, 21, 0.1);
  padding: 9px 10px;
  text-align: left;
  vertical-align: top;
  overflow-wrap: anywhere;
}

.report-section-body :deep(.markdown-table th) {
  background: rgba(42, 32, 21, 0.06);
  color: var(--hx-accent);
  font-family: var(--hx-font-mono);
  font-size: 11px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

@media (max-width: 1100px) {
  .workbench-hero {
    flex-direction: column;
  }

  .report-meta-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .report-brief-grid,
  .story-grid,
  .visual-grid,
  .image-grid,
  .quote-grid,
  .social-grid {
    grid-template-columns: 1fr;
  }

  .story-panel.headline {
    grid-column: auto;
  }

  .brief-card {
    border-right: 0;
  }

  .report-section {
    grid-template-columns: 1fr;
  }

  .report-section-body {
    grid-column: 1;
  }
}
</style>
