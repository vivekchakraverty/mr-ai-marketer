// Shared deal-state metadata for the Lead Gen Agent's CRM views — the order of the pipeline,
// each state's human label, and a colour used on the board and in the lead detail. Kept in one
// place so the operate (Research) and observe (Analytics) surfaces stay consistent.

export interface StageMeta {
  key: string
  label: string
  color: string
}

// Ordered as the pipeline actually flows. FAILED is terminal and shown apart from the flow.
export const PIPELINE_STAGES: StageMeta[] = [
  { key: 'NEW', label: 'Discovered', color: '#8a8172' },
  { key: 'QUALIFYING', label: 'Qualifying', color: '#b9863f' },
  { key: 'QUALIFIED', label: 'Qualified', color: '#c39b45' },
  { key: 'READY_TO_FIND_EMAIL', label: 'Finding email', color: '#7fa05a' },
  { key: 'FINDING_EMAIL', label: 'Finding email', color: '#7fa05a' },
  { key: 'READY_TO_EMAIL', label: 'Ready to email', color: '#5f9ea0' },
  { key: 'DRAFTED', label: 'Draft ready', color: '#5b8bd0' },
  { key: 'EMAILED', label: 'Emailed', color: '#7a6fc0' },
  { key: 'REPLIED', label: 'Replied', color: '#b062a8' },
  { key: 'COMPLETED', label: 'Completed', color: '#2fa366' }
]

export const FAILED_STAGE: StageMeta = { key: 'FAILED', label: 'Dead', color: '#a34a3a' }

export const ALL_STAGES: StageMeta[] = [...PIPELINE_STAGES, FAILED_STAGE]

export function stageMeta(key: string): StageMeta {
  return ALL_STAGES.find((s) => s.key === key) ?? { key, label: key, color: '#8a8172' }
}
