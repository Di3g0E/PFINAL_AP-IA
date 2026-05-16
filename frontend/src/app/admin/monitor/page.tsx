"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";

import {
  clearToken, getIsAdmin, getMonitorEvents, getMonitorReport, getUserId,
  type MonitorEvent, type MonitoringReport,
} from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";

/**
 * Panel del agente Monitor (Punto 4 del enunciado).
 *
 * Consume `GET /monitor/health-detailed` y pinta:
 *   - 4 cards de KPI (eventos, error_rate, sesiones activas, usuarios)
 *   - Indicador de health (ok/warning/critical)
 *   - Bar chart con p50/p95 por (agent, action)
 *   - Tabla de top errores
 * Auto-refresca cada 30s.
 */
const REFRESH_INTERVAL_MS = 30_000;
const WINDOW_OPTIONS = [
  { value: 15, label: "15 min" },
  { value: 60, label: "1 h" },
  { value: 360, label: "6 h" },
  { value: 1440, label: "24 h" },
];

const HEALTH_STYLES: Record<string, { label: string; classes: string }> = {
  ok: { label: "✅ Sistema sano", classes: "bg-green-100 text-green-800 border-green-300" },
  warning: { label: "⚠️ Atención", classes: "bg-amber-100 text-amber-800 border-amber-300" },
  critical: { label: "🚨 Crítico", classes: "bg-red-100 text-red-800 border-red-300" },
};

function formatMs(v: number | null): string {
  if (v == null) return "—";
  if (v < 1000) return `${v.toFixed(0)} ms`;
  return `${(v / 1000).toFixed(2)} s`;
}

export default function MonitorPage() {
  const router = useRouter();
  const [userId, setUserId] = useState<string | null>(null);
  const [report, setReport] = useState<MonitoringReport | null>(null);
  const [windowMinutes, setWindowMinutes] = useState(60);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);

  // Analizador de logs
  const [events, setEvents] = useState<MonitorEvent[]>([]);
  const [logStatus, setLogStatus] = useState<string>("");
  const [logAgent, setLogAgent] = useState<string>("");
  const [logLimit, setLogLimit] = useState<number>(50);
  const [logsLoading, setLogsLoading] = useState(false);
  const [logsError, setLogsError] = useState<string | null>(null);

  const refresh = useCallback(async (minutes: number) => {
    setLoading(true);
    setError(null);
    try {
      const r = await getMonitorReport(minutes);
      setReport(r);
      setLastRefresh(new Date());
    } catch (err) {
      const msg = (err as Error).message;
      setError(msg);
      if (msg.toLowerCase().includes("token") || msg.includes("401")) {
        clearToken();
        router.replace("/login");
      }
    } finally {
      setLoading(false);
    }
  }, [router]);

  const refreshLogs = useCallback(async () => {
    setLogsLoading(true);
    setLogsError(null);
    try {
      const list = await getMonitorEvents({
        limit: logLimit,
        status: logStatus || undefined,
        agent: logAgent || undefined,
      });
      setEvents(list);
    } catch (err) {
      setLogsError((err as Error).message);
    } finally {
      setLogsLoading(false);
    }
  }, [logLimit, logStatus, logAgent]);

  // Auth + carga inicial
  useEffect(() => {
    const uid = getUserId();
    if (!uid) {
      router.replace("/login");
      return;
    }
    if (!getIsAdmin()) {
      // Solo admins pueden ver esta página: a un usuario normal lo
      // mandamos al chat para evitar 401s y confusión.
      router.replace("/chat");
      return;
    }
    setUserId(uid);
    refresh(windowMinutes);
    refreshLogs();
  }, [router, refresh, refreshLogs, windowMinutes]);

  // Auto-refresh cada N s mientras el usuario esté en la página
  useEffect(() => {
    if (!userId) return;
    const handle = setInterval(() => {
      refresh(windowMinutes);
      refreshLogs();
    }, REFRESH_INTERVAL_MS);
    return () => clearInterval(handle);
  }, [userId, windowMinutes, refresh, refreshLogs]);

  if (!userId) {
    return <p className="text-sm text-slate-500">Cargando…</p>;
  }

  const healthStyle = report ? (HEALTH_STYLES[report.health] ?? HEALTH_STYLES.ok) : HEALTH_STYLES.ok;
  const latencyData = (report?.latencies ?? [])
    .filter((l) => l.p95_ms != null)
    .slice(0, 10)
    .map((l) => ({
      label: `${l.agent}.${l.action}`,
      p50: l.p50_ms ?? 0,
      p95: l.p95_ms ?? 0,
    }));

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 via-white to-blue-50">
      <main className="max-w-6xl mx-auto px-4 py-6">
        <div className="flex items-start justify-between mb-6 flex-wrap gap-3">
          <div>
            <h1 className="text-2xl font-bold gradient-text">Monitor del sistema</h1>
            <p className="text-sm text-slate-500 mt-1">
              Estado agregado de los agentes y eventos. Auto-refresco cada 30s.
            </p>
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            <select
              value={windowMinutes}
              onChange={(e) => setWindowMinutes(Number(e.target.value))}
              className="text-sm border border-slate-300 rounded-md px-3 py-2 bg-white"
            >
              {WINDOW_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>{opt.label}</option>
              ))}
            </select>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => refresh(windowMinutes)}
              disabled={loading}
            >
              {loading ? "Actualizando…" : "Refrescar"}
            </Button>
          </div>
        </div>

        {error && (
          <Card variant="default" className="border-red-200 bg-red-50 mb-6">
            <CardContent className="p-4">
              <p className="text-sm font-medium text-red-800">Error: {error}</p>
            </CardContent>
          </Card>
        )}

        {report && (
          <>
            {/* Badge de health + última actualización */}
            <div className="flex items-center justify-between flex-wrap gap-2 mb-4">
              <span className={`px-3 py-1.5 rounded-full text-sm font-medium border ${healthStyle.classes}`}>
                {healthStyle.label}
              </span>
              <p className="text-xs text-slate-500">
                Ventana: últimos {report.window_minutes} min ·
                Última actualización: {lastRefresh?.toLocaleTimeString("es-ES")}
              </p>
            </div>

            {/* KPIs */}
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
              <KPI title="Eventos totales" value={report.total_events.toString()} subtitle={`${report.ok_count} OK`} />
              <KPI
                title="Tasa de error"
                value={`${(report.error_rate * 100).toFixed(1)}%`}
                subtitle={`${report.error_count} errores`}
                accent={report.error_rate >= 0.20 ? "red" : report.error_rate >= 0.05 ? "amber" : "green"}
              />
              <KPI title="Sesiones activas" value={report.active_sessions.toString()} subtitle="chats únicos" />
              <KPI title="Usuarios activos" value={report.active_users.toString()} subtitle="con actividad" />
            </div>

            {/* Reparto de status */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mb-6 text-xs">
              <StatusBadge label="OK" count={report.ok_count} cls="bg-green-50 text-green-700 border-green-200" />
              <StatusBadge label="Warning" count={report.warning_count} cls="bg-amber-50 text-amber-700 border-amber-200" />
              <StatusBadge label="Error" count={report.error_count} cls="bg-red-50 text-red-700 border-red-200" />
              <StatusBadge label="Denied" count={report.denied_count} cls="bg-slate-100 text-slate-700 border-slate-200" />
            </div>

            {/* Latencias */}
            <Card variant="glass" className="mb-6">
              <CardHeader>
                <CardTitle className="text-base">Latencias p50/p95 por agente y acción</CardTitle>
              </CardHeader>
              <CardContent>
                {latencyData.length === 0 ? (
                  <p className="text-sm text-slate-500 text-center py-8">
                    Sin datos de latencia en esta ventana.
                  </p>
                ) : (
                  <div className="w-full h-72">
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={latencyData} margin={{ top: 10, right: 20, left: 0, bottom: 30 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                        <XAxis dataKey="label" tick={{ fontSize: 10 }} angle={-25} textAnchor="end" />
                        <YAxis tick={{ fontSize: 11 }} unit=" ms" />
                        <Tooltip formatter={(v: unknown) => typeof v === "number" ? `${v.toFixed(0)} ms` : String(v)} />
                        <Bar dataKey="p50" fill="#3b82f6" name="p50" radius={[4, 4, 0, 0]} />
                        <Bar dataKey="p95" fill="#a855f7" name="p95" radius={[4, 4, 0, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                )}
              </CardContent>
            </Card>

            {/* Detalle latencias en tabla */}
            <Card variant="glass" className="mb-6">
              <CardHeader>
                <CardTitle className="text-base">Detalle por acción</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-left text-slate-500 border-b border-slate-200">
                        <th className="py-2 pr-4">Agente</th>
                        <th className="py-2 pr-4">Acción</th>
                        <th className="py-2 pr-4 text-right">Count</th>
                        <th className="py-2 pr-4 text-right">p50</th>
                        <th className="py-2 pr-4 text-right">p95</th>
                        <th className="py-2 text-right">max</th>
                      </tr>
                    </thead>
                    <tbody>
                      {report.latencies.length === 0 ? (
                        <tr><td colSpan={6} className="py-4 text-center text-slate-400">Sin datos</td></tr>
                      ) : (
                        report.latencies.map((l, i) => (
                          <tr key={i} className="border-b border-slate-100">
                            <td className="py-2 pr-4 font-mono text-xs text-slate-600">{l.agent}</td>
                            <td className="py-2 pr-4 font-mono text-xs text-slate-700">{l.action}</td>
                            <td className="py-2 pr-4 text-right tabular-nums">{l.count}</td>
                            <td className="py-2 pr-4 text-right tabular-nums">{formatMs(l.p50_ms)}</td>
                            <td className="py-2 pr-4 text-right tabular-nums">{formatMs(l.p95_ms)}</td>
                            <td className="py-2 text-right tabular-nums">{formatMs(l.max_ms)}</td>
                          </tr>
                        ))
                      )}
                    </tbody>
                  </table>
                </div>
              </CardContent>
            </Card>

            {/* Top errores */}
            <Card variant="glass">
              <CardHeader>
                <CardTitle className="text-base">Top 5 errores en la ventana</CardTitle>
              </CardHeader>
              <CardContent>
                {report.top_errors.length === 0 ? (
                  <p className="text-sm text-slate-500 text-center py-4">
                    🎉 Sin errores en esta ventana.
                  </p>
                ) : (
                  <ul className="space-y-2">
                    {report.top_errors.map((e, i) => (
                      <li key={i} className="flex items-center justify-between bg-red-50 border border-red-200 rounded-md px-3 py-2">
                        <span className="font-mono text-xs text-red-800">
                          {e.agent}.{e.action}
                        </span>
                        <span className="text-sm font-semibold text-red-700">{e.error_count}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </CardContent>
            </Card>

            {report.note && (
              <p className="mt-4 text-xs text-slate-500 italic">Nota: {report.note}</p>
            )}
          </>
        )}

        {/* Analizador de logs (tabla `events`) */}
        <Card variant="glass" className="mt-6">
          <CardHeader>
            <CardTitle className="text-base flex items-center justify-between flex-wrap gap-2">
              <span>Analizador de logs</span>
              <span className="text-xs font-normal text-slate-500">
                Últimos eventos en la BD · sin PII en claro
              </span>
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex flex-wrap items-center gap-2 mb-3">
              <select
                value={logStatus}
                onChange={(e) => setLogStatus(e.target.value)}
                className="text-sm border border-slate-300 rounded-md px-2 py-1 bg-white"
              >
                <option value="">Todos los status</option>
                <option value="ok">ok</option>
                <option value="error">error</option>
                <option value="warning">warning</option>
                <option value="denied">denied</option>
              </select>
              <select
                value={logAgent}
                onChange={(e) => setLogAgent(e.target.value)}
                className="text-sm border border-slate-300 rounded-md px-2 py-1 bg-white"
              >
                <option value="">Todos los agentes</option>
                <option value="orchestrator">orchestrator</option>
                <option value="conversational">conversational</option>
                <option value="analyst">analyst</option>
                <option value="registrar">registrar</option>
                <option value="security">security</option>
                <option value="monitor">monitor</option>
                <option value="api">api</option>
              </select>
              <select
                value={logLimit}
                onChange={(e) => setLogLimit(Number(e.target.value))}
                className="text-sm border border-slate-300 rounded-md px-2 py-1 bg-white"
              >
                <option value={25}>25 últimos</option>
                <option value={50}>50 últimos</option>
                <option value={100}>100 últimos</option>
                <option value={250}>250 últimos</option>
              </select>
              <Button variant="secondary" size="sm" onClick={refreshLogs} disabled={logsLoading}>
                {logsLoading ? "Cargando…" : "Refrescar logs"}
              </Button>
              <span className="text-xs text-slate-500">{events.length} eventos</span>
            </div>

            {logsError && (
              <p className="text-sm text-red-600 mb-2">Error: {logsError}</p>
            )}

            <div className="overflow-x-auto max-h-96 overflow-y-auto border border-slate-200 rounded-md">
              <table className="w-full text-xs">
                <thead className="bg-slate-50 sticky top-0 border-b border-slate-200 text-slate-600">
                  <tr>
                    <th className="px-3 py-2 text-left">Timestamp</th>
                    <th className="px-3 py-2 text-left">Agente</th>
                    <th className="px-3 py-2 text-left">Acción</th>
                    <th className="px-3 py-2 text-left">Status</th>
                    <th className="px-3 py-2 text-right">Lat (ms)</th>
                    <th className="px-3 py-2 text-left">User</th>
                    <th className="px-3 py-2 text-left">Payload</th>
                  </tr>
                </thead>
                <tbody>
                  {events.length === 0 ? (
                    <tr><td colSpan={7} className="px-3 py-4 text-center text-slate-400">
                      Sin eventos con esos filtros.
                    </td></tr>
                  ) : events.map((e, i) => (
                    <tr key={i} className="border-b border-slate-100">
                      <td className="px-3 py-1.5 font-mono whitespace-nowrap text-slate-600">
                        {new Date(e.ts).toLocaleString("es-ES", { hour12: false })}
                      </td>
                      <td className="px-3 py-1.5 font-mono text-slate-700">{e.agent}</td>
                      <td className="px-3 py-1.5 font-mono text-slate-700">{e.action}</td>
                      <td className="px-3 py-1.5">
                        <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${
                          e.status === "ok" ? "bg-green-100 text-green-700" :
                          e.status === "error" ? "bg-red-100 text-red-700" :
                          e.status === "warning" ? "bg-amber-100 text-amber-700" :
                                                    "bg-slate-100 text-slate-700"
                        }`}>{e.status}</span>
                      </td>
                      <td className="px-3 py-1.5 text-right tabular-nums text-slate-600">
                        {e.latency_ms ?? "—"}
                      </td>
                      <td className="px-3 py-1.5 font-mono text-slate-500">
                        {e.user_id ? e.user_id.slice(0, 8) + "…" : "—"}
                      </td>
                      <td className="px-3 py-1.5 font-mono text-slate-500 max-w-md truncate">
                        {e.payload ? JSON.stringify(e.payload) : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      </main>
    </div>
  );
}


function KPI({ title, value, subtitle, accent }: {
  title: string;
  value: string;
  subtitle?: string;
  accent?: "green" | "amber" | "red";
}) {
  const accentClass = accent === "red"
    ? "text-red-600"
    : accent === "amber"
    ? "text-amber-600"
    : accent === "green"
    ? "text-green-600"
    : "text-slate-800";
  return (
    <Card variant="glass">
      <CardContent className="p-4">
        <p className="text-xs uppercase tracking-wide text-slate-500">{title}</p>
        <p className={`text-2xl font-bold mt-1 ${accentClass}`}>{value}</p>
        {subtitle && <p className="text-xs text-slate-500 mt-1">{subtitle}</p>}
      </CardContent>
    </Card>
  );
}


function StatusBadge({ label, count, cls }: { label: string; count: number; cls: string }) {
  return (
    <div className={`px-3 py-2 rounded-md border ${cls} text-center`}>
      <p className="font-mono">{label}</p>
      <p className="font-semibold tabular-nums mt-0.5">{count}</p>
    </div>
  );
}
