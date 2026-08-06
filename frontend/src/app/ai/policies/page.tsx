'use client'

/**
 * AI Policies — the rules a business user owns.
 *
 * Every policy here is a real row in policy_definitions. Editing a threshold
 * bumps its version, writes an immutable policy_versions record, and changes
 * what the agent does on the very next run — with no code and no deploy.
 *
 * The evaluation history below each policy is the audit trail: every verdict
 * the engine has produced, with the reasons that drove it.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { motion } from 'framer-motion'
import toast from 'react-hot-toast'

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Icons } from '@/components/ui/icons'
import { cn } from '@/lib/utils'
import { apiClient } from '@/lib/api-client'

type Primitive = string | number | boolean | string[]

interface Hint {
  label?: string
  help?: string
  type?: 'integer' | 'float' | 'boolean' | 'list' | 'string'
  min?: number
  max?: number
  step?: number
  options?: string[]
}

interface Policy {
  id: string
  policy_key: string
  name: string
  description: string | null
  active_version: number
  configuration: Record<string, Primitive>
  schema_hints: Record<string, Hint> | null
  is_active: boolean
  updated_by: string | null
  updated_at: string | null
}

interface PolicyVersion {
  id: string
  version: number
  configuration: Record<string, Primitive>
  change_note: string | null
  created_by: string | null
  created_at: string | null
}

interface Evaluation {
  id: string
  issue_key: string | null
  policy_version: number
  verdict: string
  reasons: string[]
  is_simulation: boolean
  evaluated_at: string | null
}

const VERDICT: Record<string, string> = {
  ALLOW: 'bg-emerald-100 text-emerald-800 ring-emerald-600/20',
  REQUIRE_HUMAN_REVIEW: 'bg-amber-100 text-amber-800 ring-amber-600/20',
  DENY: 'bg-rose-100 text-rose-800 ring-rose-600/20',
}

function titleise(key: string) {
  return key.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

export default function PoliciesPage() {
  const [policies, setPolicies] = useState<Policy[]>([])
  const [selectedKey, setSelectedKey] = useState<string | null>(null)
  const [draft, setDraft] = useState<Record<string, Primitive>>({})
  const [note, setNote] = useState('')
  const [versions, setVersions] = useState<PolicyVersion[]>([])
  const [evals, setEvals] = useState<Evaluation[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)

  const selected = useMemo(
    () => policies.find((p) => p.policy_key === selectedKey) || null,
    [policies, selectedKey]
  )

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await apiClient.get<Policy[]>('/api/ai/policies')
      setPolicies(data)
      setSelectedKey((prev) => prev ?? data[0]?.policy_key ?? null)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to load policies')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const loadDetail = useCallback(async (key: string) => {
    try {
      const [v, e] = await Promise.all([
        apiClient.get<PolicyVersion[]>(`/api/ai/policies/${key}/versions`),
        apiClient.get<Evaluation[]>(
          `/api/ai/policy-evaluations?policy_key=${key}&limit=15`
        ),
      ])
      setVersions(v)
      setEvals(e)
    } catch {
      setVersions([])
      setEvals([])
    }
  }, [])

  useEffect(() => {
    if (!selected) return
    setDraft({ ...selected.configuration })
    setNote('')
    void loadDetail(selected.policy_key)
  }, [selected, loadDetail])

  const dirty = useMemo(() => {
    if (!selected) return false
    return JSON.stringify(draft) !== JSON.stringify(selected.configuration)
  }, [draft, selected])

  async function save() {
    if (!selected) return
    setSaving(true)
    try {
      const updated = await apiClient.put<Policy>(
        `/api/ai/policies/${selected.policy_key}`,
        {
          configuration: draft,
          change_note: note || 'Edited from the Command Center',
          updated_by: 'command_center',
        }
      )
      toast.success(
        `Saved. ${updated.name} is now v${updated.active_version} — the next run uses it.`
      )
      await load()
      await loadDetail(selected.policy_key)
      setNote('')
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to save')
    } finally {
      setSaving(false)
    }
  }

  function setField(key: string, value: Primitive) {
    setDraft((d) => ({ ...d, [key]: value }))
  }

  function renderField(key: string, value: Primitive) {
    const hint: Hint = selected?.schema_hints?.[key] || {}
    const label = hint.label || titleise(key)
    const type =
      hint.type ||
      (typeof value === 'boolean'
        ? 'boolean'
        : Array.isArray(value)
          ? 'list'
          : typeof value === 'number'
            ? Number.isInteger(value)
              ? 'integer'
              : 'float'
            : 'string')

    return (
      <div key={key} className='space-y-1.5 rounded-lg border border-slate-200 p-3'>
        <div className='flex items-start justify-between gap-3'>
          <div>
            <p className='text-sm font-medium'>{label}</p>
            {hint.help && (
              <p className='mt-0.5 text-xs text-muted-foreground'>{hint.help}</p>
            )}
          </div>
          <code className='shrink-0 rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[11px] text-slate-600'>
            {key}
          </code>
        </div>

        {type === 'boolean' && (
          <button
            type='button'
            onClick={() => setField(key, !(value as boolean))}
            className={cn(
              'relative h-6 w-11 rounded-full transition-colors',
              value ? 'bg-brand-cornflower' : 'bg-slate-300'
            )}
            aria-pressed={Boolean(value)}
          >
            <span
              className={cn(
                'absolute top-0.5 h-5 w-5 rounded-full bg-white transition-transform',
                value ? 'translate-x-5' : 'translate-x-0.5'
              )}
            />
          </button>
        )}

        {(type === 'integer' || type === 'float') && (
          <div className='flex items-center gap-3'>
            <input
              type='range'
              min={hint.min ?? 0}
              max={hint.max ?? (type === 'float' ? 1 : 100)}
              step={hint.step ?? (type === 'float' ? 0.01 : 1)}
              value={Number(value)}
              onChange={(e) => setField(key, Number(e.target.value))}
              className='flex-1 accent-brand-cornflower'
            />
            <input
              type='number'
              min={hint.min}
              max={hint.max}
              step={hint.step ?? (type === 'float' ? 0.01 : 1)}
              value={Number(value)}
              onChange={(e) => setField(key, Number(e.target.value))}
              className='w-24 rounded-lg border border-slate-300 px-2 py-1 text-sm tabular-nums'
            />
          </div>
        )}

        {type === 'list' && (
          <div className='flex flex-wrap gap-1.5'>
            {(hint.options || (value as string[])).map((opt) => {
              const on = (value as string[]).includes(opt)
              return (
                <button
                  key={opt}
                  type='button'
                  onClick={() =>
                    setField(
                      key,
                      on
                        ? (value as string[]).filter((v) => v !== opt)
                        : [...(value as string[]), opt]
                    )
                  }
                  className={cn(
                    'rounded-full px-2.5 py-1 text-xs ring-1 ring-inset transition-colors',
                    on
                      ? 'bg-brand-cornflower text-white ring-brand-cornflower'
                      : 'bg-white text-slate-600 ring-slate-300'
                  )}
                >
                  {opt}
                </button>
              )
            })}
          </div>
        )}

        {type === 'string' && (
          <input
            value={String(value)}
            onChange={(e) => setField(key, e.target.value)}
            className='w-full rounded-lg border border-slate-300 px-3 py-1.5 text-sm'
          />
        )}
      </div>
    )
  }

  return (
    <div className='space-y-6 p-6'>
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        className='flex flex-wrap items-end justify-between gap-4'
      >
        <div>
          <h1 className='text-3xl font-bold tracking-tight text-brand-navy'>AI Policies</h1>
          <p className='mt-1 text-sm text-muted-foreground'>
            The rules that decide what the agent may do alone. Change a threshold here and
            the next run behaves differently — no code, no deploy.
          </p>
        </div>
        <Button variant='outline' size='sm' onClick={() => void load()} disabled={loading}>
          <Icons.refresh className={cn('mr-1.5 h-4 w-4', loading && 'animate-spin')} />
          Refresh
        </Button>
      </motion.div>

      <div className='grid grid-cols-12 gap-6'>
        {/* Policy list */}
        <div className='col-span-12 space-y-3 lg:col-span-4'>
          {policies.map((p) => (
            <Card
              key={p.id}
              onClick={() => setSelectedKey(p.policy_key)}
              className={cn(
                'cursor-pointer transition-all hover:shadow-md',
                selectedKey === p.policy_key && 'ring-2 ring-brand-cornflower'
              )}
            >
              <CardContent className='space-y-2 p-4'>
                <div className='flex items-start justify-between gap-2'>
                  <p className='text-sm font-semibold'>{p.name}</p>
                  <span className='shrink-0 rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-700'>
                    v{p.active_version}
                  </span>
                </div>
                <p className='text-xs text-muted-foreground'>{p.description}</p>
                <div className='flex items-center gap-2'>
                  <span
                    className={cn(
                      'h-2 w-2 rounded-full',
                      p.is_active ? 'bg-emerald-500' : 'bg-slate-400'
                    )}
                  />
                  <span className='text-xs text-muted-foreground'>
                    {p.is_active ? 'Active' : 'Inactive'}
                  </span>
                </div>
              </CardContent>
            </Card>
          ))}
          {!loading && policies.length === 0 && (
            <Card>
              <CardContent className='py-10 text-center text-sm text-muted-foreground'>
                No policies configured.
              </CardContent>
            </Card>
          )}
        </div>

        {/* Editor */}
        <div className='col-span-12 space-y-4 lg:col-span-8'>
          {selected && (
            <>
              <Card>
                <CardHeader className='pb-3'>
                  <div className='flex flex-wrap items-center justify-between gap-3'>
                    <CardTitle className='flex items-center gap-2 text-base'>
                      <Icons.shield
                        className='h-5 w-5 text-brand-cornflower'
                        strokeWidth={1.5}
                      />
                      {selected.name}
                    </CardTitle>
                    <span className='rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium'>
                      active v{selected.active_version}
                    </span>
                  </div>
                </CardHeader>
                <CardContent className='space-y-4'>
                  <div className='grid gap-3 sm:grid-cols-2'>
                    {Object.entries(draft).map(([k, v]) => renderField(k, v))}
                  </div>

                  <div className='space-y-2 border-t border-slate-100 pt-4'>
                    <input
                      value={note}
                      onChange={(e) => setNote(e.target.value)}
                      placeholder='Why are you changing this? (recorded in the version history)'
                      className='w-full rounded-lg border border-slate-300 px-3 py-2 text-sm'
                    />
                    <div className='flex items-center gap-3'>
                      <Button onClick={() => void save()} disabled={!dirty || saving}>
                        {saving ? 'Saving…' : `Save as v${selected.active_version + 1}`}
                      </Button>
                      {dirty && (
                        <Button
                          variant='outline'
                          onClick={() => setDraft({ ...selected.configuration })}
                        >
                          Discard
                        </Button>
                      )}
                      <p className='text-xs text-muted-foreground'>
                        {dirty
                          ? 'Unsaved changes — the agent is still using the active version.'
                          : 'No changes.'}
                      </p>
                    </div>
                  </div>
                </CardContent>
              </Card>

              {/* Evaluation history */}
              <Card>
                <CardHeader className='pb-3'>
                  <CardTitle className='text-base'>Evaluation history</CardTitle>
                  <p className='text-xs text-muted-foreground'>
                    Every verdict this policy has produced, with the reasons behind it.
                    Records are immutable.
                  </p>
                </CardHeader>
                <CardContent>
                  {evals.length === 0 ? (
                    <p className='py-6 text-center text-sm text-muted-foreground'>
                      No evaluations yet.
                    </p>
                  ) : (
                    <ul className='divide-y divide-slate-100'>
                      {evals.map((ev) => (
                        <li key={ev.id} className='space-y-1 py-2.5'>
                          <div className='flex flex-wrap items-center gap-2'>
                            <span
                              className={cn(
                                'rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset',
                                VERDICT[ev.verdict] || 'bg-slate-100 text-slate-700'
                              )}
                            >
                              {ev.verdict}
                            </span>
                            <span className='font-mono text-xs'>{ev.issue_key || '—'}</span>
                            <span className='text-xs text-muted-foreground'>
                              v{ev.policy_version}
                            </span>
                            {ev.is_simulation && (
                              <span className='rounded bg-slate-100 px-1.5 text-[11px]'>
                                simulation
                              </span>
                            )}
                            <span className='ml-auto text-xs text-muted-foreground'>
                              {ev.evaluated_at
                                ? new Date(ev.evaluated_at).toLocaleString()
                                : ''}
                            </span>
                          </div>
                          <ul className='list-inside list-disc text-xs text-muted-foreground'>
                            {(ev.reasons || []).slice(0, 3).map((r, i) => (
                              <li key={i}>{r}</li>
                            ))}
                          </ul>
                        </li>
                      ))}
                    </ul>
                  )}
                </CardContent>
              </Card>

              {/* Version history */}
              <Card>
                <CardHeader className='pb-3'>
                  <CardTitle className='text-base'>Version history</CardTitle>
                </CardHeader>
                <CardContent>
                  <ul className='divide-y divide-slate-100'>
                    {versions.map((v) => (
                      <li key={v.id} className='flex items-start justify-between gap-3 py-2'>
                        <div>
                          <p className='text-sm font-medium'>v{v.version}</p>
                          <p className='text-xs text-muted-foreground'>
                            {v.change_note || 'No note'}
                          </p>
                        </div>
                        <p className='shrink-0 text-xs text-muted-foreground'>
                          {v.created_by} ·{' '}
                          {v.created_at ? new Date(v.created_at).toLocaleString() : ''}
                        </p>
                      </li>
                    ))}
                  </ul>
                </CardContent>
              </Card>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
