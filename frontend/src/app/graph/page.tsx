"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import {
  clearToken, getMyAgentGraph, getMyAgentGraphDot,
  type UserAgentGraphResponse,
} from "@/lib/api";
import { GraphView } from "@/components/GraphView";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";


const WINDOW_OPTIONS = [
  { value: 1, label: "1 h" },
  { value: 6, label: "6 h" },
  { value: 24, label: "24 h" },
  { value: 24 * 7, label: "7 días" },
  { value: 24 * 30, label: "30 días" },
];


export default function UserGraphPage() {
  const router = useRouter();
  const [data, setData] = useState<UserAgentGraphResponse | null>(null);
  const [dot, setDot] = useState<string>("");
  const [windowHours, setWindowHours] = useState(24);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async (hours: number) => {
    setLoading(true);
    setError(null);
    try {
      const [json, dotText] = await Promise.all([
        getMyAgentGraph(hours),
        getMyAgentGraphDot(hours),
      ]);
      setData(json);
      setDot(dotText);
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

  useEffect(() => {
    refresh(windowHours);
  }, [windowHours, refresh]);

  return (
    <div className="mx-auto max-w-6xl space-y-4 p-4">
      <Card>
        <CardHeader>
          <CardTitle>Mi grafo agéntico</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-sm text-slate-600">
            Visualiza cómo el sistema multiagente procesa tus consultas
            financieras. Los nodos están etiquetados con el rol de respuesta
            (<span className="font-mono">basic</span> o
            <span className="font-mono"> advanced</span>) que tenías activo
            durante cada interacción.
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
            <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
              <Kpi label="Rol activo" value={data.role ?? "—"} />
              <Kpi label="Eventos" value={data.graph.meta.total_events.toString()} />
              <Kpi label="Sesiones" value={data.graph.meta.total_sessions.toString()} />
            </div>
          )}

          <GraphView dot={dot} className="mt-2" />

          {data && data.graph.nodes.length > 0 && (
            <div className="overflow-auto">
              <h3 className="mb-2 text-sm font-semibold">Detalle por nodo</h3>
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-left text-xs uppercase text-slate-500">
                    <th className="py-1">Nodo</th>
                    <th>Rol</th>
                    <th>Eventos</th>
                    <th>Errores</th>
                    <th>Latencia media</th>
                  </tr>
                </thead>
                <tbody>
                  {data.graph.nodes.map((n) => (
                    <tr key={n.id} className="border-b">
                      <td className="py-1 font-mono">{n.agent}</td>
                      <td>{n.role ?? "—"}</td>
                      <td>{n.count}</td>
                      <td>{n.error_count}</td>
                      <td>{n.avg_latency_ms != null ? `${n.avg_latency_ms} ms` : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}


function Kpi({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded border border-slate-200 bg-slate-50 p-3">
      <div className="text-xs uppercase text-slate-500">{label}</div>
      <div className="text-lg font-semibold">{value}</div>
    </div>
  );
}
