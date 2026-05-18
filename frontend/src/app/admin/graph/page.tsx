"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import {
  clearToken, getAdminAgentGraph, getAdminAgentGraphDot, getIsAdmin,
  type AdminAgentGraphResponse, type AgentGraph,
} from "@/lib/api";
import { GraphView, agentGraphToDot } from "@/components/GraphView";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";


const WINDOW_OPTIONS = [
  { value: 1, label: "1 h" },
  { value: 6, label: "6 h" },
  { value: 24, label: "24 h" },
  { value: 24 * 7, label: "7 días" },
];


export default function AdminGraphPage() {
  const router = useRouter();
  const [data, setData] = useState<AdminAgentGraphResponse | null>(null);
  const [dot, setDot] = useState<string>("");
  const [windowHours, setWindowHours] = useState(24);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!getIsAdmin()) router.replace("/login");
  }, [router]);

  const refresh = useCallback(async (hours: number) => {
    setLoading(true);
    setError(null);
    try {
      const [json, dotText] = await Promise.all([
        getAdminAgentGraph(hours),
        getAdminAgentGraphDot(hours),
      ]);
      setData(json);
      setDot(dotText);
    } catch (err) {
      const msg = (err as Error).message;
      setError(msg);
      if (msg.toLowerCase().includes("token") || msg.includes("401")) {
        clearToken();
        router.replace("/login");
      } else if (msg.includes("403")) {
        router.replace("/login");
      }
    } finally {
      setLoading(false);
    }
  }, [router]);

  useEffect(() => {
    refresh(windowHours);
  }, [windowHours, refresh]);

  return (
    <div className="mx-auto max-w-7xl space-y-4 p-4">
      <Card>
        <CardHeader>
          <CardTitle>Grafo del sistema (admin)</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-sm text-slate-600">
            Vista system-wide del flujo entre agentes, enriquecida con métricas
            de Langfuse SaaS, snapshot del MonitorAgent y análisis del fichero
            <span className="font-mono"> logs/app.log</span>.
          </p>

          <div className="flex flex-wrap items-center gap-3">
            <span className="text-sm font-medium">Ventana:</span>
            {WINDOW_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                type="button"
                onClick={() => setWindowHours(opt.value)}
                className={`rounded border px-3 py-1 text-sm ${
                  windowHours === opt.value
                    ? "border-blue-500 bg-blue-50 text-blue-700"
                    : "border-slate-300 bg-white hover:bg-slate-50"
                }`}
              >
                {opt.label}
              </button>
            ))}
            <Button onClick={() => refresh(windowHours)} disabled={loading}>
              {loading ? "Cargando..." : "Refrescar"}
            </Button>
          </div>

          {error && (
            <div className="rounded border border-red-300 bg-red-50 p-3 text-sm text-red-700">
              {error}
            </div>
          )}

          {data && (
            <>
              <KpiRow data={data} />

              <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
                <div>
                  <h3 className="mb-1 text-sm font-semibold text-slate-700">
                    Grafo de aplicación
                    <span className="ml-2 text-xs font-normal text-slate-500">
                      orchestrator · analyst · registrar · security · …
                    </span>
                  </h3>
                  <GraphView dot={dot} />
                </div>
                <div>
                  <h3 className="mb-1 text-sm font-semibold text-slate-700">
                    Grafo de operaciones
                    <span className="ml-2 text-xs font-normal text-violet-600">
                      admin_orchestrator · observability
                    </span>
                  </h3>
                  <GraphView
                    dot={agentGraphToDot(data.graph_ops, `System (ops) — last ${windowHours}h`)}
                  />
                </div>
              </div>

              <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                <LangfuseCard data={data} />
                <LogsCard data={data} />
              </div>

              <NodesTable graph={data.graph_app} title="Detalle por agente (app)" />
              <NodesTable graph={data.graph_ops} title="Detalle por agente (ops)" />
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}


function KpiRow({ data }: { data: AdminAgentGraphResponse }) {
  const monitor = data.meta.monitor;
  const lf = data.meta.langfuse;
  const errorRate =
    monitor && monitor.total_events > 0
      ? `${(monitor.error_rate * 100).toFixed(1)}%`
      : "—";
  const totalApp = data.graph_app.meta.total_events;
  const totalOps = data.graph_ops.meta.total_events;
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
      <Kpi label="Eventos app" value={totalApp.toString()} />
      <Kpi label="Eventos ops" value={totalOps.toString()} />
      <Kpi label="Error rate" value={errorRate} />
      <Kpi
        label="Salud"
        value={monitor?.health ?? "—"}
        tone={
          monitor?.health === "critical"
            ? "red"
            : monitor?.health === "warning"
            ? "amber"
            : "green"
        }
      />
      <Kpi
        label="Coste Langfuse"
        value={lf?.ok ? `$${lf.total_cost_usd.toFixed(4)}` : "—"}
      />
    </div>
  );
}


function LangfuseCard({ data }: { data: AdminAgentGraphResponse }) {
  const lf = data.meta.langfuse;
  if (!lf) {
    return null;
  }
  if (!lf.enabled) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Langfuse</CardTitle>
        </CardHeader>
        <CardContent className="text-sm text-slate-600">
          {lf.note ?? "Langfuse deshabilitado."}
        </CardContent>
      </Card>
    );
  }
  if (!lf.ok) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Langfuse</CardTitle>
        </CardHeader>
        <CardContent className="text-sm text-red-700">
          Error consultando Langfuse: {lf.note}
        </CardContent>
      </Card>
    );
  }
  return (
    <Card>
      <CardHeader>
        <CardTitle>Langfuse SaaS</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2 text-sm">
        <div>Traces: <b>{lf.traces_count}</b> · Observaciones: <b>{lf.observations_count}</b></div>
        <div>Tokens in/out: <b>{lf.total_tokens_in}</b> / <b>{lf.total_tokens_out}</b></div>
        <div>Coste total: <b>${lf.total_cost_usd.toFixed(6)}</b></div>
        {lf.models_used.length > 0 && (
          <div>
            <div className="text-xs uppercase text-slate-500">Modelos</div>
            <ul className="ml-4 list-disc">
              {lf.models_used.map((m) => (
                <li key={m.model}>
                  <span className="font-mono">{m.model}</span>
                  {" — "}
                  in: {m.tokens_in} · out: {m.tokens_out}
                  {m.cost_usd > 0 && ` · $${m.cost_usd.toFixed(6)}`}
                </li>
              ))}
            </ul>
          </div>
        )}
        {lf.top_trace_names.length > 0 && (
          <div>
            <div className="text-xs uppercase text-slate-500">Endpoints más usados</div>
            <ul className="ml-4 list-disc">
              {lf.top_trace_names.map((t) => (
                <li key={t.name}><span className="font-mono">{t.name}</span> · {t.count}</li>
              ))}
            </ul>
          </div>
        )}
      </CardContent>
    </Card>
  );
}


function LogsCard({ data }: { data: AdminAgentGraphResponse }) {
  const logs = data.meta.logs;
  if (!logs) return null;
  if (logs.note) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Logs</CardTitle>
        </CardHeader>
        <CardContent className="text-sm text-slate-600">{logs.note}</CardContent>
      </Card>
    );
  }
  return (
    <Card>
      <CardHeader>
        <CardTitle>Análisis de logs ({logs.window_hours}h)</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2 text-sm">
        <div>Líneas analizadas: <b>{logs.lines_parsed}</b> / {logs.lines_scanned}</div>
        <div className="flex flex-wrap gap-2">
          {Object.entries(logs.counts_by_level).map(([level, c]) => (
            <span
              key={level}
              className={`rounded px-2 py-0.5 text-xs ${
                level === "ERROR" || level === "CRITICAL"
                  ? "bg-red-100 text-red-700"
                  : level === "WARNING"
                  ? "bg-amber-100 text-amber-700"
                  : "bg-slate-100 text-slate-700"
              }`}
            >
              {level}: {c}
            </span>
          ))}
        </div>
        {logs.anomalies.length > 0 && (
          <div>
            <div className="text-xs uppercase text-slate-500">Anomalías detectadas</div>
            <ul className="ml-4 list-disc">
              {logs.anomalies.map((a, idx) => (
                <li
                  key={idx}
                  className={
                    a.severity === "critical" ? "text-red-700" : "text-amber-700"
                  }
                >
                  <span className="font-semibold">[{a.kind}]</span> {a.description}
                  {a.sample_message && (
                    <span className="ml-1 font-mono text-xs text-slate-500">
                      &quot;{a.sample_message.slice(0, 80)}&quot;
                    </span>
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}
        {logs.top_modules_with_errors.length > 0 && (
          <div>
            <div className="text-xs uppercase text-slate-500">Top módulos con errores</div>
            <ul className="ml-4 list-disc">
              {logs.top_modules_with_errors.slice(0, 5).map((m) => (
                <li key={m.module}><span className="font-mono">{m.module}</span> · {m.count}</li>
              ))}
            </ul>
          </div>
        )}
      </CardContent>
    </Card>
  );
}


function NodesTable({ graph, title }: { graph: AgentGraph; title: string }) {
  if (graph.nodes.length === 0) return null;
  return (
    <div className="overflow-auto">
      <h3 className="mb-2 text-sm font-semibold">{title}</h3>
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b text-left text-xs uppercase text-slate-500">
            <th className="py-1">Agente</th>
            <th>Eventos</th>
            <th>Errores</th>
            <th>Error rate</th>
            <th>Usuarios distintos</th>
            <th>Latencia media</th>
          </tr>
        </thead>
        <tbody>
          {graph.nodes.map((n) => (
            <tr key={n.id} className={`border-b ${n.is_ops ? "bg-violet-50" : ""}`}>
              <td className="py-1 font-mono">{n.agent}</td>
              <td>{n.count}</td>
              <td>{n.error_count}</td>
              <td>{(n.error_rate * 100).toFixed(1)}%</td>
              <td>{n.users_distinct ?? "—"}</td>
              <td>{n.avg_latency_ms != null ? `${n.avg_latency_ms} ms` : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}


function Kpi({
  label, value, tone,
}: { label: string; value: string; tone?: "red" | "amber" | "green" }) {
  const toneClass =
    tone === "red"
      ? "bg-red-50 border-red-300 text-red-800"
      : tone === "amber"
      ? "bg-amber-50 border-amber-300 text-amber-800"
      : tone === "green"
      ? "bg-emerald-50 border-emerald-300 text-emerald-800"
      : "bg-slate-50 border-slate-200";
  return (
    <div className={`rounded border p-3 ${toneClass}`}>
      <div className="text-xs uppercase opacity-70">{label}</div>
      <div className="text-lg font-semibold">{value}</div>
    </div>
  );
}
