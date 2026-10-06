import type { DistributionJob } from '../api/client'

/** Calendar keys always use the viewer's local day, never a UTC ISO date slice. */
export function localDayKey(date: Date): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}

export function calendarWindow(today: string): { days: Date[]; start: Date; end: Date } {
  const [year, month, day] = today.split('-').map(Number)
  const start = new Date(year, month - 1, day)
  const days = Array.from({ length: 30 }, (_, offset) => new Date(year, month - 1, day + offset))
  return { days, start, end: new Date(year, month - 1, day + 30) }
}

export function postsByDay(jobs: DistributionJob[]): Map<string, DistributionJob[]> {
  const grouped = new Map<string, DistributionJob[]>()
  for (const job of jobs) {
    if (!['scheduled', 'scheduled_cloud'].includes(job.status) || !job.scheduled_at) continue
    const date = new Date(job.scheduled_at)
    if (Number.isNaN(date.getTime())) continue
    const key = localDayKey(date)
    grouped.set(key, [...(grouped.get(key) ?? []), job])
  }
  return grouped
}
