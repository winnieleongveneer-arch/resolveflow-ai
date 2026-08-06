'use client'

/**
 * Decision Passport — the evidence record for one run.
 *
 * Answers, in plain language, what happened and why: which facts were used,
 * which Operators took part, which policy versions were evaluated, whether a
 * human intervened and what they changed, and what the outcome was.
 *
 * Every field comes from a persisted row. Where nothing was recorded, the
 * page says so rather than filling the gap.
 */

import { useCallback, useEffect, useState } from 'react'
import { useParams } from 'next/navigation'
import { motion } from 'framer-motion'
import toast from 'react-hot-toast'

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Icons } from '@/components/ui/icons'
import { cn } from '@/lib/utils'
import { apiClient } from '@/lib/api-client'

interface Passport {
  run_id: string
  issue_key: string
  auto_run_id: string | null
  status: string
  current_stage: string | null
  trigger_source: string
  started_at: string | null
  completed_at: string | null
  duration_seconds: number | null
  error_message: string | null
  summary: {
    what_happened: string
    why_this_outcome: string
    did_a_human_intervene: string
    operators_involved: string[]
    policies_evaluated: string[]
    external_changes: unknown
    verification: unknown
  }
  timeline: Array<{
    at: string | null
    actor: string
    event: string
    status: string | null
    duration_ms: number | null
    detail: Record<string, unknown> | null
  }>
  facts_used: Array<{ fact: string; value: unknown; supplied_to: string }>
  policy_decisions: Array<{
    policy_key: string
    policy_version: number
    verdict: string
    reasons: string[]
    configuration_at_the_time: Record<string, unknown> | null
    is_simulation: boolean
    evaluated_at: string | null
  }>
  human_decisions: Array<{
    workbench_item_id: string
    request_type: string
    decision: string
    reviewer: string
    notes: string | null
    original_recommendation: Record<string, unknown> | null
    amended_action: Record<string, unknown> | null
    approved_scope: Record<string, unknown> | null
    changed_by_human: boolean
    escalation: string | null
    decided_at: string | null
  }>
  counts: Record<string, number>
}

const VERDICT: Record<string, string> = {
  ALLOW: 'bg-emerald-100 text-emerald-800 ring-emerald-600/20',
  REQUIRE_HUMAN_REVIEW: 'bg-amber-100 text-amber-800 ring-amber-600/20',
  DENY: 'bg-rose-100 text-rose-800 ring-rose-600/20',
}

function Json({ value }: { value: unknown }) {
  if (value === null || value === undefined) {
    return <p className='text-sm italic text-muted-foreground'>Not recorded</p>
  }
  if (typeof value === 'string') return <p className='text-sm'>{value}</p>
  return (
    <pre className='max-h-48 overflow-auto rounded-lg bg-slate-950/95 p-3 font-mono text-xs text-slate-100'>
      {JSON.stringify(value, null, 2)}
    </pre>
  )
}

function Answer({ q, children }: { q: string; children: React.ReactNode }) {
  return (
    <div className='space-y-1'>
      <p className='text-xs font-semibold uppercase tracking-wide text-muted-foreground'>{q}</p>
      <div className='text-sm'>{children}</div>
    </div>
  )
}

export default function PassportPage() {
  const params = useParams<{ runId: string }>()
  const [doc, setDoc] = useState<Passport | null>(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    if (!params?.runId) return
    setLoading(true)
    try {
      setDoc(await apiClient.get<Passport>(`/api/agent/runs/${params.runId}/passport`))
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to load the passport')
    } finally {
      setLoading(false)
    }
  }, [params?.runId])

  useEffect(() => {
    void load()
  }, [load])

  function exportJson() {
    if (!doc) return
    const blob = new Blob([JSON.stringify(doc, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `decision-passport-${doc.issue_key}-${doc.run_id.slice(0, 8)}.json`
    a.click()
    URL.revokeObjectURL(url)
  }

  if (loading && !doc) {
    return <div className='p-6 text-sm text-muted-foreground'>Loading passport…</div>
  }
  if (!doc) {
    return <div className='p-6 text-sm text-muted-foreground'>Run not found.</div>
  }

  return (
    <div className='space-y-6 p-6'>
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        className='flex flex-wrap items-end justify-between gap-4'
      >
        <div>
          <h1 className='text-3xl font-bold tracking-tight text-brand-navy'>
            Decision Passport
          </h1>
          <p className='mt-1 text-sm text-muted-foreground'>
            <span className='font-mono'>{doc.issue_key}</span> · run{' '}
            <span className='font-mono'>{doc.run_id.slice(0, 8)}</span>
            {doc.auto_run_id && (
              <> · Auto run <span className='font-mono'>{doc.auto_run_id.slice(0, 8)}</span></>
            )}
          </p>
        </div>
        <div className='flex gap-2'>
          <Button variant='outline' size='sm' onClick={() => void load()}>
            <Icons.refresh className='mr-1.5 h-4 w-4' /> Refresh
          </Button>
          <Button size='sm' onClick={exportJson}>
            <Icons.download className='mr-1.5 h-4 w-4' /> Export JSON
          </Button>
        </div>
      </motion.div>

      {/* The plain-language answers */}
      <Card className='border-l-4 border-l-brand-cornflower'>
        <CardHeader className='pb-3'>
          <CardTitle className='text-base'>What happened, in plain language</CardTitle>
        </CardHeader>
        <CardContent className='grid gap-5 sm:grid-cols-2'>
          <Answer q='What happened'>{doc.summary.what_happened}</Answer>
          <Answer q='Why this outcome'>{doc.summary.why_this_outcome}</Answer>
          <Answer q='Did a human intervene'>{doc.summary.did_a_human_intervene}</Answer>
          <Answer q='Operators involved'>
            <div className='flex flex-wrap gap-1.5'>
              {doc.summary.operators_involved.map((o) => (
                <span key={o} className='rounded-md bg-slate-100 px-2 py-0.5 font-mono text-[11px]'>
                  {o}
                </span>
              ))}
            </div>
          </Answer>
          <Answer q='Policies evaluated'>
            <div className='flex flex-wrap gap-1.5'>
              {doc.summary.policies_evaluated.map((p) => (
                <span key={p} className='rounded-md bg-slate-100 px-2 py-0.5 font-mono text-[11px]'>
                  {p}
                </span>
              ))}
            </div>
          </Answer>
          <Answer q='External changes made'>
            <Json value={doc.summary.external_changes} />
          </Answer>
        </CardContent>
      </Card>

      {/* Policy decisions */}
      <Card>
        <CardHeader className='pb-3'>
          <CardTitle className='text-base'>Policy decisions</CardTitle>
          <p className='text-xs text-muted-foreground'>
            Each verdict with the configuration that was active when it was made.
          </p>
        </CardHeader>
        <CardContent className='space-y-4'>
          {doc.policy_decisions.length === 0 && (
            <p className='py-4 text-center text-sm text-muted-foreground'>
              No policy was evaluated during this run.
            </p>
          )}
          {doc.policy_decisions.map((d, i) => (
            <div key={i} className='space-y-2 rounded-lg border border-slate-200 p-3'>
              <div className='flex flex-wrap items-center gap-2'>
                <span
                  className={cn(
                    'rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset',
                    VERDICT[d.verdict] || 'bg-slate-100 text-slate-700'
                  )}
                >
                  {d.verdict}
                </span>
                <span className='font-mono text-xs'>
                  {d.policy_key} v{d.policy_version}
                </span>
                {d.is_simulation && (
                  <span className='rounded bg-slate-100 px-1.5 text-[11px]'>simulation</span>
                )}
                <span className='ml-auto text-xs text-muted-foreground'>
                  {d.evaluated_at ? new Date(d.evaluated_at).toLocaleString() : ''}
                </span>
              </div>
              <ul className='list-inside list-disc space-y-0.5 text-sm'>
                {d.reasons.map((r, j) => (
                  <li key={j}>{r}</li>
                ))}
              </ul>
              <details className='text-xs'>
                <summary className='cursor-pointer text-muted-foreground'>
                  Configuration at the time
                </summary>
                <Json value={d.configuration_at_the_time} />
              </details>
            </div>
          ))}
        </CardContent>
      </Card>

      {/* Human decisions */}
      {doc.human_decisions.length > 0 && (
        <Card>
          <CardHeader className='pb-3'>
            <CardTitle className='text-base'>Human decisions</CardTitle>
          </CardHeader>
          <CardContent className='space-y-4'>
            {doc.human_decisions.map((h) => (
              <div key={h.workbench_item_id} className='space-y-3 rounded-lg border border-slate-200 p-3'>
                <div className='flex flex-wrap items-center gap-2 text-sm'>
                  <span className='font-semibold'>{h.decision}</span>
                  {h.reviewer && h.reviewer !== 'Not recorded.' && (
                    <span className='text-muted-foreground'>by {h.reviewer}</span>
                  )}
                  <span className='rounded bg-slate-100 px-1.5 text-[11px]'>{h.request_type}</span>
                  <span className='ml-auto text-xs text-muted-foreground'>
                    {h.decided_at ? new Date(h.decided_at).toLocaleString() : 'pending'}
                  </span>
                </div>
                {h.notes && <p className='text-sm'>{h.notes}</p>}
                {h.changed_by_human && (
                  <div className='grid gap-3 sm:grid-cols-2'>
                    <Answer q='Agent proposed'>
                      <Json value={h.original_recommendation} />
                    </Answer>
                    <Answer q='Human approved'>
                      <Json value={h.approved_scope} />
                    </Answer>
                  </div>
                )}
                {h.escalation && (
                  <p className='text-xs text-muted-foreground'>
                    Escalation: <span className='font-mono'>{h.escalation}</span>
                  </p>
                )}
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      {/* Timeline */}
      <Card>
        <CardHeader className='pb-3'>
          <CardTitle className='text-base'>Timeline</CardTitle>
          <p className='text-xs text-muted-foreground'>
            {doc.counts.events} events · {doc.counts.policy_evaluations} policy evaluations ·{' '}
            {doc.counts.human_reviews} human reviews
            {doc.duration_seconds != null && ` · ${doc.duration_seconds}s total`}
          </p>
        </CardHeader>
        <CardContent>
          <ol className='relative space-y-3 border-l border-slate-200 pl-5'>
            {doc.timeline.map((t, i) => (
              <li key={i} className='relative'>
                <span className='absolute -left-[23px] top-1.5 h-2.5 w-2.5 rounded-full bg-brand-cornflower' />
                <div className='flex flex-wrap items-baseline gap-2'>
                  <span className='font-mono text-xs font-medium'>{t.event}</span>
                  <span className='text-xs text-muted-foreground'>{t.actor}</span>
                  {t.duration_ms != null && (
                    <span className='text-xs text-muted-foreground'>{t.duration_ms} ms</span>
                  )}
                  <span className='ml-auto text-xs text-muted-foreground'>
                    {t.at ? new Date(t.at).toLocaleTimeString() : ''}
                  </span>
                </div>
                {t.detail && (
                  <details className='mt-1 text-xs'>
                    <summary className='cursor-pointer text-muted-foreground'>detail</summary>
                    <Json value={t.detail} />
                  </details>
                )}
              </li>
            ))}
          </ol>
        </CardContent>
      </Card>

      {/* Facts */}
      {doc.facts_used.length > 0 && (
        <Card>
          <CardHeader className='pb-3'>
            <CardTitle className='text-base'>Facts the decision relied on</CardTitle>
          </CardHeader>
          <CardContent>
            <table className='w-full text-sm'>
              <thead>
                <tr className='border-b border-slate-200 text-left text-xs uppercase text-muted-foreground'>
                  <th className='pb-2'>Fact</th>
                  <th className='pb-2'>Value</th>
                  <th className='pb-2'>Supplied to</th>
                </tr>
              </thead>
              <tbody className='divide-y divide-slate-100'>
                {doc.facts_used.map((f, i) => (
                  <tr key={i}>
                    <td className='py-1.5 font-mono text-xs'>{f.fact}</td>
                    <td className='py-1.5'>{String(f.value)}</td>
                    <td className='py-1.5 font-mono text-xs text-muted-foreground'>
                      {f.supplied_to}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </CardContent>
        </Card>
      )}
    </div>
  )
}
