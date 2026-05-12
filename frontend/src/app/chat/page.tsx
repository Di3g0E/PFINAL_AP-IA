"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import {
  addManualTransaction,
  chat,
  clearToken,
  extractFromImage,
  getUserId,
  type ManualTransactionInput,
  type OCRExtracted,
} from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Input } from "@/components/ui/Input";
import { OCRConfirmModal } from "@/components/OCRConfirmModal";

type Turn = {
  role: "user" | "assistant";
  text: string;
  action?: string | null;
};

const SUGGESTIONS = [
  "resume mis gastos del último mes",
  "¿qué tendencia tienen mis gastos?",
  "¿qué gastos recurrentes detectas?",
  "¿qué tengo pendiente de revisar?",
];

export default function ChatPage() {
  const router = useRouter();
  const [userId, setUserId] = useState<string | null>(null);
  const [sessionId, setSessionId] = useState<string | undefined>();
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // OCR
  const [ocrLoading, setOcrLoading] = useState(false);
  const [ocrExtracted, setOcrExtracted] = useState<OCRExtracted | null>(null);
  const [ocrPreviewUrl, setOcrPreviewUrl] = useState<string | null>(null);
  const [ocrSubmitting, setOcrSubmitting] = useState(false);
  const [ocrModalError, setOcrModalError] = useState<string | null>(null);

  // Libera la URL del blob cuando cambia el preview
  useEffect(() => {
    return () => {
      if (ocrPreviewUrl) URL.revokeObjectURL(ocrPreviewUrl);
    };
  }, [ocrPreviewUrl]);

  // Protege la ruta: sin token → /login
  useEffect(() => {
    const uid = getUserId();
    if (!uid) {
      router.replace("/login");
    } else {
      setUserId(uid);
    }
  }, [router]);

  // Auto-scroll al fondo cuando llega un nuevo turno
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [turns]);

  const send = async (text: string) => {
    setError(null);
    setTurns((t) => [...t, { role: "user", text }]);
    setInput("");
    setLoading(true);
    try {
      const r = await chat(text, sessionId);
      setSessionId(r.session_id);
      setTurns((t) => [
        ...t,
        { role: "assistant", text: r.response, action: r.last_action },
      ]);
    } catch (err) {
      const msg = (err as Error).message;
      setError(msg);
      // Si el token expiró, redirigir a login
      if (msg.toLowerCase().includes("token") || msg.includes("401")) {
        clearToken();
        router.replace("/login");
      }
    } finally {
      setLoading(false);
    }
  };

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (input.trim() && !loading) send(input.trim());
  };

  const onLogout = () => {
    clearToken();
    router.replace("/login");
  };

  const onAttachClick = () => {
    if (loading || ocrLoading) return;
    fileInputRef.current?.click();
  };

  const onFileChosen = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = ""; // permite reseleccionar la misma imagen
    if (!file) return;

    setError(null);
    setOcrModalError(null);
    setOcrLoading(true);

    // Preview local mientras se procesa
    const url = URL.createObjectURL(file);
    if (ocrPreviewUrl) URL.revokeObjectURL(ocrPreviewUrl);
    setOcrPreviewUrl(url);

    setTurns((t) => [...t, { role: "user", text: `📎 ${file.name}` }]);

    try {
      const extracted = await extractFromImage(file);
      setOcrExtracted(extracted);
    } catch (err) {
      const msg = (err as Error).message;
      setError(`OCR: ${msg}`);
      URL.revokeObjectURL(url);
      setOcrPreviewUrl(null);
      if (msg.toLowerCase().includes("token") || msg.includes("401")) {
        clearToken();
        router.replace("/login");
      }
    } finally {
      setOcrLoading(false);
    }
  };

  const onOcrConfirm = async (input: ManualTransactionInput) => {
    setOcrSubmitting(true);
    setOcrModalError(null);
    try {
      const res = await addManualTransaction(input);
      const accepted = res.accepted[0];
      const pending = res.pending_review[0];
      const rejected = res.rejected[0];

      let assistantText: string;
      if (accepted) {
        const areaTxt = accepted.area.length ? ` · ${accepted.area.join(", ")}` : "";
        assistantText = `Gasto registrado: ${accepted.amount} ${accepted.currency}${areaTxt} (id ${accepted.id.slice(0, 8)}…).`;
      } else if (pending) {
        assistantText =
          `Transacción guardada como pendiente de revisión. Razones: ` +
          `${pending.anomaly_reasons.join("; ") || "sin detalle"}.`;
      } else if (rejected) {
        assistantText = `No se pudo registrar: ${rejected.reason}`;
      } else {
        assistantText = "Operación completada sin resultado.";
      }
      setTurns((t) => [...t, { role: "assistant", text: assistantText, action: "registrar" }]);

      // Cierra el modal solo si el backend aceptó el alta (incluye pending_review).
      setOcrExtracted(null);
      if (ocrPreviewUrl) URL.revokeObjectURL(ocrPreviewUrl);
      setOcrPreviewUrl(null);
    } catch (err) {
      setOcrModalError((err as Error).message);
    } finally {
      setOcrSubmitting(false);
    }
  };

  const onOcrCancel = () => {
    if (ocrSubmitting) return;
    setOcrExtracted(null);
    if (ocrPreviewUrl) URL.revokeObjectURL(ocrPreviewUrl);
    setOcrPreviewUrl(null);
    setOcrModalError(null);
  };

  const copyToClipboard = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      console.error("Error al copiar:", err);
    }
  };

  if (!userId) {
    return <p className="text-sm text-slate-500">Cargando…</p>;
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-blue-50 via-white to-purple-50">
      {/* Header */}
      <header className="bg-white/80 backdrop-blur-sm border-b border-slate-200/50 sticky top-0 z-10">
        <div className="max-w-4xl mx-auto px-4 py-4 flex items-center justify-between">
          <div className="flex items-center space-x-4">
            <div className="w-10 h-10 bg-gradient-to-br from-blue-600 to-purple-600 rounded-xl flex items-center justify-center">
              <svg className="w-5 h-5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
              </svg>
            </div>
            <div className="flex-1">
              <h1 className="text-xl font-bold gradient-text">Asistente Financiero</h1>
              <div className="flex items-center space-x-2 mt-1">
                <span className="text-xs text-slate-500">ID Usuario:</span>
                <div className="flex items-center space-x-1 bg-slate-100 px-2 py-1 rounded-md border border-slate-200">
                  <code className="text-xs font-mono text-slate-700">{userId}</code>
                  <button
                    onClick={() => copyToClipboard(userId)}
                    className="p-1 hover:bg-slate-200 rounded transition-colors group"
                    title="Copiar UUID"
                  >
                    {copied ? (
                      <svg className="w-3 h-3 text-green-600" fill="currentColor" viewBox="0 0 20 20">
                        <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
                      </svg>
                    ) : (
                      <svg className="w-3 h-3 text-slate-500 group-hover:text-slate-700" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
                      </svg>
                    )}
                  </button>
                </div>
                {sessionId && (
                  <span className="text-xs text-slate-500">
                    · Sesión: <code className="font-mono bg-slate-100 px-1 rounded">{sessionId.slice(0, 8)}…</code>
                  </span>
                )}
              </div>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => router.push("/settings")}
              icon={
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                </svg>
              }
            >
              Configuración
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={onLogout}
              icon={
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1" />
                </svg>
              }
            >
              Cerrar sesión
            </Button>
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main className="max-w-4xl mx-auto px-4 py-6">
        <Card variant="glass" className="h-[600px] flex flex-col">
          <CardHeader className="pb-4">
            <CardTitle className="text-lg">Conversación</CardTitle>
          </CardHeader>
          
          <CardContent className="flex-1 flex flex-col">
            <div
              ref={scrollRef}
              className="flex-1 overflow-y-auto space-y-4 pr-2"
            >
              {turns.length === 0 ? (
                <div className="text-center py-8">
                  <div className="inline-flex items-center justify-center w-16 h-16 bg-gradient-to-br from-blue-100 to-purple-100 rounded-full mb-4">
                    <svg className="w-8 h-8 text-blue-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z" />
                    </svg>
                  </div>
                  <h3 className="text-lg font-semibold text-slate-800 mb-2">¡Hola! 👋</h3>
                  <p className="text-slate-600 mb-6">Soy tu asistente financiero personal. ¿En qué puedo ayudarte hoy?</p>
                  
                  <div className="space-y-3">
                    <p className="text-sm font-medium text-slate-700">Prueba estas preguntas rápidas:</p>
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                      {SUGGESTIONS.map((s) => (
                        <Button
                          key={s}
                          variant="secondary"
                          size="sm"
                          onClick={() => send(s)}
                          className="text-left justify-start h-auto py-3 px-4 text-xs"
                        >
                          {s}
                        </Button>
                      ))}
                    </div>
                  </div>
                </div>
              ) : (
                turns.map((t, i) => (
                  <div
                    key={i}
                    className={`flex ${t.role === "user" ? "justify-end" : "justify-start"} animate-fadeIn`}
                  >
                    <div
                      className={`max-w-[80%] rounded-2xl px-4 py-3 ${
                        t.role === "user"
                          ? "bg-gradient-to-r from-blue-600 to-purple-600 text-white shadow-lg"
                          : "bg-white/80 backdrop-blur-sm border border-slate-200/50 shadow-md"
                      }`}
                    >
                      <div className="flex items-start space-x-2">
                        {t.role === "assistant" && (
                          <div className="w-6 h-6 bg-gradient-to-br from-blue-500 to-purple-500 rounded-full flex items-center justify-center flex-shrink-0 mt-1">
                            <svg className="w-3 h-3 text-white" fill="currentColor" viewBox="0 0 20 20">
                              <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clipRule="evenodd" />
                            </svg>
                          </div>
                        )}
                        <div className="flex-1">
                          <p className={`text-sm whitespace-pre-wrap ${t.role === "user" ? "text-white" : "text-slate-800"}`}>
                            {t.text}
                          </p>
                          {t.action && (
                            <p className={`mt-2 text-xs uppercase tracking-wider ${
                              t.role === "user" ? "text-blue-100" : "text-slate-500"
                            }`}>
                              {t.action}
                            </p>
                          )}
                        </div>
                      </div>
                    </div>
                  </div>
                ))
              )}
              
              {loading && (
                <div className="flex justify-center">
                  <div className="bg-white/80 backdrop-blur-sm border border-slate-200/50 rounded-full px-4 py-2 shadow-md">
                    <div className="flex items-center space-x-2">
                      <div className="flex space-x-1">
                        <div className="w-2 h-2 bg-blue-600 rounded-full animate-pulse"></div>
                        <div className="w-2 h-2 bg-purple-600 rounded-full animate-pulse delay-100"></div>
                        <div className="w-2 h-2 bg-blue-600 rounded-full animate-pulse delay-200"></div>
                      </div>
                      <span className="text-sm text-slate-600">El orquestador está pensando…</span>
                    </div>
                  </div>
                </div>
              )}
            </div>

            {/* Input Form */}
            <div className="pt-4 border-t border-slate-200/50">
              <form onSubmit={onSubmit} className="flex gap-2">
                <input
                  ref={fileInputRef}
                  type="file"
                  accept="image/*"
                  capture="environment"
                  className="hidden"
                  onChange={onFileChosen}
                />
                <button
                  type="button"
                  onClick={onAttachClick}
                  disabled={loading || ocrLoading}
                  title="Adjuntar factura para OCR"
                  className="px-3 rounded-lg border border-slate-300/50 bg-white/80 backdrop-blur-sm hover:bg-slate-50 text-slate-600 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                >
                  {ocrLoading ? (
                    <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
                      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" className="opacity-25" />
                      <path fill="currentColor" className="opacity-75" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z" />
                    </svg>
                  ) : (
                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15.172 7l-6.586 6.586a2 2 0 102.828 2.828l6.414-6.586a4 4 0 00-5.656-5.656l-6.415 6.585a6 6 0 108.486 8.486L20.5 13" />
                    </svg>
                  )}
                </button>
                <div className="flex-1 relative">
                  <input
                    type="text"
                    placeholder="Escribe tu mensaje…"
                    value={input}
                    onChange={(e) => setInput(e.target.value)}
                    disabled={loading}
                    className="w-full rounded-lg border border-slate-300/50 bg-white/80 backdrop-blur-sm px-12 py-3 text-sm text-slate-800 transition-all duration-200 focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-500/20 disabled:opacity-50 disabled:cursor-not-allowed"
                  />
                  <div className="absolute left-3 top-1/2 transform -translate-y-1/2 text-slate-400">
                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
                    </svg>
                  </div>
                </div>
                <Button
                  type="submit"
                  variant="primary"
                  size="md"
                  disabled={loading || !input.trim()}
                  icon={
                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" />
                    </svg>
                  }
                >
                  Enviar
                </Button>
              </form>
            </div>
          </CardContent>
        </Card>
      </main>

      <OCRConfirmModal
        open={ocrExtracted !== null}
        initial={ocrExtracted}
        previewUrl={ocrPreviewUrl}
        submitting={ocrSubmitting}
        error={ocrModalError}
        onCancel={onOcrCancel}
        onConfirm={onOcrConfirm}
      />

      {error && (
        <div className="fixed bottom-4 right-4 max-w-md">
          <Card variant="default" className="border-red-200 bg-red-50">
            <CardContent className="p-4 flex items-center">
              <svg className="w-5 h-5 text-red-600 mr-3" fill="currentColor" viewBox="0 0 20 20">
                <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clipRule="evenodd" />
              </svg>
              <div className="flex-1">
                <p className="text-sm font-medium text-red-800">Error</p>
                <p className="text-sm text-red-600">{error}</p>
              </div>
              <Button variant="ghost" size="sm" onClick={() => setError(null)}>
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </Button>
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}
