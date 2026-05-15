"use client";

import { useState } from "react";
import {
  Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart, Pie, PieChart,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";

import type { ChartSpec } from "@/lib/api";

/**
 * Renderiza el `ChartSpec` que el backend devuelve junto a la respuesta
 * del chat (Fase 4 — diagramas dinámicos + XAI).
 *
 * - Detecta el tipo (line/bar/pie/area) y elige el componente Recharts.
 * - Bloque "¿Por qué este gráfico?" colapsable con la explicación XAI.
 */

type Props = {
  chart: ChartSpec;
};

const PIE_COLORS = [
  "#3b82f6", "#a855f7", "#10b981", "#f59e0b",
  "#ef4444", "#06b6d4", "#ec4899", "#84cc16",
];

// Formatter del Tooltip de Recharts: el value llega tipado como
// `string | number | (string|number)[] | undefined`, así que aceptamos
// `unknown` y solo formateamos cuando es número.
function formatEuro(value: unknown): string {
  if (typeof value === "number") return `${value.toFixed(2)}€`;
  if (typeof value === "string") return value;
  return "";
}

export function ChatChart({ chart }: Props) {
  const [explanationOpen, setExplanationOpen] = useState(false);

  const data = chart.data ?? [];
  if (data.length === 0) return null;

  return (
    <div className="mt-3 bg-white/80 border border-slate-200/60 rounded-xl p-3 shadow-sm">
      <p className="text-xs font-semibold text-slate-700 mb-2">{chart.title}</p>

      <div className="w-full h-56">
        <ResponsiveContainer width="100%" height="100%">
          {renderChart(chart.type, data)}
        </ResponsiveContainer>
      </div>

      {chart.explanation && (
        <div className="mt-2 border-t border-slate-100 pt-2">
          <button
            type="button"
            onClick={() => setExplanationOpen((v) => !v)}
            className="text-xs text-slate-500 hover:text-slate-700 flex items-center gap-1"
          >
            <span>{explanationOpen ? "▾" : "▸"}</span>
            <span className="font-medium">¿Por qué este gráfico?</span>
          </button>
          {explanationOpen && (
            <p className="text-xs text-slate-600 mt-1 leading-relaxed">
              {chart.explanation}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

function renderChart(
  type: string,
  data: { label: string; value: number }[],
) {
  if (type === "pie") {
    return (
      <PieChart>
        <Pie
          data={data}
          dataKey="value"
          nameKey="label"
          cx="50%" cy="50%"
          outerRadius="80%"
          label
        >
          {data.map((_, i) => (
            <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />
          ))}
        </Pie>
        <Tooltip formatter={formatEuro} />
        <Legend />
      </PieChart>
    );
  }

  if (type === "line") {
    return (
      <LineChart data={data}>
        <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
        <XAxis dataKey="label" tick={{ fontSize: 11 }} />
        <YAxis tick={{ fontSize: 11 }} />
        <Tooltip formatter={formatEuro} />
        <Line type="monotone" dataKey="value" stroke="#3b82f6" strokeWidth={2}
              dot={{ r: 3, fill: "#3b82f6" }} />
      </LineChart>
    );
  }

  // default: bar
  return (
    <BarChart data={data}>
      <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
      <XAxis dataKey="label" tick={{ fontSize: 11 }} />
      <YAxis tick={{ fontSize: 11 }} />
      <Tooltip formatter={formatEuro} />
      <Bar dataKey="value" fill="#a855f7" radius={[4, 4, 0, 0]} />
    </BarChart>
  );
}
