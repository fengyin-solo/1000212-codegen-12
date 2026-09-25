<template>
  <section class="page" data-module="retention">
    <header class="page-head">
      <div>
        <h2>拍摄素材归档保留规则</h2>
        <p class="page-desc">按素材类型、拍摄日期与归档状态判定保留期限；类型不一致或临近到期进入待处理范围，执行前先核对影响口径。</p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" :disabled="loading" @click="() => loadPreview()">刷新口径</button>
        <button class="btn primary" type="button" :disabled="loading || executed" @click="runArchive">
          {{ executed ? '本轮已执行，可刷新后再来一轮' : '按上述口径执行归档' }}
        </button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in cards" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <section class="rule-box">
      <h3>保留期限规则（基准日 {{ preview?.基准日期 ?? '—' }}，临近窗口 {{ preview?.临近窗口天数 ?? windowDays }} 天）</h3>
      <table class="data-table">
        <thead>
          <tr>
            <th>规则</th><th>素材类型</th><th>归档状态</th><th>保留天数</th><th>大小上限</th><th>说明</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="rule in rules" :key="rule.id">
            <td>{{ rule.id }}</td>
            <td>{{ rule.素材类型 }}</td>
            <td>{{ rule.归档状态 ?? '不限' }}</td>
            <td>{{ rule.保留天数 }} 天</td>
            <td>{{ rule.大小上限GB != null ? `${rule.大小上限GB}GB（超限禁止归档）` : '无自动归档' }}</td>
            <td>{{ rule.说明 }}</td>
          </tr>
        </tbody>
      </table>
      <h3>规则冲突时的优先级</h3>
      <ol class="priority-list">
        <li v-for="note in priorityNotes" :key="note">{{ note.replace(/^\d+\.\s*/, '') }}</li>
      </ol>
    </section>

    <section v-if="errorMessage" class="error-banner">{{ errorMessage }}</section>

    <section class="run-box">
      <h3>执行前影响口径{{ executed ? '（已按该口径执行，结果见下表）' : '（只读预览，尚未改动任何数据）' }}</h3>
      <table class="data-table">
        <thead>
          <tr>
            <th>素材编号</th><th>素材类型</th><th>拍摄日期</th><th>文件大小</th>
            <th>当前状态</th><th>命中规则</th><th>到期日</th><th>距到期</th>
            <th>判定口径</th><th>执行结果</th><th>原因 / 说明</th><th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="String(row.id)" :class="`tone-${resultOf(row).处理结果 ?? row.判定}`">
            <td>{{ row.素材编号 }}</td>
            <td>{{ row.素材类型 }}</td>
            <td>{{ row.拍摄日期 }}</td>
            <td>{{ row.文件大小 }}</td>
            <td>{{ row.当前状态 }}</td>
            <td>{{ row.命中规则 }}</td>
            <td>{{ row.到期日 ?? '—' }}</td>
            <td>{{ row.距到期天数 == null ? '—' : `${row.距到期天数} 天` }}</td>
            <td>{{ row.判定说明 }}</td>
            <td>{{ resultLabel(row) }}</td>
            <td class="reason-cell">{{ resultOf(row).说明 ?? row.原因?.join('；') }}</td>
            <td class="row-actions">
              <button
                v-if="resultOf(row).处理结果 === 'failed'"
                class="link"
                type="button"
                @click="retry(row.id)"
              >重试</button>
              <span v-else>—</span>
            </td>
          </tr>
          <tr v-if="!rows.length">
            <td colspan="12" class="empty-state">暂无素材数据</td>
          </tr>
        </tbody>
      </table>
    </section>

    <footer class="page-foot">
      <span>待处理 {{ preview?.汇总?.待处理合计 ?? 0 }} 条：待归档 {{ preview?.汇总?.待归档 ?? 0 }}、禁止归档 {{ preview?.汇总?.禁止归档 ?? 0 }}、待人工确认 {{ preview?.汇总?.待人工确认 ?? 0 }}</span>
      <span v-if="runSummary" class="success-text">
        上轮执行：归档 {{ runSummary.archived }} · 失败 {{ runSummary.failed }} · 拦截 {{ runSummary.blocked }} ·
        人工 {{ runSummary.manual }} · 跳过 {{ runSummary.skipped }} · 未到期 {{ runSummary.normal }}
      </span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

interface Rule {
  id: string
  素材类型: string | null
  归档状态: string | null
  保留天数: number
  大小上限GB: number | null
  说明: string
}

interface Row {
  id: number
  素材编号: string
  素材类型: string
  拍摄日期: string
  文件大小: string
  当前状态: string
  命中规则: string
  到期日: string | null
  距到期天数: number | null
  判定: string
  判定说明: string
  原因: string[]
}

interface RunItem {
  id: number
  判定: string
  处理结果?: string
  说明?: string
}

interface Preview {
  基准日期: string
  临近窗口天数: number
  规则: Rule[]
  兜底规则: Rule
  优先级说明: string[]
  汇总: Record<string, number>
  明细: Row[]
}

const ENDPOINT = '/api/retention'
const preview = ref<Preview | null>(null)
const runItems = ref<RunItem[]>([])
const runSummary = ref<Record<string, number> | null>(null)
const executed = ref(false)
const loading = ref(false)
const errorMessage = ref('')

const windowDays = 7
const rules = computed<Rule[]>(() => preview.value ? [...preview.value.规则, preview.value.兜底规则] : [])
const priorityNotes = computed(() => preview.value?.优先级说明 ?? [])
const rows = computed<Row[]>(() => preview.value?.明细 ?? [])

const cards = computed(() => {
  const s = preview.value?.汇总
  return [
    { label: '素材总数', value: s?.素材总数 ?? 0 },
    { label: '待归档（临近到期）', value: s?.待归档 ?? 0 },
    { label: '禁止归档（超上限/丢失）', value: s?.禁止归档 ?? 0 },
    { label: '待人工确认（类型不一致）', value: s?.待人工确认 ?? 0 },
    { label: '已归档跳过', value: s?.已归档跳过 ?? 0 },
  ]
})

function resultOf(row: Row): RunItem {
  return runItems.value.find(item => item.id === row.id) ?? { id: row.id, 判定: row.判定 }
}

const RESULT_LABEL: Record<string, string> = {
  archived: '已归档',
  failed: '失败可重试',
  blocked: '已拦截未归档',
  manual: '转人工确认',
  skipped: '跳过（不重复处理）',
  normal: '未到期未处理',
}

function resultLabel(row: Row): string {
  const item = resultOf(row)
  return item.处理结果 ? RESULT_LABEL[item.处理结果] ?? item.处理结果 : '待执行'
}

async function loadPreview(keepRuns = false) {
  loading.value = true
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/preview`)
    if (!response.ok) throw new Error('影响口径预览读取失败')
    preview.value = await response.json()
    // 默认按新一轮预览清空上轮结果；执行/重试后刷新时保留结果，避免口径与结果错位。
    if (!keepRuns) {
      runItems.value = []
      runSummary.value = null
      executed.value = false
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '影响口径读取失败'
  } finally {
    loading.value = false
  }
}

async function runArchive() {
  loading.value = true
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/run`, { method: 'POST' })
    if (!response.ok) throw new Error('归档执行失败，请稍后重试')
    const payload = await response.json()
    runItems.value = payload.结果 ?? []
    runSummary.value = payload.汇总
    executed.value = true
    // 执行后重新拉取同一口径的判定，列表与归档结果保持同一标准。
    await loadPreview(true)
    executed.value = true
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '归档执行失败'
  } finally {
    loading.value = false
  }
}

async function retry(id: number) {
  loading.value = true
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${id}/retry`, { method: 'POST' })
    const payload = await response.json()
    if (!response.ok || !payload.ok) {
      errorMessage.value = payload.message ?? '重试失败，可再次重试'
    }
    // 无论成功失败都刷新口径与结果，保证页面展示的是最新判定。
    await loadPreview(true)
    if (payload.ok) executed.value = true
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '重试请求未送达'
  } finally {
    loading.value = false
  }
}

onMounted(loadPreview)
</script>

<style scoped>
.rule-box,
.run-box {
  margin-top: 16px;
  padding: 16px;
  border: 1px solid #e3e6ee;
  border-radius: 10px;
  background: #fff;
}

.rule-box h3,
.run-box h3 {
  margin: 0 0 12px;
  font-size: 15px;
}

.priority-list {
  margin: 8px 0 0;
  padding-left: 20px;
  color: #4a5063;
  line-height: 1.8;
}

.reason-cell {
  max-width: 260px;
  color: #5a6072;
}

.tone-archived td {
  background: #f0f9f0;
}

.tone-failed td,
.tone-blocked td {
  background: #fdf3f0;
}

.tone-manual td {
  background: #fdf8ec;
}

.tone-skipped td {
  color: #8a8f9c;
}

.success-text {
  color: #2f8f46;
}

.error-banner {
  margin-top: 12px;
  padding: 10px 14px;
  border: 1px solid #f0b8ab;
  border-radius: 8px;
  background: #fdf3f0;
  color: #b2421d;
}
</style>
