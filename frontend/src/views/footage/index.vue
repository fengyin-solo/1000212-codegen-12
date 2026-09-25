<template>
  <section class="page" data-module="footage">
    <header class="page-head">
      <div>
        <h2>素材管理管理</h2>
        <p class="page-desc">维护拍摄素材，围绕素材编号、素材类型、拍摄日期、文件大小做登记、筛选与状态流转。</p>
      </div>
      <div class="page-actions">
        <RouterLink class="btn" to="/retention">归档保留规则</RouterLink>
        <button class="btn primary" type="button" @click="openCreate">登记拍摄素材</button>
        <button class="btn" type="button" @click="exportRows">导出素材管理清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>命中规则</th>
          <th>保留到期日</th>
          <th>保留判定</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td>{{ row['命中规则'] ?? '—' }}</td>
          <td>{{ row['保留到期日'] ?? '—' }}</td>
          <td :class="retentionTone(String(row['保留判定'] ?? ''))">{{ row['保留判定'] ?? '—' }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
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
          <td :colspan="columns.length + 4" class="empty-state">暂无素材管理数据，可先登记拍摄素材</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条素材管理记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

const ENDPOINT = '/api/footage'
const columns = ["素材编号", "素材类型", "拍摄日期", "文件大小", "存储介质", "转码格式", "备份位置", "素材状态"]
const actions = ["提交转码", "确认归档", "登记丢失"]
const statuses = ["待转码", "转码中", "已归档", "已丢失"]
const stats = [{"label": "待转码素材", "value": 0}, {"label": "已归档素材", "value": 0}, {"label": "存储占用", "value": 0}]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function retentionTone(text: string): string {
  if (text.includes('待归档')) return 'retention-archive'
  if (text.includes('禁止归档')) return 'retention-block'
  if (text.includes('人工确认')) return 'retention-manual'
  if (text.includes('已归档')) return 'retention-skip'
  return ''
}

function openCreate() {
  errorMessage.value = '拍摄素材登记入口尚未接入审批流'
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    if (!response.ok) {
      throw new Error('素材管理动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '素材管理操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('拍摄素材列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '素材管理列表读取失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.retention-archive {
  color: #2f8f46;
}

.retention-block {
  color: #b2421d;
}

.retention-manual {
  color: #a8731a;
}

.retention-skip {
  color: #8a8f9c;
}
</style>
