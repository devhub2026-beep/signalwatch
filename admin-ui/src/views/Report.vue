<template>
  <div>
    <h2 class="page-title">报表</h2>
    <el-card shadow="never">
      <div class="toolbar">
        <el-select v-model="days" style="width: 140px" @change="load">
          <el-option :value="1" label="近 1 天" />
          <el-option :value="7" label="近 7 天" />
          <el-option :value="30" label="近 30 天" />
        </el-select>
        <el-button @click="load">刷新</el-button>
      </div>
      <el-table :data="bySource" size="small">
        <el-table-column prop="source" label="来源" />
        <el-table-column prop="items" label="条目" width="100" />
        <el-table-column label="最近一条">
          <template #default="{ row }">{{ fmtTime(row.latest) }}</template>
        </el-table-column>
      </el-table>
      <h3 class="sub">公司 Top 10</h3>
      <el-table :data="top" size="small">
        <el-table-column prop="sec_code" label="代码" width="120" />
        <el-table-column prop="sec_name" label="公司" />
        <el-table-column prop="items" label="条数" width="100" />
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import http from '../api'
import { fmtTime } from '../utils/format'

const days = ref(7)
const bySource = ref([])
const top = ref([])

async function load() {
  const r = await http.get('/report', { params: { days: days.value } })
  bySource.value = r.data.by_source || []
  top.value = r.data.top_companies || []
}

onMounted(load)
</script>

<style scoped>
.page-title { margin: 0 0 16px; font-size: 18px; }
.toolbar { display: flex; gap: 8px; margin-bottom: 16px; }
.sub { margin: 20px 0 8px; font-size: 15px; }
</style>
