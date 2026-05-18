"use client";

import { useEffect, useRef, useState } from "react";
import { instance } from "@viz-js/viz";
import type { AgentGraph } from "@/lib/api";

type Props = {
  dot: string;
  className?: string;
};


/**
 * Convierte un `AgentGraph` JSON a notación DOT en cliente.
 *
 * Espejo simplificado del `graph_to_dot` del backend. Se usa cuando el
 * frontend ya tiene el grafo como JSON (p. ej. el `graph_ops` del admin,
 * que el endpoint `?format=dot` no expone) y queremos renderizarlo sin
 * un segundo round-trip.
 */
export function agentGraphToDot(graph: AgentGraph, title: string): string {
  const lines: string[] = [
    `digraph "${title.replace(/"/g, '\\"')}" {`,
    "  rankdir=LR;",
    "  node [shape=box, style=\"rounded,filled\", fontname=\"Helvetica\"];",
    "  edge [fontname=\"Helvetica\", fontsize=10];",
  ];

  for (const n of graph.nodes) {
    const labelParts: string[] = [n.id, `count=${n.count}`];
    if (n.avg_latency_ms != null) labelParts.push(`avg=${n.avg_latency_ms}ms`);
    if (n.error_rate) labelParts.push(`err=${(n.error_rate * 100).toFixed(1)}%`);
    const label = labelParts.join("\\n");
    const fill = nodeColor(n);
    const id = n.id.replace(/"/g, '\\"');
    lines.push(`  "${id}" [label="${label}", fillcolor="${fill}"];`);
  }

  for (const e of graph.edges) {
    const parts = [`n=${e.count}`];
    if (e.avg_latency_ms != null) parts.push(`${e.avg_latency_ms}ms`);
    const label = parts.join(" · ");
    const s = e.source.replace(/"/g, '\\"');
    const t = e.target.replace(/"/g, '\\"');
    lines.push(`  "${s}" -> "${t}" [label="${label}"];`);
  }
  lines.push("}");
  return lines.join("\n");
}


function nodeColor(n: AgentGraph["nodes"][number]): string {
  const err = n.error_rate ?? 0;
  if (err >= 0.20) return "#fca5a5";
  if (err >= 0.05) return "#fde68a";
  if (n.is_ops) return "#ddd6fe";  // morado claro: agentes de ops
  if (n.role === "advanced") return "#bfdbfe";
  if (n.role === "basic") return "#bbf7d0";
  return "#e5e7eb";
}

/**
 * Renderiza un grafo en notación Graphviz DOT al SVG correspondiente
 * usando viz-js (WebAssembly, 100% client-side). Permite descargar el
 * SVG resultante. No depende del binario `dot` del sistema.
 */
export function GraphView({ dot, className }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [svgText, setSvgText] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    setSvgText(null);

    if (!dot.trim()) {
      return;
    }

    instance()
      .then((viz) => {
        if (cancelled) return;
        const svg = viz.renderString(dot, { format: "svg" });
        setSvgText(svg);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError((err as Error).message);
      });

    return () => {
      cancelled = true;
    };
  }, [dot]);

  function downloadSvg() {
    if (!svgText) return;
    const blob = new Blob([svgText], { type: "image/svg+xml" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "agent-graph.svg";
    a.click();
    URL.revokeObjectURL(url);
  }

  function downloadDot() {
    const blob = new Blob([dot], { type: "text/vnd.graphviz" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "agent-graph.dot";
    a.click();
    URL.revokeObjectURL(url);
  }

  if (error) {
    return (
      <div className="rounded border border-red-300 bg-red-50 p-3 text-sm text-red-700">
        Error renderizando grafo: {error}
      </div>
    );
  }

  if (!svgText) {
    return (
      <div className="flex h-48 items-center justify-center text-sm text-gray-500">
        {dot.trim() ? "Renderizando..." : "Sin datos para mostrar."}
      </div>
    );
  }

  return (
    <div className={className}>
      <div
        ref={containerRef}
        className="overflow-auto rounded border bg-white p-2"
        dangerouslySetInnerHTML={{ __html: svgText }}
      />
      <div className="mt-2 flex gap-2">
        <button
          type="button"
          onClick={downloadSvg}
          className="rounded border bg-gray-50 px-3 py-1 text-xs hover:bg-gray-100"
        >
          Descargar SVG
        </button>
        <button
          type="button"
          onClick={downloadDot}
          className="rounded border bg-gray-50 px-3 py-1 text-xs hover:bg-gray-100"
        >
          Descargar DOT
        </button>
      </div>
    </div>
  );
}
