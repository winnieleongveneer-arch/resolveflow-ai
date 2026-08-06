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
  active_runs: number
  pending_human_reviews: number
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
}: {
  label: string
  value: number
  icon: React.ComponentType<{ className?: string; strokeWidth?: number }>
  tone: string
  href?: string
}) {
  const body = (
    <Card className={cn('h-full transition-shadow', href && 'cursor-pointer hover:shadow-md')}>
      <CardContent className='flex items-center justify-between p-5'>
        <div>
          <p className='text-xs font-medium uppercase tracking-wide text-muted-foreground'>
            {label}
          </p>
          <p className='mt-1 text-3xl font-bold tabular-nums text-brand-navy'>{value}</p>
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
        apiClient.get<Run[]>('/api/agent/runs?limit=8'),
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

  const resolved = summary?.runs_by_status?.RESOLVED ?? 0
  const total = summary?.total_runs ?? 0
  const autoRate = total > 0 ? Math.round((resolved / total) * 100) : 0

  return (
    <div className='space-y-4'>
      <div className='grid grid-cols-2 gap-4 lg:grid-cols-4'>
        <Tile
          label='Total runs'
          value={total}
          icon={Icons.activity}
          tone='bg-brand-navy'
        />
        <Tile
          label='Active now'
          value={summary?.active_runs ?? 0}
          icon={Icons.zap}
          tone='bg-brand-cornflower'
        />
        <Tile
          label='Awaiting human'
          value={summary?.pending_human_reviews ?? 0}
          icon={Icons.workbench}
          tone='bg-amber-500'
          href='/workbench'
        />
        <Tile
          label='Auto-resolved %'
          value={autoRate}
          icon={Icons.checkCircle}
          tone='bg-brand-purple'
        />
      </div>

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
