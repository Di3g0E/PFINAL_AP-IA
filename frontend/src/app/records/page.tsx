"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";

import {
  clearToken,
  getIsAdmin,
  getUserId,
  listTransactions,
  type TransactionRecord,
} from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Card, CardContent } from "@/components/ui/Card";
import { formatCurrency, formatDate } from "@/lib/utils";
import { RecordModal } from "@/components/RecordModal";

type SortKey = "date" | "amount" | "description" | "type" | "area" | "status";
type SortDir = "asc" | "desc";

const PAGE_SIZE = 50;

export default function RecordsPage() {
  const router = useRouter();
  const [items, setItems] = useState<TransactionRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isModalOpen, setIsModalOpen] = useState(false);

  // Estado tabla: orden + búsqueda + filtro tipo + paginación
  const [sortKey, setSortKey] = useState<SortKey>("date");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [query, setQuery] = useState("");
  const [typeFilter, setTypeFilter] = useState<"all" | "Income" | "Expenses">("all");
  const [page, setPage] = useState(1);

  const refresh = useCallback(async () => {
    setError(null);
    setLoading(true);
    try {
      const list = await listTransactions();
      setItems(list);
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
    if (!getUserId()) {
      router.replace("/login");
      return;
    }
    if (getIsAdmin()) {
      router.replace("/admin/monitor");
      return;
    }
    refresh();
  }, [router, refresh]);

  const onSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      // Por defecto: fechas e importes en desc; texto en asc
      setSortDir(key === "date" || key === "amount" ? "desc" : "asc");
    }
    setPage(1);
  };

  // KPIs derivados (sobre TODO el set, no la página)
  const kpis = useMemo(() => {
    let income = 0;
    let expenses = 0;
    for (const it of items) {
      const a = Number(it.amount);
      if (it.type === "Income") income += a;
      else expenses += a;
    }
    return { income, expenses, net: income - expenses, count: items.length };
  }, [items]);

  const filteredAndSorted = useMemo(() => {
    const q = query.trim().toLowerCase();
    const filtered = items.filter((it) => {
      if (typeFilter !== "all" && it.type !== typeFilter) return false;
      if (!q) return true;
      const areasStr = (it.area ?? []).join(" ").toLowerCase();
      return (
        it.description.toLowerCase().includes(q) ||
        areasStr.includes(q) ||
        it.status.toLowerCase().includes(q)
      );
    });

    const cmp = (a: TransactionRecord, b: TransactionRecord): number => {
      let r = 0;
      switch (sortKey) {
        case "date":
          r = a.date.localeCompare(b.date);
          break;
        case "amount":
          r = Number(a.amount) - Number(b.amount);
          break;
        case "description":
          r = a.description.localeCompare(b.description, "es");
          break;
        case "type":
          r = a.type.localeCompare(b.type);
          break;
        case "area":
          r = (a.area?.[0] ?? "").localeCompare(b.area?.[0] ?? "", "es");
          break;
        case "status":
          r = a.status.localeCompare(b.status);
          break;
      }
      return sortDir === "asc" ? r : -r;
    };
    return [...filtered].sort(cmp);
  }, [items, query, typeFilter, sortKey, sortDir]);

  const totalPages = Math.max(1, Math.ceil(filteredAndSorted.length / PAGE_SIZE));
  const pageItems = filteredAndSorted.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

  return (
    <div className="min-h-screen bg-gradient-to-br from-blue-50 via-white to-purple-50">
      <main className="max-w-6xl mx-auto px-4 py-6">
        <div className="flex items-start justify-between mb-6 flex-wrap gap-3">
          <div>
            <h1 className="text-2xl font-bold gradient-text">Registros</h1>
            <p className="text-sm text-slate-500 mt-1">
              Todos tus ingresos y gastos consolidados
            </p>
          </div>
          <div className="flex gap-2">
            <Button variant="secondary" size="sm" onClick={refresh}>Refrescar</Button>
            <Button variant="primary" size="sm" onClick={() => setIsModalOpen(true)}>
              + Nuevo registro
            </Button>
          </div>
        </div>

        {/* KPIs */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-5">
          <Kpi label="Total" value={kpis.count.toString()} />
          <Kpi label="Ingresos" value={formatCurrency(kpis.income)} accent="green" />
          <Kpi label="Gastos" value={formatCurrency(kpis.expenses)} accent="red" />
          <Kpi label="Neto"
               value={formatCurrency(kpis.net)}
               accent={kpis.net >= 0 ? "green" : "red"} />
        </div>

        {error && (
          <Card variant="default" className="border-red-200 bg-red-50 mb-4">
            <CardContent className="p-4">
              <p className="text-sm font-medium text-red-800">Error</p>
              <p className="text-sm text-red-600">{error}</p>
            </CardContent>
          </Card>
        )}

        {/* Filtros */}
        <Card variant="glass" className="mb-3">
          <CardContent className="p-3 flex flex-wrap items-center gap-3">
            <input
              type="search"
              placeholder="Buscar por descripción, categoría o estado…"
              value={query}
              onChange={(e) => { setQuery(e.target.value); setPage(1); }}
              className="flex-1 min-w-[220px] text-sm border border-slate-300 rounded-md px-3 py-2 bg-white"
            />
            <select
              value={typeFilter}
              onChange={(e) => { setTypeFilter(e.target.value as typeof typeFilter); setPage(1); }}
              className="text-sm border border-slate-300 rounded-md px-3 py-2 bg-white"
            >
              <option value="all">Todos</option>
              <option value="Income">Solo ingresos</option>
              <option value="Expenses">Solo gastos</option>
            </select>
            <span className="text-xs text-slate-500">
              {filteredAndSorted.length} de {items.length} registros
            </span>
          </CardContent>
        </Card>

        {/* Tabla */}
        <Card variant="glass">
          <CardContent className="p-0 overflow-x-auto">
            {loading ? (
              <div className="py-12 text-center text-sm text-slate-500">Cargando registros…</div>
            ) : items.length === 0 ? (
              <div className="py-12 text-center text-sm text-slate-500">
                Todavía no has añadido ninguna transacción.
              </div>
            ) : (
              <table className="w-full text-sm">
                <thead className="bg-slate-50/80 border-b border-slate-200 text-slate-600">
                  <tr>
                    <Th label="Fecha" col="date" sortKey={sortKey} sortDir={sortDir} onSort={onSort} className="w-28" />
                    <Th label="Descripción" col="description" sortKey={sortKey} sortDir={sortDir} onSort={onSort} />
                    <Th label="Categoría" col="area" sortKey={sortKey} sortDir={sortDir} onSort={onSort} className="w-44" />
                    <Th label="Tipo" col="type" sortKey={sortKey} sortDir={sortDir} onSort={onSort} className="w-24" />
                    <Th label="Estado" col="status" sortKey={sortKey} sortDir={sortDir} onSort={onSort} className="w-28" />
                    <Th label="Importe" col="amount" sortKey={sortKey} sortDir={sortDir} onSort={onSort} className="w-32 text-right" align="right" />
                  </tr>
                </thead>
                <tbody>
                  {pageItems.map((it) => (
                    <tr key={it.id} className="border-b border-slate-100 hover:bg-slate-50/60">
                      <td className="px-3 py-2 whitespace-nowrap text-xs text-slate-600 tabular-nums">
                        {formatDate(it.date)}
                      </td>
                      <td className="px-3 py-2 text-slate-800">{it.description}</td>
                      <td className="px-3 py-2">
                        <div className="flex flex-wrap gap-1">
                          {(it.area ?? []).map((a) => (
                            <span key={a} className="inline-flex px-2 py-0.5 rounded text-xs bg-slate-100 text-slate-700">
                              {a}
                            </span>
                          ))}
                        </div>
                      </td>
                      <td className="px-3 py-2">
                        <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                          it.type === "Income" ? "bg-green-100 text-green-700" : "bg-red-100 text-red-700"
                        }`}>
                          {it.type === "Income" ? "Ingreso" : "Gasto"}
                        </span>
                      </td>
                      <td className="px-3 py-2">
                        <span className={`text-xs ${
                          it.status === "accepted" ? "text-slate-500" :
                          it.status === "pending"  ? "text-amber-700" :
                                                     "text-red-700"
                        }`}>
                          {it.status === "accepted" ? "✓ aceptado" :
                           it.status === "pending"  ? "⏳ pendiente" :
                                                       "✗ rechazado"}
                        </span>
                      </td>
                      <td className={`px-3 py-2 text-right font-semibold tabular-nums ${
                        it.type === "Income" ? "text-green-600" : "text-slate-800"
                      }`}>
                        {it.type === "Income" ? "+" : "-"}{formatCurrency(Number(it.amount))}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </CardContent>
        </Card>

        {/* Paginación */}
        {filteredAndSorted.length > PAGE_SIZE && (
          <div className="flex items-center justify-between mt-3 text-sm">
            <span className="text-slate-500">
              Página {page} de {totalPages}
            </span>
            <div className="flex gap-2">
              <Button variant="secondary" size="sm"
                disabled={page === 1}
                onClick={() => setPage((p) => Math.max(1, p - 1))}>
                ← Anterior
              </Button>
              <Button variant="secondary" size="sm"
                disabled={page === totalPages}
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}>
                Siguiente →
              </Button>
            </div>
          </div>
        )}
      </main>

      <RecordModal
        open={isModalOpen}
        onClose={() => setIsModalOpen(false)}
        onSuccess={() => {
          setIsModalOpen(false);
          refresh();
        }}
      />
    </div>
  );
}


function Th({
  label, col, sortKey, sortDir, onSort, className = "", align = "left",
}: {
  label: string;
  col: SortKey;
  sortKey: SortKey;
  sortDir: SortDir;
  onSort: (k: SortKey) => void;
  className?: string;
  align?: "left" | "right";
}) {
  const active = sortKey === col;
  const arrow = !active ? "↕" : sortDir === "asc" ? "↑" : "↓";
  return (
    <th className={`px-3 py-2 font-medium ${align === "right" ? "text-right" : "text-left"} ${className}`}>
      <button
        type="button"
        onClick={() => onSort(col)}
        className={`inline-flex items-center gap-1 hover:text-blue-600 ${active ? "text-blue-600" : ""}`}
      >
        {label}
        <span className="text-[10px]">{arrow}</span>
      </button>
    </th>
  );
}


function Kpi({ label, value, accent }: { label: string; value: string; accent?: "green" | "red" }) {
  const cls = accent === "green" ? "text-green-700" : accent === "red" ? "text-red-700" : "text-slate-800";
  return (
    <Card variant="glass">
      <CardContent className="p-3">
        <p className="text-xs uppercase tracking-wide text-slate-500">{label}</p>
        <p className={`text-lg font-bold mt-0.5 ${cls}`}>{value}</p>
      </CardContent>
    </Card>
  );
}
