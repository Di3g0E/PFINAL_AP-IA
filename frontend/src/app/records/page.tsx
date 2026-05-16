"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import {
  clearToken,
  getUserId,
  listTransactions,
  type TransactionRecord,
} from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Card, CardContent } from "@/components/ui/Card";
import { formatCurrency, formatDate } from "@/lib/utils";
import { RecordModal } from "@/components/RecordModal";

export default function RecordsPage() {
  const router = useRouter();
  const [items, setItems] = useState<TransactionRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  
  const [isModalOpen, setIsModalOpen] = useState(false);

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
    refresh();
  }, [router, refresh]);

  return (
    <div className="min-h-screen bg-gradient-to-br from-blue-50 via-white to-purple-50">
      <main className="max-w-4xl mx-auto px-4 py-6">
        <div className="flex items-start justify-between mb-6 flex-wrap gap-3">
          <div>
            <h1 className="text-2xl font-bold gradient-text">Registros</h1>
            <p className="text-sm text-slate-500 mt-1">
              Todos tus ingresos y gastos consolidados
            </p>
          </div>
          <div className="flex gap-2">
            <Button
              variant="secondary"
              size="sm"
              onClick={refresh}
              icon={
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                </svg>
              }
            >
              Refrescar
            </Button>
            <Button
              variant="primary"
              size="sm"
              onClick={() => setIsModalOpen(true)}
              icon={
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                </svg>
              }
            >
              Nuevo Registro
            </Button>
          </div>
        </div>

        {error && (
          <Card variant="default" className="border-red-200 bg-red-50 mb-6">
            <CardContent className="p-4 flex items-center">
              <svg className="w-5 h-5 text-red-600 mr-3" fill="currentColor" viewBox="0 0 20 20">
                <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clipRule="evenodd" />
              </svg>
              <div className="flex-1">
                <p className="text-sm font-medium text-red-800">Error</p>
                <p className="text-sm text-red-600">{error}</p>
              </div>
            </CardContent>
          </Card>
        )}

        {loading ? (
          <div className="flex justify-center py-12">
            <div className="flex items-center space-x-2">
              <div className="flex space-x-1">
                <div className="w-2 h-2 bg-blue-600 rounded-full animate-pulse"></div>
                <div className="w-2 h-2 bg-purple-600 rounded-full animate-pulse delay-100"></div>
                <div className="w-2 h-2 bg-blue-600 rounded-full animate-pulse delay-200"></div>
              </div>
              <span className="text-sm text-slate-600">Cargando registros...</span>
            </div>
          </div>
        ) : items.length === 0 ? (
          <Card variant="glass" className="text-center py-12">
            <div className="inline-flex items-center justify-center w-16 h-16 bg-gradient-to-br from-blue-100 to-indigo-100 rounded-full mb-4">
              <svg className="w-8 h-8 text-blue-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10" />
              </svg>
            </div>
            <h3 className="text-lg font-semibold text-slate-800 mb-2">No hay registros</h3>
            <p className="text-slate-600">Todavía no has añadido ninguna transacción.</p>
          </Card>
        ) : (
          <div className="space-y-4">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold text-slate-800">
                {items.length} {items.length === 1 ? 'registro' : 'registros'}
              </h2>
            </div>

            <div className="grid gap-3">
              {items.map((it) => (
                <Card key={it.id} variant="elevated" className="animate-fadeIn">
                  <CardContent className="p-4 sm:p-5 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
                    <div className="flex-1">
                      <div className="flex items-center gap-2 mb-1">
                        <span className={`px-2 py-0.5 rounded-full text-xs font-semibold ${
                          it.type === 'Income' 
                            ? 'bg-green-100 text-green-700' 
                            : 'bg-red-100 text-red-700'
                        }`}>
                          {it.type === 'Income' ? 'Ingreso' : 'Gasto'}
                        </span>
                        <span className="text-xs font-medium text-slate-500 flex items-center">
                          {formatDate(it.date)}
                        </span>
                        {it.status === 'pending' && (
                          <span className="bg-amber-100 text-amber-700 text-xs px-2 py-0.5 rounded-full font-medium">
                            En revisión
                          </span>
                        )}
                      </div>
                      <h3 className="text-base font-semibold text-slate-800 leading-tight">
                        {it.description}
                      </h3>
                      {it.area && it.area.length > 0 && (
                        <div className="flex flex-wrap gap-1 mt-2">
                          {it.area.map(a => (
                            <span key={a} className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-slate-100 text-slate-600">
                              {a}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                    <div className="text-left sm:text-right w-full sm:w-auto">
                      <div className="text-xl font-bold">
                        <span className={it.type === 'Income' ? 'text-green-600' : 'text-slate-800'}>
                          {it.type === 'Income' ? '+' : '-'}
                          {formatCurrency(Number(it.amount))}
                        </span>
                      </div>
                      <div className="text-xs text-slate-500 uppercase">{it.currency}</div>
                    </div>
                  </CardContent>
                </Card>
              ))}
            </div>
          </div>
        )}
      </main>

      {/* Modal para Crear Nuevo Registro */}
      <RecordModal
        open={isModalOpen}
        onClose={() => setIsModalOpen(false)}
        onSuccess={(record) => {
          setIsModalOpen(false);
          refresh(); // Refrescamos la lista para mostrar el nuevo registro
        }}
      />
    </div>
  );
}
