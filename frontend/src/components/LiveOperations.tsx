'use client'

/**
 * Live operational picture, computed from runs the agent actually performed.
 *
 * Every number here comes from /api/agent/summary. On a fresh database they
 * are all zero — which is correct. The dashboard must move because the agent
 * ran, not because a value was seeded.
 */

import { useCallback, useEffect, useState } from 'react'
import Link from 'next/link'
import { motion } from 'framer-motion'

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Icons } from '@/components/ui/icons'
import { cn } from '@/lib/utils'
import { apiClient } from '@/lib/api-client'

interface Summary {
  total_runs: number
  backlog: number
  open_agent_cases: number
  source_tickets: number | null
  executing_now: number
  awaiting_human: number
  verified_resolved: number
  verified_auto_resolved: number
  technical_failures: number
  auto_resolution_rate: number | null
  auto_resolution_note: string | null
  runs_by_status: Record<string, number>
  latest_run: { id: string; issue_key: string; status: string; started_at: string | null } | null
}

interface Run {
  id: string
  issue_key: string
  status: string
  current_stage: string | null
  started_at: string | null
}

const STATUS_TONE: Record<string, string> = {
  RESOLVED: 'text-emerald-700 bg-emerald-50',
  APPROVED: 'text-emerald-700 bg-emerald-50',
  WAITING_FOR_HUMAN: 'text-amber-700 bg-amber-50',
  POLICY_GATED: 'text-blue-700 bg-blue-50',
  DENIED: 'text-rose-700 bg-rose-50',
  FAILED: 'text-rose-700 bg-rose-50',
  ESCALATED: 'text-amber-700 bg-amber-50',
}

function Tile({
  label,
  value,
  icon: Icon,
  tone,
  href,
  suffix,
  hint,
}: {
  label: string
  value: number | null
  icon: React.ComponentType<{ className?: string; strokeWidth?: number }>
  tone: string
  href?: string
  suffix?: string
  hint?: string
}) {
  const body = (
    <Card className={cn('h-full transition-shadow', href && 'cursor-pointer hover:shadow-md')}>
      <CardContent className='flex items-center justify-between p-5'>
        <div>
          <p className='text-xs font-medium uppercase tracking-wide text-muted-foreground'>
            {label}
          </p>
          <p className='mt-1 text-3xl font-bold tabular-nums text-brand-navy'>
            {value === null ? 'N/A' : value}
            {value !== null && suffix ? (
              <span className='ml-0.5 text-xl'>{suffix}</span>
            ) : null}
          </p>
          {hint && <p className='mt-0.5 text-[11px] text-muted-foreground'>{hint}</p>}
        </div>
        <div className={cn('rounded-xl p-2.5 text-white shadow-lg', tone)}>
          <Icon className='h-5 w-5' strokeWidth={1.5} />
        </div>
      </CardContent>
    </Card>
  )
  return href ? <Link href={href}>{body}</Link> : body
}

export function LiveOperations() {
  const [summary, setSummary] = useState<Summary | null>(null)
  const [runs, setRuns] = useState<Run[]>([])
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const [s, r] = await Promise.all([
        apiClient.get<Summary>('/api/agent/summary'),
        apiClient.get<Run[]>('/api/agent/runs?limit=8&agent_only=true'),
      ])
      setSummary(s)
      setRuns(r)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Unable to reach the agent API')
    }
  }, [])

  useEffect(() => {
    void load()
    const t = setInterval(() => void load(), 5000)
    return () => clearInterval(t)
  }, [load])

  if (error) {
    return (
      <Card className='border-l-4 border-l-rose-500'>
        <CardContent className='py-4 text-sm'>
          <p className='font-medium text-rose-800'>Agent API unreachable</p>
          <p className='mt-1 text-muted-foreground'>{error}</p>
        </CardContent>
      </Card>
    )
  }

  return (
    <div className='space-y-4'>
      <div className='grid grid-cols-2 gap-4 lg:grid-cols-3 xl:grid-cols-6'>
        {/*
          Two different questions, kept apart on purpose. "Backlog" alone
          invited the obvious challenge — the source system has hundreds of
          tickets, so why does this say a hundred and something? Both numbers
          are true; they count different things. Naming the denominator here
          answers it before it is asked.
        */}
        <Tile
          label='Open agent cases'
          value={summary?.open_agent_cases ?? summary?.backlog ?? 0}
          icon={Icons.inbox}
          tone='bg-slate-600'
          hint={
            summary?.source_tickets
              ? `of ${summary.source_tickets} source tickets`
              : 'accepted, not yet in a verified outcome'
          }
        />
        <Tile
          label='Executing now'
          value={summary?.executing_now ?? 0}
          icon={Icons.zap}
          tone='bg-brand-cornflower'
          hint='in flight on Auto'
        />
        <Tile
          label='Awaiting human'
          value={summary?.awaiting_human ?? 0}
          icon={Icons.workbench}
          tone='bg-amber-500'
          href='/workbench'
          hint='pending decisions'
        />
        <Tile
          label='Verified resolved'
          value={summary?.verified_resolved ?? 0}
          icon={Icons.checkCircle}
          tone='bg-emerald-600'
          hint='with verification'
        />
        <Tile
          label='Technical failures'
          value={summary?.technical_failures ?? 0}
          icon={Icons.alertTriangle}
          tone='bg-rose-600'
          hint='not policy denials'
        />
        <Tile
          label='Auto-resolution'
          value={summary?.auto_resolution_rate ?? null}
          suffix='%'
          icon={Icons.sparkles}
          tone='bg-brand-purple'
          hint='of verified resolutions'
        />
      </div>

      {summary?.auto_resolution_note && (
        <p className='text-xs text-muted-foreground'>{summary.auto_resolution_note}</p>
      )}

      <Card>
        <CardHeader className='pb-3'>
          <CardTitle className='flex items-center gap-2 text-base'>
            <Icons.activity className='h-4 w-4 text-brand-cornflower' strokeWidth={1.5} />
            Recent agent activity
          </CardTitle>
        </CardHeader>
        <CardContent>
          {runs.length === 0 ? (
            <p className='py-6 text-center text-sm text-muted-foreground'>
              No runs yet. Trigger the Orchestrator and this list fills from real activity.
            </p>
          ) : (
            <ul className='divide-y divide-slate-100'>
              {runs.map((run) => (
                <motion.li
                  key={run.id}
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  className='flex items-center justify-between gap-3 py-2.5'
                >
                  {/* whole row links to the Decision Passport */}
                  <div className='min-w-0'>
                    <Link
                      href={`/runs/${run.id}`}
                      className='font-mono text-sm font-medium hover:text-brand-cornflower hover:underline'
                    >
                      {run.issue_key}
                    </Link>
                    <p className='truncate text-xs text-muted-foreground'>
                      {run.started_at ? new Date(run.started_at).toLocaleString() : '—'}
                      {/*
                        The run id, short enough to read aloud. A ticket key is
                        not unique across attempts — ITSM-2231 has been run four
                        times — so without this there is no way to say which run
                        a Passport, a Slack message or an Auto execution refers
                        to. Eight characters is enough to match by eye and short
                        enough not to crowd the row.
                      */}
                      <span className='ml-2 font-mono opacity-60'>
                        run {run.id.slice(0, 8)}
                      </span>
                    </p>
                  </div>
                  <span
                    className={cn(
                      'shrink-0 rounded-full px-2.5 py-0.5 text-xs font-medium',
                      STATUS_TONE[run.status] || 'bg-slate-100 text-slate-700'
                    )}
                  >
                    {run.status.replaceAll('_', ' ').toLowerCase()}
                  </span>
                </motion.li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

export default LiveOperations
