'use client'

/**
 * AI Insights — findings derived from what the agent actually processed.
 *
 * Every card here is computed on request from policy_evaluations,
 * workbench_items and workflow_runs. Nothing is seeded. An empty page means
 * the agent has not yet produced anything worth flagging, which is the
 * honest answer rather than a reason to invent one.
 */

import { useCallback, useEffect, useState } from 'react'
import Link from 'next/link'
import { motion } from 'framer-motion'
import toast from 'react-hot-toast'

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Icons } from '@/components/ui/icons'
import { cn } from '@/lib/utils'
import { apiClient } from '@/lib/api-client'

interface Insight {
  id: string
  title: string
  type: string
  severity: 'critical' | 'high' | 'medium' | 'low'
  evidence: string[]
  affected_cases: string[]
  affected_count: number
  detected_at: string
  business_implication: string
  recommended_action: string
  action_label: string
  action_href: string
  status: string
  owner: string
}

const SEVERITY: Record<string, { pill: string; bar: string; label: string }> = {
  critical: { pill: 'bg-rose-100 text-rose-800 ring-rose-600/20', bar: 'border-l-rose-500', label: 'Critical' },
  high: { pill: 'bg-amber-100 text-amber-800 ring-amber-600/20', bar: 'border-l-amber-500', label: 'High' },
  medium: { pill: 'bg-blue-100 text-blue-800 ring-blue-600/20', bar: 'border-l-blue-500', label: 'Medium' },
  low: { pill: 'bg-slate-100 text-slate-700 ring-slate-600/20', bar: 'border-l-slate-400', label: 'Low' },
}

const TYPE_LABEL: Record<string, string> = {
  major_incident: 'Major incident',
  major_incident_forming: 'Incident forming',
  automation_opportunity: 'Automation opportunity',
  learning_candidate: 'Learning candidate',
  knowledge_gap: 'Knowledge gap',
  sla_risk: 'SLA risk',
  reliability: 'Reliability',
}

export default function InsightsPage() {
  const [items, setItems] = useState<Insight[]>([])
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setItems(await apiClient.get<Insight[]>('/api/ai/insights'))
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to load insights')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const counts = items.reduce<Record<string, number>>((acc, i) => {
    acc[i.severity] = (acc[i.severity] || 0) + 1
    return acc
  }, {})

  return (
    <div className='space-y-6 p-6'>
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        className='flex flex-wrap items-end justify-between gap-4'
      >
        <div>
          <h1 className='text-3xl font-bold tracking-tight text-brand-navy'>AI Insights</h1>
          <p className='mt-1 text-sm text-muted-foreground'>
            Patterns the agent found in its own work — each with the evidence behind it and
            something you can do about it.
          </p>
        </div>
        <div className='flex items-center gap-2'>
          {(['critical', 'high', 'medium', 'low'] as const).map(
            (s) =>
              counts[s] > 0 && (
                <span
                  key={s}
                  className={cn(
                    'rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset',
                    SEVERITY[s].pill
                  )}
                >
                  {counts[s]} {SEVERITY[s].label.toLowerCase()}
                </span>
              )
          )}
          <Button variant='outline' size='sm' onClick={() => void load()} disabled={loading}>
            <Icons.refresh className={cn('mr-1.5 h-4 w-4', loading && 'animate-spin')} />
            Refresh
          </Button>
        </div>
      </motion.div>

      {loading && items.length === 0 && (
        <p className='text-sm text-muted-foreground'>Analysing processed cases…</p>
      )}

      {!loading && items.length === 0 && (
        <Card>
          <CardContent className='py-14 text-center'>
            <Icons.lightbulb className='mx-auto h-8 w-8 text-muted-foreground' strokeWidth={1.5} />
            <p className='mt-3 text-sm font-medium'>No insights yet</p>
            <p className='mx-auto mt-1 max-w-lg text-xs text-muted-foreground'>
              Insights are derived from cases the agent has actually processed — policy
              verdicts, human decisions and run outcomes. Trigger some runs and they appear
              here. Nothing on this page is pre-seeded.
            </p>
          </CardContent>
        </Card>
      )}

      <div className='space-y-4'>
        {items.map((insight, idx) => {
          const sev = SEVERITY[insight.severity] || SEVERITY.low
          return (
            <motion.div
              key={insight.id}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: idx * 0.04 }}
            >
              <Card className={cn('border-l-4', sev.bar)}>
                <CardHeader className='pb-3'>
                  <div className='flex flex-wrap items-start justify-between gap-3'>
                    <CardTitle className='text-base'>{insight.title}</CardTitle>
                    <div className='flex items-center gap-2'>
                      <span className='rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-700'>
                        {TYPE_LABEL[insight.type] || insight.type}
                      </span>
                      <span
                        className={cn(
                          'rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset',
                          sev.pill
                        )}
                      >
                        {sev.label}
                      </span>
                    </div>
                  </div>
                </CardHeader>

                <CardContent className='space-y-4'>
                  <div>
                    <p className='text-xs font-semibold uppercase tracking-wide text-muted-foreground'>
                      Evidence
                    </p>
                    <ul className='mt-1 list-inside list-disc space-y-0.5 text-sm'>
                      {insight.evidence.map((e, i) => (
                        <li key={i}>{e}</li>
                      ))}
                    </ul>
                  </div>

                  <div>
                    <p className='text-xs font-semibold uppercase tracking-wide text-muted-foreground'>
                      Why it matters
                    </p>
                    <p className='mt-1 text-sm'>{insight.business_implication}</p>
                  </div>

                  <div>
                    <p className='text-xs font-semibold uppercase tracking-wide text-muted-foreground'>
                      Recommended next step
                    </p>
                    <p className='mt-1 text-sm'>{insight.recommended_action}</p>
                  </div>

                  {insight.affected_cases.length > 0 && (
                    <div className='flex flex-wrap gap-1.5'>
                      {insight.affected_cases.slice(0, 12).map((c) => (
                        <span
                          key={c}
                          className='rounded-md bg-slate-100 px-2 py-0.5 font-mono text-[11px] text-slate-700'
                        >
                          {c}
                        </span>
                      ))}
                      {insight.affected_cases.length > 12 && (
                        <span className='px-1 text-[11px] text-muted-foreground'>
                          +{insight.affected_cases.length - 12} more
                        </span>
                      )}
                    </div>
                  )}

                  <div className='flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 pt-3'>
                    <p className='text-xs text-muted-foreground'>
                      Detected {new Date(insight.detected_at).toLocaleString()} · owner{' '}
                      {insight.owner}
                    </p>
                    <Link href={insight.action_href}>
                      <Button size='sm'>
                        {insight.action_label}
                        <Icons.arrowRight className='ml-1.5 h-4 w-4' />
                      </Button>
                    </Link>
                  </div>
                </CardContent>
              </Card>
            </motion.div>
          )
        })}
      </div>
    </div>
  )
}
