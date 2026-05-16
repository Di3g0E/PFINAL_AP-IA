"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import {
  clearToken,
  confirmPending,
  getIsAdmin,
  getUserId,
  listPending,
  rejectPending,
  type PendingReview,
} from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { formatCurrency, formatDate } from "@/lib/utils";

export default function PendingPage() {
  const router = useRouter();
  const [items, setItems] = useState<PendingReview[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setError(null);
    setLoading(true);
    try {
      const list = await listPending();
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

  // Protege la ruta + carga inicial
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

  const onConfirm = async (id: string) => {
    setBusyId(id);
    try {
      await confirmPending(id);
      await refresh();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusyId(null);
    }
  };

  const onReject = async (id: string) => {
    if (!confirm("¿Rechazar esta transacción? No se contabilizará en analytics.")) {
      return;
    }
    setBusyId(id);
    try {
      await rejectPending(id);
      await refresh();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-blue-50 via-white to-purple-50">
      {/* Main Content */}
      <main className="max-w-4xl mx-auto px-4 py-6">
        <div className="flex items-start justify-between mb-6 flex-wrap gap-3">
          <div>
            <h1 className="text-2xl font-bold gradient-text">Transacciones Pendientes</h1>
            <p className="text-sm text-slate-500 mt-1">
              Revisa las transacciones marcadas como posibles anomalías
            </p>
          </div>
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
              <span className="text-sm text-slate-600">Cargando transacciones...</span>
            </div>
          </div>
        ) : items.length === 0 ? (
          <Card variant="glass" className="text-center py-12">
            <div className="inline-flex items-center justify-center w-16 h-16 bg-gradient-to-br from-green-100 to-emerald-100 rounded-full mb-4">
              <svg className="w-8 h-8 text-green-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            </div>
            <h3 className="text-lg font-semibold text-slate-800 mb-2">¡Todo está en orden! 🎉</h3>
            <p className="text-slate-600">No tienes transacciones pendientes de revisión</p>
          </Card>
        ) : (
          <div className="space-y-4">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold text-slate-800">
                {items.length} {items.length === 1 ? 'transacción' : 'transacciones'} pendientes
              </h2>
              <div className="text-sm text-slate-500">
                Revisa y aprueba las que quieras incluir en tus análisis
              </div>
            </div>

            <div className="grid gap-4">
              {items.map((it) => (
                <Card key={it.record.id} variant="elevated" className="animate-fadeIn">
                  <CardContent className="p-6">
                    <div className="flex items-start justify-between mb-4">
                      <div className="flex-1">
                        <h3 className="text-lg font-semibold text-slate-800 mb-2">
                          {it.record.description}
                        </h3>
                        <div className="flex items-center space-x-4 text-sm text-slate-500">
                          <span className="flex items-center">
                            <svg className="w-4 h-4 mr-1" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
                            </svg>
                            {formatDate(it.record.date)}
                          </span>
                          <span className="flex items-center">
                            <svg className="w-4 h-4 mr-1" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z" />
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 11a3 3 0 11-6 0 3 3 0 016 0z" />
                            </svg>
                            {it.record.area.join(", ")}
                          </span>
                          <span className={`px-2 py-1 rounded-full text-xs font-medium ${
                            it.record.type === 'Income' 
                              ? 'bg-green-100 text-green-800' 
                              : 'bg-red-100 text-red-800'
                          }`}>
                            {it.record.type === 'Income' ? 'Ingreso' : 'Gasto'}
                          </span>
                        </div>
                      </div>
                      <div className="text-right">
                        <div className="text-2xl font-bold">
                          <span className={it.record.type === 'Income' ? 'text-green-600' : 'text-red-600'}>
                            {it.record.type === 'Income' ? '+' : '-'}
                            {formatCurrency(Number(it.record.amount))}
                          </span>
                        </div>
                        <div className="text-sm text-slate-500">{it.record.currency}</div>
                      </div>
                    </div>

                    {it.anomaly_reasons.length > 0 && (
                      <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 mb-4">
                        <div className="flex items-start space-x-2">
                          <svg className="w-4 h-4 text-amber-600 mt-0.5" fill="currentColor" viewBox="0 0 20 20">
                            <path fillRule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clipRule="evenodd" />
                          </svg>
                          <div className="flex-1">
                            <p className="text-sm font-medium text-amber-800 mb-1">Posibles anomalías detectadas:</p>
                            <ul className="text-xs text-amber-700 space-y-0.5">
                              {it.anomaly_reasons.map((r, i) => (
                                <li key={i} className="flex items-start">
                                  <span className="mr-1">•</span>
                                  <span>{r}</span>
                                </li>
                              ))}
                            </ul>
                          </div>
                        </div>
                      </div>
                    )}

                    <div className="flex gap-3">
                      <Button
                        variant="primary"
                        size="sm"
                        onClick={() => onConfirm(it.record.id)}
                        disabled={busyId === it.record.id}
                        loading={busyId === it.record.id}
                        icon={
                          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                          </svg>
                        }
                      >
                        Aprobar
                      </Button>
                      <Button
                        variant="danger"
                        size="sm"
                        onClick={() => onReject(it.record.id)}
                        disabled={busyId === it.record.id}
                        icon={
                          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                          </svg>
                        }
                      >
                        Rechazar
                      </Button>
                    </div>
                  </CardContent>
                </Card>
              ))}
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
