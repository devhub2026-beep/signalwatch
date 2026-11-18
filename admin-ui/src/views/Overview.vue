<template>
  <div>
    <h2 class="page-title">概览 · 近 7 天</h2>
    <div class="kpi-row">
      <el-card shadow="never" class="kpi"><div class="kpi-num">{{ sum7 }}</div><div class="kpi-label">7 天新增</div></el-card>
      <el-card shadow="never" class="kpi"><div class="kpi-num">{{ report.revisions ?? '-' }}</div><div class="kpi-label">修订条目</div></el-card>
      <el-card shadow="never" class="kpi"><div class="kpi-num">{{ report.sources ?? '-' }}</div><div class="kpi-label">在跑源</div></el-card>
    </div>

    <el-card shadow="never" class="mt16">
      <template #header>每日新增 / 修订</template>
      <el-table :data="report.daily || []" size="small">
        <el-table-column prop="day" label="日期" />
        <el-table-column prop="items" label="新增" />
        <el-table-column prop="revised" label="修订" />
      </el-table>
    </el-card>

    <el-card shadow="never" class="mt16">
      <template #header>按来源</template>
      <el-table :data="report.by_source || []" size="small">
        <el-table-column prop="source" label="源" />
        <el-table-column prop="items" label="条目" width="100" />
        <el-table-column label="最近一条">
          <template #default="{ row }">{{ fmtTime(row.latest) }}</template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import http from '../api'
import { fmtTime } from '../utils/format'

const report = ref({})
const sum7 = computed(() => (report.value.daily || []).reduce((a, d) => a + (d.items || 0), 0))

onMounted(async () => {
  const r = await http.get('/report', { params: { days: 7 } })
  report.value = r.data
})
</script>

<style scoped>
.page-title { margin: 0 0 16px; font-size: 18px; }
.kpi-row { display: flex; gap: 16px; flex-wrap: wrap; }
.kpi { flex: 1; min-width: 160px; text-align: center; }
.kpi-num { font-size: 28px; font-weight: 700; }
.kpi-label { color: #6b7280; font-size: 13px; margin-top: 4px; }
.mt16 { margin-top: 16px; }
</style>
