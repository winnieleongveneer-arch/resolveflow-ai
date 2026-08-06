'use client'

/**
 * Data Manager — the live registry of every connected system.
 *
 * Guide 8.4: an integration that is connected but unused, or an entry that is
 * hardcoded, does not count toward the integration floor. So every badge here
 * comes from a real health check or a real read/write, and "Run health checks"
 * makes those calls live in front of you.
 */

import { useCallback, useEffect, useState } from 'react'
import { motion } from 'framer-motion'
import toast from 'react-hot-toast'

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Icons } from '@/components/ui/icons'
import { cn } from '@/lib/utils'
import { apiClient } from '@/lib/api-client'

interface Integration {
  id: string
  integration_key: string
  integration_name: string
  category: string
  purpose: string | null
  status: string
  credentials_configured: boolean
  last_health_check: string | null
  last_successful_read: string | null
  last_successful_write: string | null
  latency_ms: number | null
  records_processed: number
  latest_error: string | null
  used_by_operators: string[] | null
}

const STATUS: Record<string, { dot: string; pill: string; label: string }> = {
  HEALTHY: { dot: 'bg-emerald-500', pill: 'bg-emerald-100 text-emerald-800 ring-emerald-600/20', label: 'Healthy' },
  DEGRADED: { dot: 'bg-amber-500', pill: 'bg-amber-100 text-amber-800 ring-amber-600/20', label: 'Degraded' },
  UNHEALTHY: { dot: 'bg-rose-500', pill: 'bg-rose-100 text-rose-800 ring-rose-600/20', label: 'Unhealthy' },
  UNKNOWN: { dot: 'bg-slate-400', pill: 'bg-slate-100 text-slate-700 ring-slate-600/20', label: 'Not configured' },
}

const CATEGORY_LABEL: Record<string, string> = {
  system_of_record: 'System of record',
  channel: 'Channel',
  agent_platform: 'Agent platform',
  knowledge: 'Knowledge',
}

function when(value: string | null) {
  if (!value) return 'Never'
  return new Date(value).toLocaleString()
}

export default function DataManagerPage() {
  const [rows, setRows] = useState<Integration[]>([])
  const [loading, setLoading] = useState(true)
  const [checking, setChecking] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setRows(await apiClient.get<Integration[]>('/api/integrations'))
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to load integrations')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  async function runChecks() {
    setChecking(true)
    try {
      setRows(await apiClient.post<Integration[]>('/api/integrations/health-check'))
      toast.success('Health checks complete')
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Health check failed')
    } finally {
      setChecking(false)
    }
  }

  const healthy = rows.filter((r) => r.status === 'HEALTHY').length
  const categories = new Set(rows.filter((r) => r.status === 'HEALTHY').map((r) => r.category))
  const meetsFloor = healthy >= 3 && categories.size >= 2

  return (
    <div className='space-y-6 p-6'>
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        className='flex flex-wrap items-end justify-between gap-4'
      >
        <div>
          <h1 className='text-3xl font-bold tracking-tight text-brand-navy'>Data Manager</h1>
          <p className='mt-1 text-sm text-muted-foreground'>
            Every system this agent is connected to, what it is used for, and whether it is
            actually working.
          </p>
        </div>
        <Button onClick={() => void runChecks()} disabled={checking}>
          <Icons.refresh className={cn('mr-1.5 h-4 w-4', checking && 'animate-spin')} />
          {checking ? 'Checking…' : 'Run health checks'}
        </Button>
      </motion.div>

      {/* Integration floor */}
      <Card className={cn('border-l-4', meetsFloor ? 'border-l-emerald-500' : 'border-l-amber-500')}>
        <CardContent className='flex flex-wrap items-center justify-between gap-4 py-4'>
          <div className='flex items-center gap-3'>
            {meetsFloor ? (
              <Icons.checkCircle className='h-5 w-5 text-emerald-600' strokeWidth={1.5} />
            ) : (
              <Icons.alertTriangle className='h-5 w-5 text-amber-600' strokeWidth={1.5} />
            )}
            <div>
              <p className='text-sm font-semibold'>
                {healthy} healthy across {categories.size}{' '}
                {categories.size === 1 ? 'category' : 'categories'}
              </p>
              <p className='text-xs text-muted-foreground'>
                Requirement: at least three live integrations across at least two categories,
                including one channel and one system of record.
              </p>
            </div>
          </div>
          <span
            className={cn(
              'rounded-full px-3 py-1 text-xs font-medium ring-1 ring-inset',
              meetsFloor
                ? 'bg-emerald-100 text-emerald-800 ring-emerald-600/20'
                : 'bg-amber-100 text-amber-800 ring-amber-600/20'
            )}
          >
            {meetsFloor ? 'Floor met' : 'Floor not yet met'}
          </span>
        </CardContent>
      </Card>

      {loading && rows.length === 0 && (
        <p className='text-sm text-muted-foreground'>Loading integrations…</p>
      )}

      <div className='grid gap-4 lg:grid-cols-2'>
        {rows.map((row) => {
          const s = STATUS[row.status] || STATUS.UNKNOWN
          return (
            <motion.div key={row.id} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}>
              <Card className='h-full'>
                <CardHeader className='pb-3'>
                  <div className='flex items-start justify-between gap-3'>
                    <CardTitle className='flex items-center gap-2 text-base'>
                      <span className={cn('h-2.5 w-2.5 rounded-full', s.dot)} />
                      {row.integration_name}
                    </CardTitle>
                    <span
                      className={cn(
                        'rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset',
                        s.pill
                      )}
                    >
                      {s.label}
                    </span>
                  </div>
                  <p className='text-xs uppercase tracking-wide text-muted-foreground'>
                    {CATEGORY_LABEL[row.category] || row.category}
                  </p>
                </CardHeader>

                <CardContent className='space-y-3 text-sm'>
                  <p className='text-muted-foreground'>{row.purpose}</p>

                  <dl className='grid grid-cols-2 gap-x-4 gap-y-2 text-xs'>
                    <div>
                      <dt className='text-muted-foreground'>Credentials</dt>
                      <dd className='font-medium'>
                        {row.credentials_configured ? 'Configured' : 'Not set'}
                      </dd>
                    </div>
                    <div>
                      <dt className='text-muted-foreground'>Latency</dt>
                      <dd className='font-medium'>
                        {row.latency_ms != null ? `${row.latency_ms} ms` : '—'}
                      </dd>
                    </div>
                    <div>
                      <dt className='text-muted-foreground'>Last check</dt>
                      <dd className='font-medium'>{when(row.last_health_check)}</dd>
                    </div>
                    <div>
                      <dt className='text-muted-foreground'>Records processed</dt>
                      <dd className='font-medium'>{row.records_processed}</dd>
                    </div>
                    <div>
                      <dt className='text-muted-foreground'>Last read</dt>
                      <dd className='font-medium'>{when(row.last_successful_read)}</dd>
                    </div>
                    <div>
                      <dt className='text-muted-foreground'>Last write</dt>
                      <dd className='font-medium'>{when(row.last_successful_write)}</dd>
                    </div>
                  </dl>

                  {row.used_by_operators && row.used_by_operators.length > 0 && (
                    <div className='flex flex-wrap gap-1.5'>
                      {row.used_by_operators.map((op) => (
                        <span
                          key={op}
                          className='rounded-md bg-slate-100 px-2 py-0.5 font-mono text-[11px] text-slate-700'
                        >
                          {op}
                        </span>
                      ))}
                    </div>
                  )}

                  {row.latest_error && (
                    <p className='rounded-lg bg-slate-50 p-2.5 text-xs text-slate-700'>
                      {row.latest_error}
                    </p>
                  )}
                </CardContent>
              </Card>
            </motion.div>
          )
        })}
      </div>
    </div>
  )
}
