export function fmtTime(ms) {
  if (!ms) return '-'
  const d = new Date(ms)
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

export function fmtRole(role) {
  return { admin: '管理员', analyst: '分析员', auditor: '审计员', viewer: '只读' }[role] || role || '-'
}
