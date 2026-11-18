<template>
  <div>
    <h2 class="page-title">条目管理</h2>
    <el-card shadow="never">
      <div class="toolbar">
        <el-input v-model="q" placeholder="标题 / 公司 / 代码" clearable style="width: 260px" @keyup.enter="load(1)" />
        <el-button type="primary" @click="load(1)">搜索</el-button>
        <el-button @click="batch('online')">上架</el-button>
        <el-button @click="batch('offline')">下架</el-button>
        <el-button type="danger" @click="batchDel">删除</el-button>
        <a class="export" href="/api/admin/items.csv" target="_blank">导出 CSV</a>
      </div>

      <el-table v-if="!narrow" :data="items" size="small" v-loading="loading" @selection-change="(v) => (selected = v)">
        <el-table-column type="selection" width="40" />
        <el-table-column label="时间" width="150">
          <template #default="{ row }">{{ fmtTime(row.published_at) }}</template>
        </el-table-column>
        <el-table-column prop="source" label="来源" width="100" />
        <el-table-column label="状态" width="120">
          <template #default="{ row }">
            <el-tag :type="row.state === 'offline' ? 'info' : 'success'" size="small">
              {{ row.state === 'offline' ? '下架' : '在架' }}{{ row.pinned ? ' · 置顶' : '' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="title" label="标题" show-overflow-tooltip />
        <el-table-column label="操作" width="200" fixed="right">
          <template #default="{ row }">
            <el-button size="small" text type="primary" @click="pin(row)">{{ row.pinned ? '取消置顶' : '置顶' }}</el-button>
            <el-button size="small" text @click="history(row)">历史</el-button>
            <el-button v-if="row.sec_code" size="small" text @click="timeline(row)">时间线</el-button>
          </template>
        </el-table-column>
      </el-table>


      <!-- 窄屏：表格 5 列在 390px 上会把标题挤没，换成卡片 -->
      <div v-else v-loading="loading" class="cards">
        <div v-for="row in items" :key="row.id" class="card">
          <div class="card-head">
            <el-checkbox :model-value="isPicked(row.id)" @change="toggle(row)" />
            <div class="card-title">{{ row.title }}</div>
          </div>
          <div class="card-meta">
            <span>{{ fmtTime(row.published_at) }}</span>
            <span class="dot">·</span>
            <span>{{ row.source }}</span>
            <el-tag :type="row.state === 'offline' ? 'info' : 'success'" size="small">
              {{ row.state === 'offline' ? '已下架' : '在架' }}
            </el-tag>
            <el-tag v-if="row.pinned" type="warning" size="small">置顶</el-tag>
          </div>
          <div class="card-actions">
            <el-button size="small" text type="primary" @click="pin(row)">
              {{ row.pinned ? '取消置顶' : '置顶' }}
            </el-button>
            <el-button size="small" text type="primary" @click="history(row)">历史</el-button>
            <el-button size="small" text type="primary" @click="timeline(row)">时间线</el-button>
          </div>
        </div>
        <el-empty v-if="!items.length && !loading" description="没有条目" />
      </div>

      <div class="pager">
        <el-pagination background layout="prev, pager, next, total" :total="total" :page-size="50"
                       :current-page="page" @current-change="load" />
      </div>
    </el-card>

    <el-dialog v-model="histDialog" title="改动历史" width="60%">
      <div v-for="v in histVersions" :key="v" class="hist-line">{{ v }}</div>
      <div class="hist-diff">{{ histDiff }}</div>
    </el-dialog>
    <el-dialog v-model="tlDialog" :title="tlTitle" width="60%">
      <div v-for="(x, i) in tlRows" :key="i" class="hist-line">{{ fmtTime(x.published_at) }}  {{ x.title }}</div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import http from '../api'
import { fmtTime } from '../utils/format'

const q = ref('')
const items = ref([])
const total = ref(0)
const page = ref(1)
const pages = ref(1)
const loading = ref(false)
const selected = ref([])

// 窄屏换卡片布局：表格 5 列在手机上会把「标题」这列挤到看不见
const narrow = ref(false)
{
  const mq = window.matchMedia('(max-width: 820px)')
  narrow.value = mq.matches
  mq.addEventListener('change', (e) => { narrow.value = e.matches })
}

function isPicked(id) {
  return selected.value.some((r) => r.id === id)
}

function toggle(row) {
  if (isPicked(row.id)) selected.value = selected.value.filter((r) => r.id !== row.id)
  else selected.value = [...selected.value, row]
}

const histDialog = ref(false)
const histVersions = ref([])
const histDiff = ref('')
const tlDialog = ref(false)
const tlTitle = ref('')
const tlRows = ref([])

async function load(p = 1) {
  page.value = p
  loading.value = true
  try {
    const r = await http.get('/admin/items', { params: { q: q.value || undefined, page: p, size: 50 } })
    items.value = r.data.items
    total.value = r.data.total
    pages.value = r.data.pages
  } catch (e) {
    ElMessage.error(e.userMessage)
  } finally {
    loading.value = false
  }
}

function pickedIds() {
  return selected.value.map((r) => r.id)
}

async function batch(state) {
  const ids = pickedIds()
  if (!ids.length) return ElMessage.warning('先点选几行')
  await http.post('/admin/items/state', { ids, state })
  ElMessage.success('已更新')
  load(page.value)
}

async function batchDel() {
  const ids = pickedIds()
  if (!ids.length) return ElMessage.warning('先点选几行')
  await ElMessageBox.confirm(`删掉这 ${ids.length} 条？`, '确认', { type: 'warning' })
  await http.post('/admin/items/delete', { ids })
  ElMessage.success('已删除')
  load(page.value)
}

async function pin(row) {
  await http.post(`/admin/items/${row.id}/pin`)
  load(page.value)
}

async function history(row) {
  const r = await http.get(`/admin/items/${row.id}/history`)
  histVersions.value = (r.data.history || []).map((x) => `v${x.version} · ${fmtTime(x.at)}`)
  histDiff.value = (r.data.diff || []).join('\n') || '相邻版本没有差异'
  histDialog.value = true
}

async function timeline(row) {
  const r = await http.get('/admin/timeline', { params: { code: row.sec_code, limit: 60 } })
  tlTitle.value = `${row.sec_code} 时间线`
  tlRows.value = r.data.items || r.data.rows || []
  tlDialog.value = true
}

onMounted(() => load(1))
</script>

<style scoped>
.page-title { margin: 0 0 16px; font-size: 18px; }
.toolbar { display: flex; gap: 8px; margin-bottom: 16px; flex-wrap: wrap; align-items: center; }
.export { margin-left: auto; font-size: 13px; }
.pager { margin-top: 16px; display: flex; justify-content: flex-end; }
.hist-line { padding: 4px 0; border-bottom: 1px solid #f0f0f0; font-size: 13px; }
.hist-diff { margin-top: 12px; white-space: pre-wrap; font-size: 13px; color: #6b7280; max-height: 300px; overflow: auto; }

.cards { display: flex; flex-direction: column; gap: 10px; }
.card { border: 1px solid #e5e7eb; border-radius: 10px; padding: 10px 12px; background: #fff; }
.card-head { display: flex; gap: 8px; align-items: flex-start; }
.card-title { font-size: 14px; line-height: 1.45; font-weight: 600; color: #111827; flex: 1; }
.card-meta { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin: 6px 0 2px 26px;
             color: #6b7280; font-size: 12px; }
.card-meta .dot { color: #d1d5db; }
.card-actions { display: flex; gap: 2px; margin-left: 22px; }
@media (max-width: 820px) {
  .toolbar { flex-wrap: wrap; gap: 8px; }
}
</style>
