import assert from 'node:assert/strict'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { build } from 'esbuild'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const bundled = await build({
  entryPoints: [join(root, 'src/renderer/src/components/distributionCalendarDates.ts')],
  bundle: true, write: false, platform: 'node', format: 'esm'
})
const { calendarWindow, localDayKey, postsByDay } = await import(`data:text/javascript;base64,${Buffer.from(bundled.outputFiles[0].contents).toString('base64')}`)
const initialTimezone = process.env.TZ
try {
  for (const timezone of ['UTC', 'Asia/Kolkata', 'America/Los_Angeles']) {
    process.env.TZ = timezone
    for (const today of ['2026-12-20', '2026-03-01', '2026-10-20']) {
      const { start, end, days } = calendarWindow(today)
      assert.equal(days.length, 30)
      assert.equal(localDayKey(start), today)
      assert.equal(new Set(days.map(localDayKey)).size, 30)
      const next = new Date(days[29]); next.setDate(next.getDate() + 1)
      assert.equal(localDayKey(end), localDayKey(next))
      for (let i = 1; i < days.length; i++) {
        const expected = new Date(days[i - 1]); expected.setDate(expected.getDate() + 1)
        assert.equal(localDayKey(days[i]), localDayKey(expected))
      }
    }
    const grouped = postsByDay([
      { id: 'local', status: 'scheduled', scheduled_at: '2026-10-06T20:00:00Z' },
      { id: 'cloud', status: 'scheduled_cloud', scheduled_at: '2026-10-06T20:00:00Z' },
      { id: 'cancelled', status: 'cancelled', scheduled_at: '2026-10-06T20:00:00Z' },
      { id: 'sent', status: 'sent', scheduled_at: '2026-10-06T20:00:00Z' },
      { id: 'invalid', status: 'scheduled', scheduled_at: 'invalid' }
    ])
    const key = timezone === 'Asia/Kolkata' ? '2026-10-07' : '2026-10-06'
    assert.equal(grouped.size, 1)
    assert.deepEqual(grouped.get(key).map((job) => job.id), ['local', 'cloud'])
  }
} finally {
  if (initialTimezone === undefined) delete process.env.TZ
  else process.env.TZ = initialTimezone
}
console.log('Distribution calendar passed rolling-date, year-boundary, DST, local-day, and active-schedule checks.')
