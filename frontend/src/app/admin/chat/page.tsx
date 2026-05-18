"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import {
  clearToken, createAdminChatSession, deleteAdminChatSession,
  getAdminChatMessages, getIsAdmin, listAdminChatSessions, sendAdminChat,
  type AdminChatMessage, type AdminChatSession,
} from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";


/**
 * Chat de operaciones del admin.
 *
 * El admin pregunta en lenguaje natural y el sub-agente Observability
 * decide qué tools llamar (events, monitor, logs, langfuse, agent_graph)
 * para responder. Cada turno graba eventos `agent="admin_orchestrator"`
 * + `agent="observability"` que el grafo `/admin/agent-graph?kind=ops`
 * pinta automáticamente.
 */
export default function AdminChatPage() {
  const router = useRouter();
  const [sessions, setSessions] = useState<AdminChatSession[]>([]);
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<AdminChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastToolsUsed, setLastToolsUsed] = useState<string[]>([]);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!getIsAdmin()) router.replace("/login");
  }, [router]);

  const refreshSessions = useCallback(async () => {
    try {
      const list = await listAdminChatSessions();
      setSessions(list);
      // Si hay sesiones y ninguna seleccionada, carga la más reciente.
      if (list.length > 0 && currentSessionId === null) {
        setCurrentSessionId(list[0].id);
      }
    } catch (err) {
      handleError(err);
    }
  }, [currentSessionId]);

  useEffect(() => {
    refreshSessions();
  }, [refreshSessions]);

  // Cargar mensajes al cambiar de sesión
  useEffect(() => {
    if (!currentSessionId) {
      setMessages([]);
      return;
    }
    getAdminChatMessages(currentSessionId)
      .then(setMessages)
      .catch(handleError);
    setLastToolsUsed([]);
  }, [currentSessionId]);

  // Auto-scroll al fondo cuando llegan mensajes
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  function handleError(err: unknown) {
    const msg = (err as Error).message;
    setError(msg);
    if (msg.toLowerCase().includes("token") || msg.includes("401")) {
      clearToken();
      router.replace("/login");
    } else if (msg.includes("403")) {
      router.replace("/login");
    }
  }

  async function onSend(e: React.FormEvent) {
    e.preventDefault();
    if (!input.trim() || sending) return;
    const text = input.trim();
    setSending(true);
    setError(null);

    // Optimistic: pintamos el mensaje del usuario inmediatamente.
    const optimistic: AdminChatMessage = {
      role: "user", content: text,
      created_at: new Date().toISOString(),
      sequence: messages.length + 1,
    };
    setMessages((prev) => [...prev, optimistic]);
    setInput("");

    try {
      const resp = await sendAdminChat(text, currentSessionId ?? undefined);
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant", content: resp.response,
          created_at: new Date().toISOString(),
          sequence: prev.length + 1,
        },
      ]);
      setLastToolsUsed(resp.tools_used);
      if (!currentSessionId) {
        setCurrentSessionId(resp.session_id);
      }
      refreshSessions();
    } catch (err) {
      handleError(err);
      // Revertir optimistic en caso de error
      setMessages((prev) => prev.filter((m) => m !== optimistic));
    } finally {
      setSending(false);
    }
  }

  async function onNewSession() {
    try {
      const sess = await createAdminChatSession();
      setCurrentSessionId(sess.session_id);
      setMessages([]);
      setLastToolsUsed([]);
      refreshSessions();
    } catch (err) {
      handleError(err);
    }
  }

  async function onDeleteSession(id: string) {
    if (!confirm("¿Borrar esta conversación?")) return;
    try {
      await deleteAdminChatSession(id);
      if (currentSessionId === id) {
        setCurrentSessionId(null);
        setMessages([]);
      }
      refreshSessions();
    } catch (err) {
      handleError(err);
    }
  }

  return (
    <div className="mx-auto max-w-7xl p-4">
      <Card>
        <CardHeader>
          <CardTitle>Chat de operaciones</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="mb-3 text-sm text-slate-600">
            Pregunta sobre el estado del sistema en lenguaje natural. El agente
            Observability decide qué consultar (events, logs, Langfuse, monitor)
            para responder.
          </p>

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-[260px_1fr]">
            {/* Sidebar — lista de sesiones */}
            <aside className="space-y-2">
              <Button onClick={onNewSession} className="w-full">
                + Nueva conversación
              </Button>
              <div className="max-h-[60vh] overflow-y-auto rounded border bg-white">
                {sessions.length === 0 && (
                  <div className="p-3 text-xs text-slate-500">
                    Aún no hay conversaciones.
                  </div>
                )}
                {sessions.map((s) => (
                  <div
                    key={s.id}
                    onClick={() => setCurrentSessionId(s.id)}
                    className={`group cursor-pointer border-b px-3 py-2 text-sm ${
                      currentSessionId === s.id
                        ? "bg-blue-50"
                        : "hover:bg-slate-50"
                    }`}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div className="flex-1 truncate font-medium">{s.title}</div>
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          onDeleteSession(s.id);
                        }}
                        className="text-xs text-slate-400 opacity-0 hover:text-red-600 group-hover:opacity-100"
                        title="Borrar"
                      >
                        ✕
                      </button>
                    </div>
                    <div className="text-xs text-slate-500">
                      {s.message_count} msgs · {new Date(s.last_message_at).toLocaleString()}
                    </div>
                  </div>
                ))}
              </div>
            </aside>

            {/* Panel principal — mensajes + input */}
            <section className="flex min-h-[60vh] flex-col">
              <div
                ref={scrollRef}
                className="flex-1 space-y-3 overflow-y-auto rounded border bg-slate-50 p-3"
              >
                {messages.length === 0 && !sending && (
                  <div className="text-sm text-slate-500">
                    Prueba a preguntar:
                    <ul className="ml-4 mt-2 list-disc text-xs">
                      <li>¿Cómo está el sistema en la última hora?</li>
                      <li>¿Hay anomalías en los logs de hoy?</li>
                      <li>¿Cuánto hemos gastado en LLM esta semana?</li>
                      <li>Muéstrame los errores recientes del registrar.</li>
                    </ul>
                  </div>
                )}
                {messages.map((m, i) => (
                  <MessageBubble key={i} message={m} />
                ))}
                {sending && (
                  <div className="text-sm italic text-slate-400">
                    Consultando telemetría…
                  </div>
                )}
              </div>

              {lastToolsUsed.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1 text-xs">
                  <span className="text-slate-500">Tools usadas:</span>
                  {lastToolsUsed.map((t, i) => (
                    <span
                      key={i}
                      className="rounded bg-violet-100 px-2 py-0.5 font-mono text-violet-800"
                    >
                      {t}
                    </span>
                  ))}
                </div>
              )}

              {error && (
                <div className="mt-2 rounded border border-red-300 bg-red-50 p-2 text-sm text-red-700">
                  {error}
                </div>
              )}

              <form onSubmit={onSend} className="mt-3 flex gap-2">
                <input
                  type="text"
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  placeholder="Pregunta al sistema…"
                  disabled={sending}
                  className="flex-1 rounded border border-slate-300 px-3 py-2 text-sm focus:border-blue-400 focus:outline-none"
                />
                <Button type="submit" disabled={sending || !input.trim()}>
                  {sending ? "Enviando…" : "Enviar"}
                </Button>
              </form>
            </section>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}


function MessageBubble({ message }: { message: AdminChatMessage }) {
  const isUser = message.role === "user";
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[85%] rounded-lg px-3 py-2 text-sm whitespace-pre-wrap ${
          isUser
            ? "bg-blue-500 text-white"
            : "border border-slate-200 bg-white text-slate-800"
        }`}
      >
        {message.content}
      </div>
    </div>
  );
}
