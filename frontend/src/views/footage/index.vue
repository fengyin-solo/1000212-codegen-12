<template>
  <section class="page" data-module="footage">
    <header class="page-head">
      <div>
        <h2>素材管理管理 · 归档保留规则</h2>
        <p class="page-desc">
          按素材类型、拍摄日期与归档状态判定保留期限；类型不一致或临近到期（{{ expiringSoonDays }} 天内，含已逾期）进入待处理范围，
          执行归档前先看影响口径，超过类型大小上限的素材不允许归档，处理失败可重试，已归档内容不重复处理。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" @click="toggleRules">查看保留规则</button>
        <button class="btn primary" type="button" @click="openImpact">归档处理（先看影响口径）</button>
        <button class="btn" type="button" @click="exportRows">导出素材管理清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value" :class="item.cls">{{ item.value }}</strong>
      </article>
    </div>

    <!-- 保留规则与优先级 -->
    <div v-if="showRules" class="rule-panel">
      <h3>保留规则与冲突优先级</h3>
      <p class="rule-priority">{{ rulePriority }}</p>
      <table class="data-table">
        <thead>
          <tr>
            <th>素材类型</th>
            <th>适用归档状态</th>
            <th>保留天数</th>
            <th>单文件大小上限</th>
            <th>说明</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(rule, idx) in rules" :key="idx">
            <td>{{ rule['素材类型'] }}</td>
            <td>{{ rule['归档状态'] || '不限状态' }}</td>
            <td>{{ rule['保留天数'] }} 天</td>
            <td>{{ rule['大小上限GB'] }}GB</td>
            <td>{{ rule['说明'] }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>素材编号</span>
        <input v-model="keyword" placeholder="按素材编号检索" />
      </label>
      <label class="filter-item">
        <span>归档状态</span>
        <select v-model="statusFilter">
          <option value="">全部状态</option>
          <option v-for="s in statuses" :key="s" :value="s">{{ s }}</option>
        </select>
      </label>
      <label class="filter-item">
        <span>处理范围</span>
        <select v-model="scopeFilter">
          <option value="">全部素材</option>
          <option value="pending">只看归档待处理</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>保留到期日</th>
          <th>剩余天数</th>
          <th>待处理原因 / 拦截原因</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :class="rowClass(row)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td>{{ row['到期日'] ?? '—' }}</td>
          <td>
            <span v-if="row['剩余天数'] === null || row['剩余天数'] === undefined">—</span>
            <span v-else :class="daysClass(row)">{{ formatDays(row['剩余天数']) }}</span>
          </td>
          <td class="reason-cell">
            <span v-if="row.scope === 'skip'" class="tag muted">不处理：已{{ row.status === '已丢失' ? '丢失' : '归档' }}</span>
            <template v-else>
              <span v-if="row.scope_reason" :class="['tag', row.scope === 'block' ? 'warn' : 'info']">{{ row.scope_reason }}</span>
              <span v-if="row.block_reason && row.block_reason !== row.scope_reason" class="tag danger">{{ row.block_reason }}</span>
              <span v-if="row.archive_error" class="tag danger">上次失败：{{ row.archive_error }}</span>
              <span v-if="!row.scope_reason && !row.block_reason" class="tag muted">保留期内，暂不处理</span>
            </template>
          </td>
          <td class="row-actions">
            <button
              v-for="action in actionsFor(row)"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 4" class="empty-state">暂无符合条件的拍摄素材</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条素材记录（列表与归档结果使用同一判定口径）</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <!-- 执行前影响口径 / 执行结果 -->
    <div v-if="impact" class="impact-panel" :class="{ 'result-mode': archiveResult }">
      <div class="impact-head">
        <h3>{{ archiveResult ? '本次归档处理结果' : '归档执行前 · 影响口径' }}</h3>
        <button class="btn ghost" type="button" @click="closeImpact">关闭</button>
      </div>
      <p class="rule-priority">{{ impact.rule_priority }}</p>
      <p class="impact-meta">
        判定基准日期：{{ impact.as_of }} ｜ 临近到期窗口：{{ impact.expiring_soon_days }} 天
      </p>

      <div class="stat-row">
        <article v-for="(value, label) in impact.summary" :key="label" class="stat-card">
          <span class="stat-label">{{ label }}</span>
          <strong class="stat-value" :class="summaryClass(String(label))">{{ value }}</strong>
        </article>
      </div>

      <table class="data-table impact-table">
        <thead>
          <tr>
            <th>素材编号</th>
            <th>素材类型</th>
            <th>当前状态</th>
            <th>保留到期日</th>
            <th>剩余天数</th>
            <th>本次结论</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in impact.items" :key="String(item.id)">
            <td>{{ item['素材编号'] }}</td>
            <td>{{ item['素材类型'] }}</td>
            <td>{{ item.status }}</td>
            <td>{{ item['到期日'] ?? '—' }}</td>
            <td>{{ item['剩余天数'] === null || item['剩余天数'] === undefined ? '—' : formatDays(item['剩余天数']) }}</td>
            <td>{{ impactConclusion(item) }}</td>
          </tr>
        </tbody>
      </table>

      <div v-if="archiveResult" class="result-groups">
        <div v-for="group in resultGroups" :key="group.key" v-show="group.items.length">
          <h4 :class="group.cls">{{ group.title }}（{{ group.items.length }}）</h4>
          <ul>
            <li v-for="line in group.items" :key="line.id">
              {{ line['素材编号'] }}：{{ line['原因'] || (line['归档日期'] ? `已于 ${line['归档日期']} 归档，保留至 ${line['保留到期日']}（第 ${line['尝试次数']} 次尝试）` : '') }}
            </li>
          </ul>
        </div>
      </div>

      <div v-if="!archiveResult" class="impact-actions">
        <label class="confirm-line">
          <input v-model="confirmed" type="checkbox" />
          我已核对上方影响口径：{{ impact.summary['待处理'] }} 条进入待处理范围，
          其中 {{ impact.summary['其中可归档'] }} 条将归档，{{ impact.summary['其中拦截不归档'] }} 条拦截，
          已归档的 {{ impact.summary['已归档不重复处理'] }} 条不重复处理
        </label>
        <button class="btn primary" type="button" :disabled="!confirmed || archiving" @click="executeArchive">
          {{ archiving ? '归档处理中…' : '确认无误，执行归档' }}
        </button>
      </div>
      <div v-else-if="hasFailed" class="impact-actions">
        <span class="error-text">有 {{ archiveResult.summary['处理失败可重试'] }} 条处理失败，素材未被改动，可直接重试。</span>
        <button class="btn primary" type="button" :disabled="retrying" @click="retryFailed">
          {{ retrying ? '重试中…' : '重试失败素材' }}
        </button>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | boolean | null>
type Impact = {
  as_of: string
  expiring_soon_days: number
  rule_priority: string
  summary: Record<string, number>
  items: Row[]
}
type ArchiveResult = {
  as_of: string
  rule_priority: string
  summary: Record<string, number>
  archived: Array<Record<string, string | number>>
  blocked: Array<Record<string, string | number>>
  failed: Array<Record<string, string | number>>
  skipped: Array<Record<string, string | number>>
}
type Rule = Record<string, string | number>

const ENDPOINT = '/api/footage'
const columns = ['素材编号', '素材类型', '拍摄日期', '文件大小', '存储介质', '转码格式', '备份位置', '素材状态']
const statuses = ['待转码', '转码中', '已归档', '已丢失']

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const keyword = ref('')
const statusFilter = ref('')
const scopeFilter = ref('')

const rules = ref<Rule[]>([])
const rulePriority = ref('')
const expiringSoonDays = ref(15)
const showRules = ref(false)

const impact = ref<Impact | null>(null)
const archiveResult = ref<ArchiveResult | null>(null)
const confirmed = ref(false)
const archiving = ref(false)
const retrying = ref(false)

const stats = computed(() => {
  const pending = rows.value.filter((r) => r.in_scope === true)
  const ready = rows.value.filter((r) => r.scope === 'ready')
  const blocked = rows.value.filter((r) => r.scope === 'block')
  const archived = rows.value.filter((r) => r.status === '已归档')
  const failed = rows.value.filter((r) => r.archive_failed === true)
  return [
    { label: '归档待处理', value: pending.length, cls: blocked.length ? 'stat-warn' : '' },
    { label: '临近到期可归档', value: ready.length, cls: 'stat-info' },
    { label: '拦截 / 失败', value: blocked.length + failed.length, cls: 'stat-danger' },
    { label: '已归档（不重复处理）', value: archived.length, cls: '' },
  ]
})

const resultGroups = computed(() => {
  const r = archiveResult.value
  if (!r) {
    return []
  }
  return [
    { key: 'archived', title: '归档成功', cls: 'ok-text', items: r.archived },
    { key: 'failed', title: '处理失败（可重试）', cls: 'error-text', items: r.failed },
    { key: 'blocked', title: '拦截未归档', cls: 'warn-text', items: r.blocked },
    { key: 'skipped', title: '跳过（已归档不重复处理 / 不在范围）', cls: 'muted-text', items: r.skipped },
  ]
})

const hasFailed = computed(() => (archiveResult.value?.summary['处理失败可重试'] ?? 0) > 0)

function resetFilters() {
  keyword.value = ''
  statusFilter.value = ''
  scopeFilter.value = ''
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

async function toggleRules() {
  showRules.value = !showRules.value
  if (showRules.value && !rules.value.length) {
    const response = await request(`${ENDPOINT}/retention/rules`)
    if (response.ok) {
      const payload = await response.json()
      rules.value = payload.rules ?? []
      rulePriority.value = payload.rule_priority ?? ''
    }
  }
}

function actionsFor(row: Row): string[] {
  const base = ['提交转码', '确认归档', '登记丢失']
  return row.archive_failed ? ['重试归档', ...base] : base
}

function formatDays(value: unknown): string {
  const days = Number(value)
  if (Number.isNaN(days)) return '—'
  if (days < 0) return `已逾期 ${-days} 天`
  return `${days} 天`
}

function daysClass(row: Row): string {
  const days = Number(row['剩余天数'])
  if (days < 0) return 'tag danger'
  if (days <= expiringSoonDays.value) return 'tag warn'
  return 'tag muted'
}

function rowClass(row: Row): Record<string, boolean> {
  return {
    'row-ready': row.scope === 'ready',
    'row-block': row.scope === 'block',
    'row-failed': row.archive_failed === true,
  }
}

function impactConclusion(item: Row): string {
  if (archiveResult.value) {
    const groups = resultGroups.value
    for (const group of groups) {
      const hit = group.items.find((x) => Number(x.id) === Number(item.id))
      if (hit) {
        return String(hit['原因'] || group.title)
      }
    }
  }
  if (item.scope === 'skip') return '不处理：已归档不重复处理 / 已丢失'
  if (item.scope === 'idle') return '保留期未到，不在处理范围'
  const reasons = [String(item.scope_reason || ''), String(item.block_reason || '')]
    .filter((text) => text)
    .filter((text, idx, arr) => arr.indexOf(text) === idx)
  return reasons.length ? reasons.join('；') : '进入待处理范围'
}

function summaryClass(label: string): string {
  if (label.includes('拦截') || label.includes('超过')) return 'stat-danger'
  if (label.includes('可归档')) return 'stat-info'
  if (label.includes('待处理')) return 'stat-warn'
  return ''
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    if (action === '重试归档') {
      await retryFailed()
      return
    }
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    if (!response.ok) {
      throw new Error('素材管理动作未生效，请稍后重试')
    }
    const payload = await response.json()
    if (payload.ok === false) {
      errorMessage.value = payload.message || '素材管理动作被拦截'
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '素材管理操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams()
  if (keyword.value) query.set('keyword', keyword.value)
  if (statusFilter.value) query.set('status', statusFilter.value)
  if (scopeFilter.value) query.set('scope', scopeFilter.value)
  try {
    const response = await request(`${ENDPOINT}?${query.toString()}`)
    if (!response.ok) {
      throw new Error('拍摄素材列表读取失败')
    }
    const payload = await response.json()
    rows.value = (payload.items ?? []) as Row[]
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '素材管理列表读取失败'
  }
}

async function openImpact() {
  errorMessage.value = ''
  archiveResult.value = null
  confirmed.value = false
  try {
    const response = await request(`${ENDPOINT}/retention/impact`)
    if (!response.ok) {
      throw new Error('影响口径读取失败')
    }
    impact.value = (await response.json()) as Impact
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '影响口径读取失败'
  }
}

function closeImpact() {
  impact.value = null
  archiveResult.value = null
  confirmed.value = false
}

async function executeArchive() {
  archiving.value = true
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/retention/archive`, {
      method: 'POST',
      body: JSON.stringify({ values: {} }),
    })
    if (!response.ok) {
      throw new Error('归档处理未生效，请稍后重试')
    }
    archiveResult.value = (await response.json()) as ArchiveResult
    if (impact.value && archiveResult.value) {
      impact.value.summary = archiveResult.value.summary
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '归档处理失败'
  } finally {
    archiving.value = false
  }
}

async function retryFailed() {
  retrying.value = true
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/retention/retry`, {
      method: 'POST',
      body: JSON.stringify({ values: {} }),
    })
    if (!response.ok) {
      throw new Error('重试请求未生效，请稍后再试')
    }
    const result = (await response.json()) as ArchiveResult
    if (archiveResult.value) {
      const retriedIds = new Set(result.archived.map((item) => Number(item.id)))
      archiveResult.value.failed = archiveResult.value.failed.filter(
        (item) => !retriedIds.has(Number(item.id)),
      )
      archiveResult.value.archived.push(...result.archived)
      archiveResult.value.summary['归档成功'] += result.summary['归档成功']
      archiveResult.value.summary['处理失败可重试'] = archiveResult.value.failed.length
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '失败素材重试失败'
  } finally {
    retrying.value = false
  }
}

onMounted(() => {
  void reload()
  void request(`${ENDPOINT}/retention/rules`)
    .then((response) => (response.ok ? response.json() : null))
    .then((payload: { rule_priority?: string; expiring_soon_days?: number; rules?: Rule[] } | null) => {
      if (!payload) return
      rules.value = payload.rules ?? []
      rulePriority.value = payload.rule_priority ?? ''
    })
    .catch(() => undefined)
  void request(`${ENDPOINT}/retention/impact`)
    .then((response) => (response.ok ? response.json() : null))
    .then((payload: Impact | null) => {
      if (!payload) return
      expiringSoonDays.value = payload.expiring_soon_days ?? 15
    })
    .catch(() => undefined)
})
</script>

<style scoped>
.tag {
  display: inline-block;
  padding: 1px 8px;
  border-radius: 10px;
  font-size: 12px;
  line-height: 1.7;
  margin: 1px 4px 1px 0;
}
.tag.info { background: #eff6ff; color: #1d4ed8; border: 1px solid #bfdbfe; }
.tag.warn { background: #fffbeb; color: #b45309; border: 1px solid #fde68a; }
.tag.danger { background: #fef2f2; color: #b42318; border: 1px solid #fecaca; }
.tag.muted { background: #f1f5f9; color: var(--muted); border: 1px solid var(--border); }
.stat-info { color: #1d4ed8; }
.stat-warn { color: #b45309; }
.stat-danger { color: #b42318; }
.row-ready { background: #f8fbff; }
.row-block { background: #fffdf5; }
.row-failed { background: #fef7f7; }
.reason-cell { max-width: 360px; }
.rule-panel,
.impact-panel {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px 14px;
  margin: 0 0 14px;
}
.rule-panel h3,
.impact-panel h3 { margin: 0 0 8px; font-size: 15px; }
.rule-priority {
  font-size: 12.5px;
  color: #475569;
  background: #f8fafc;
  border-left: 3px solid var(--brand);
  padding: 8px 10px;
  margin: 0 0 10px;
}
.impact-meta { font-size: 12px; color: var(--muted); margin: 0 0 10px; }
.impact-head { display: flex; justify-content: space-between; align-items: center; }
.impact-panel { margin-top: 14px; }
.impact-table { margin-top: 8px; }
.impact-actions {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px dashed var(--border);
}
.confirm-line { font-size: 12.5px; color: #334155; }
.confirm-line input { margin-right: 6px; }
.result-groups { margin-top: 12px; font-size: 12.5px; }
.result-groups h4 { margin: 8px 0 4px; font-size: 13px; }
.result-groups ul { margin: 0; padding-left: 18px; }
.ok-text { color: #15803d; }
.warn-text { color: #b45309; }
.error-text { color: #b42318; }
.muted-text { color: var(--muted); }
.filter-item select { padding: 5px 8px; border: 1px solid var(--border); border-radius: 6px; }
</style>
