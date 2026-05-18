/**
 * Cliente HTTP del backend FastAPI.
 *
 * El JWT se persiste en `localStorage` y se inyecta en `Authorization` para
 * los endpoints protegidos. Si la respuesta es 401, limpia el token y deja
 * que el llamador redirija a /login.
 */

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

// Tipos compartidos con el backend (forma simplificada)

export type TokenResponse = {
  access_token: string;
  token_type: string;
  user_id: string;
  similarity?: number | null;
  liveness_score?: number | null;
};

export type ChartSpec = {
  type: "line" | "bar" | "pie" | "area";
  title: string;
  data: { label: string; value: number }[];
  explanation: string;
};

export type ChatResponse = {
  response: string;
  session_id: string;
  last_action?: string | null;
  chart?: ChartSpec | null;
};

export type TransactionRecord = {
  id: string;
  description: string;
  date: string;
  amount: string; // Decimal como string en JSON
  currency: string;
  area: string[];
  type: "Income" | "Expenses";
  source: string;
  status: "accepted" | "pending" | "rejected";
};

export type PendingReview = {
  record: TransactionRecord;
  anomaly_reasons: string[];
};

export type OCRExtracted = {
  amount: string;                     // Decimal serializado como string
  description_suggested: string;
  date_suggested: string;             // ISO YYYY-MM-DD
  area_suggested: string[];
  type_suggested: "Income" | "Expenses";
  currency: string;
};

export type ManualTransactionInput = {
  description: string;
  date: string;                       // ISO YYYY-MM-DD
  amount: string;                     // mantenemos string para no perder precisión
  type: "Income" | "Expenses";
  area?: string[];
};

export type ManualTransactionResponse = {
  accepted: TransactionRecord[];
  pending_review: PendingReview[];
  rejected: { reason: string }[];
};

// Helpers privados

function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem("token");
}

export function setToken(token: string, userId: string) {
  localStorage.setItem("token", token);
  localStorage.setItem("user_id", userId);
  // El NavBar y otros componentes escuchan este evento para refrescar el
  // estado de autenticación sin tener que recargar la página.
  if (typeof window !== "undefined") {
    window.dispatchEvent(new Event("auth-change"));
  }
}

export function clearToken() {
  localStorage.removeItem("token");
  localStorage.removeItem("user_id");
  localStorage.removeItem("is_admin");
  // Limpia también la sesión de chat activa: evita que al loguearse otro
  // usuario distinto en el mismo navegador se intente rehidratar una
  // sesión que no le pertenece (devolvería 404 y se limpiaría sola,
  // pero es más limpio borrarla aquí).
  localStorage.removeItem("current_session_id");
  if (typeof window !== "undefined") {
    window.dispatchEvent(new Event("auth-change"));
  }
}

export function getUserId(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem("user_id");
}

/**
 * Lee el flag `is_admin` que persistimos tras llamar a `me()`. Sirve para
 * que NavBar y guardas de página decidan al instante (sin esperar a un
 * fetch) qué pestañas mostrar. La fuente de verdad sigue siendo el backend.
 */
export function getIsAdmin(): boolean {
  if (typeof window === "undefined") return false;
  return localStorage.getItem("is_admin") === "true";
}

export function setIsAdmin(flag: boolean) {
  if (typeof window === "undefined") return;
  localStorage.setItem("is_admin", flag ? "true" : "false");
  window.dispatchEvent(new Event("auth-change"));
}

async function parseError(res: Response): Promise<string> {
  try {
    const body = await res.json();
    return body.detail ?? body.message ?? `Error ${res.status}`;
  } catch {
    return `Error ${res.status} ${res.statusText}`;
  }
}

/**
 * Envuelve `fetch` para convertir errores de red (`TypeError: Failed to fetch`)
 * en mensajes accionables. Causas frecuentes:
 *   - El túnel ngrok no está levantado o cambió de subdominio.
 *   - `NEXT_PUBLIC_API_BASE_URL` apunta a una URL que ya no responde.
 *   - Mixed content (HTTPS frontend → HTTP backend).
 *   - CORS bloqueó la respuesta.
 *
 * Si fuera un error HTTP normal (4xx/5xx) el fetch resuelve y dejamos que el
 * llamador lo trate con `parseError`. Aquí solo capturamos el caso de "no se
 * pudo abrir la conexión".
 */
async function safeFetch(input: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(input, init);
  } catch (err) {
    const e = err as Error;
    // `TypeError` es lo que lanzan todos los navegadores cuando la petición
    // ni siquiera llega a obtener respuesta del servidor.
    if (e.name === "TypeError") {
      throw new Error(
        `No se pudo conectar con el backend (${API_BASE}). ` +
        `Comprueba que el túnel ngrok está activo y que ` +
        `NEXT_PUBLIC_API_BASE_URL apunta a la URL actual. ` +
        `Detalle técnico: ${e.message}`,
      );
    }
    throw e;
  }
}

async function authedFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const token = getToken();
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (!headers.has("Content-Type") && init.body && typeof init.body === "string") {
    headers.set("Content-Type", "application/json");
  }
  // Evita la pagina de advertencia de ngrok-free en peticiones cross-origin.
  headers.set("ngrok-skip-browser-warning", "true");
  const res = await safeFetch(`${API_BASE}${path}`, { ...init, headers });
  if (res.status === 401) {
    // Sesión expirada o token inválido: limpia y manda al login. Es
    // importante redirigir AQUÍ y no solo en cada página, porque varios
    // puntos (OCR, refresh de pendientes, etc.) no manejaban el 401 y
    // dejaban al usuario en una pantalla sin token, con los siguientes
    // clicks devolviendo "Bearer token requerido".
    clearToken();
    if (typeof window !== "undefined" && !window.location.pathname.startsWith("/login")) {
      window.location.replace("/login");
    }
  }
  return res;
}

// Auth

export async function register(
  email: string,
  passphrase: string,
  consent: boolean,
  face?: Blob,  // Opcional - desactivado temporalmente
  notificationsEnabled?: boolean,
  telegramChatId?: string,
): Promise<TokenResponse> {
  const fd = new FormData();
  fd.append("email", email);
  fd.append("passphrase", passphrase);
  fd.append("biometric_consent", String(consent));
  
  // Notificaciones opcionales
  if (notificationsEnabled !== undefined) {
    fd.append("notifications_enabled", String(notificationsEnabled));
  }
  if (telegramChatId) {
    fd.append("telegram_chat_id", telegramChatId);
  }
  
  // BIOMETRÍA DESACTIVADA TEMPORALMENTE
  if (face) {
    fd.append("face", face, "face.jpg");
  }
  // Si no hay face, no lo añadimos - el backend lo manejará como opcional
  
  const res = await safeFetch(`${API_BASE}/auth/register`, {
    method: "POST",
    body: fd,
    headers: { "ngrok-skip-browser-warning": "true" },
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function login(
  email: string,
  passphrase: string,
  face?: Blob,  // JPEG (single-frame) o WebM (vídeo E3)
): Promise<TokenResponse> {
  const fd = new FormData();
  fd.append("email", email);
  fd.append("passphrase", passphrase);
  
  if (face) {
    // Detectar si es vídeo (WebM) o imagen (JPEG/PNG)
    const isVideo = face.type.startsWith("video/");
    if (isVideo) {
      // E3 Fase 1: enviar como face_video (el backend lo procesa con
      // extract_from_video → voto promedio de N frames)
      fd.append("face_video", face, "face.webm");
      // Fallback face obligatorio: el endpoint sigue requiriendo 'face'.
      // Creamos un placeholder JPEG de 1x1 px transparente.
      const placeholderJpeg = _createPlaceholderJpeg();
      fd.append("face", placeholderJpeg, "face.jpg");
    } else {
      fd.append("face", face, "face.jpg");
    }
  }
  
  const res = await safeFetch(`${API_BASE}/auth/login`, {
    method: "POST",
    body: fd,
    headers: { "ngrok-skip-browser-warning": "true" },
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

/**
 * Login para cuentas operacionales (`is_admin=True`): solo email + passphrase,
 * sin cámara. El backend rechaza con el mismo mensaje genérico si el usuario
 * no es admin, así que sirve también como "intento legítimo" sin filtrar info.
 */
export async function loginAdmin(
  email: string,
  passphrase: string,
): Promise<TokenResponse> {
  const res = await safeFetch(`${API_BASE}/auth/login-admin`, {
    method: "POST",
    body: JSON.stringify({ email, passphrase }),
    headers: {
      "Content-Type": "application/json",
      "ngrok-skip-browser-warning": "true",
    },
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}


export type MeInfo = {
  user_id: string;
  email: string;
  role: string;
  is_admin: boolean;
};

/**
 * Identidad + privilegios del usuario autenticado. Llamarla justo después
 * del login para refrescar `localStorage.is_admin` y poder pintar el
 * NavBar correcto antes de la primera petición de datos.
 */
export async function me(): Promise<MeInfo> {
  const res = await authedFetch("/auth/me");
  if (!res.ok) throw new Error(await parseError(res));
  const body = (await res.json()) as MeInfo;
  if (typeof window !== "undefined") {
    localStorage.setItem("is_admin", body.is_admin ? "true" : "false");
    window.dispatchEvent(new Event("auth-change"));
  }
  return body;
}


export type MonitorEvent = {
  ts: string;                  // ISO datetime
  agent: string;
  action: string;
  status: string;              // 'ok' | 'error' | 'warning' | 'denied'
  latency_ms: number | null;
  user_id: string | null;
  session_id: string | null;
  payload: Record<string, unknown> | null;
};

/**
 * Lee los últimos eventos crudos de la tabla `events` (analizador de logs
 * del panel admin). Acepta filtros opcionales por status / agent.
 */
export async function getMonitorEvents(opts: {
  limit?: number;
  status?: string;
  agent?: string;
} = {}): Promise<MonitorEvent[]> {
  const qs = new URLSearchParams();
  if (opts.limit) qs.set("limit", String(opts.limit));
  if (opts.status) qs.set("status", opts.status);
  if (opts.agent) qs.set("agent", opts.agent);
  const suffix = qs.toString() ? `?${qs.toString()}` : "";
  const res = await authedFetch(`/monitor/events${suffix}`);
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

/**
 * Genera un Blob JPEG mínimo (1x1 px) para satisfacer el campo 'face'
 * obligatorio del endpoint cuando se envía vídeo como entrada principal.
 */
function _createPlaceholderJpeg(): Blob {
  // JPEG mínimo válido: 1x1 pixel negro (267 bytes)
  const canvas = document.createElement("canvas");
  canvas.width = 1;
  canvas.height = 1;
  const ctx = canvas.getContext("2d")!;
  ctx.fillStyle = "#000";
  ctx.fillRect(0, 0, 1, 1);
  // toBlob es async, usamos toDataURL como workaround síncrono
  const dataUrl = canvas.toDataURL("image/jpeg", 0.1);
  const byteString = atob(dataUrl.split(",")[1]);
  const ab = new ArrayBuffer(byteString.length);
  const ia = new Uint8Array(ab);
  for (let i = 0; i < byteString.length; i++) {
    ia[i] = byteString.charCodeAt(i);
  }
  return new Blob([ab], { type: "image/jpeg" });
}

// Chat

export async function chat(
  message: string,
  sessionId?: string,
): Promise<ChatResponse> {
  const res = await authedFetch("/chat", {
    method: "POST",
    body: JSON.stringify({ message, session_id: sessionId }),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

// Sesiones persistentes (Fase 1)

export type ChatSessionOut = {
  id: string;
  title: string;
  summary: string | null;
  archived: boolean;
  created_at: string;
  last_message_at: string;
};

export type ChatMessageOut = {
  role: "user" | "assistant" | "system";
  content: string;
  action: string | null;
  sequence: number;
  created_at: string;
  chart: ChartSpec | null;
};

export type ChatSessionDetail = {
  session: ChatSessionOut;
  messages: ChatMessageOut[];
};

export async function listChatSessions(includeArchived = false): Promise<ChatSessionOut[]> {
  const qs = includeArchived ? "?include_archived=true" : "";
  const res = await authedFetch(`/chat/sessions${qs}`);
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function createChatSession(title?: string): Promise<ChatSessionOut> {
  const res = await authedFetch("/chat/sessions", {
    method: "POST",
    body: JSON.stringify({ title: title ?? null }),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function getChatSession(sessionId: string): Promise<ChatSessionDetail> {
  const res = await authedFetch(`/chat/sessions/${sessionId}`);
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function renameChatSession(sessionId: string, title: string): Promise<ChatSessionOut> {
  const res = await authedFetch(`/chat/sessions/${sessionId}`, {
    method: "PATCH",
    body: JSON.stringify({ title }),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function deleteChatSession(sessionId: string): Promise<void> {
  const res = await authedFetch(`/chat/sessions/${sessionId}`, {
    method: "DELETE",
  });
  if (!res.ok && res.status !== 204) throw new Error(await parseError(res));
}

// Monitor (Punto 4: monitorización automática del sistema)

export type LatencyStats = {
  agent: string;
  action: string;
  count: number;
  p50_ms: number | null;
  p95_ms: number | null;
  max_ms: number | null;
};

export type TopError = {
  agent: string;
  action: string;
  error_count: number;
};

export type MonitoringReport = {
  generated_at: string;
  window_minutes: number;
  total_events: number;
  ok_count: number;
  error_count: number;
  warning_count: number;
  denied_count: number;
  error_rate: number;
  health: "ok" | "warning" | "critical";
  latencies: LatencyStats[];
  top_errors: TopError[];
  active_sessions: number;
  active_users: number;
  db_available: boolean;
  note: string | null;
};

export async function getMonitorReport(windowMinutes = 60): Promise<MonitoringReport> {
  const res = await authedFetch(`/monitor/health-detailed?window_minutes=${windowMinutes}`);
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}


// Rol del usuario (Fase 3: perfilado básico/avanzado)

export type UserRole = "basic" | "advanced";

export async function getUserRole(): Promise<UserRole> {
  const res = await authedFetch("/api/user/role");
  if (!res.ok) throw new Error(await parseError(res));
  const body = await res.json();
  return body.role as UserRole;
}

export async function updateUserRole(role: UserRole): Promise<UserRole> {
  const res = await authedFetch("/api/user/role", {
    method: "PUT",
    body: JSON.stringify({ role }),
  });
  if (!res.ok) throw new Error(await parseError(res));
  const body = await res.json();
  return body.role as UserRole;
}


// Grafo agéntico (vistas usuario y admin)

export type AgentGraphNode = {
  id: string;
  agent: string;
  // true cuando el agente pertenece a la familia "ops" (admin_orchestrator,
  // observability). El frontend lo usa para colorear distinto y permitir
  // al admin distinguir su propio flujo de debug del flujo de la app.
  is_ops?: boolean;
  role: "basic" | "advanced" | null;
  count: number;
  error_count: number;
  error_rate: number;
  avg_latency_ms: number | null;
  users_distinct?: number;
};

export type AgentGraphEdge = {
  source: string;
  target: string;
  count: number;
  avg_latency_ms: number | null;
};

export type AgentGraph = {
  nodes: AgentGraphNode[];
  edges: AgentGraphEdge[];
  meta: {
    total_events: number;
    total_sessions: number;
    fallback_role?: string | null;
    kind?: "app" | "ops" | "all";
  };
};

export type UserAgentGraphResponse = {
  generated_at: string;
  window_hours: number;
  role: UserRole | null;
  graph: AgentGraph;
};

export type LangfuseSummary = {
  enabled: boolean;
  ok: boolean;
  traces_count: number;
  observations_count: number;
  total_cost_usd: number;
  total_tokens_in: number;
  total_tokens_out: number;
  models_used: { model: string; tokens_in: number; tokens_out: number; cost_usd: number }[];
  top_trace_names: { name: string; count: number }[];
  note: string | null;
};

export type LogAnomaly = {
  kind: "volume" | "spike" | "repeated" | "critical";
  severity: "info" | "warning" | "critical";
  description: string;
  sample_message: string | null;
  occurrences: number;
};

export type LogAnalysisReport = {
  window_hours: number;
  lines_scanned: number;
  lines_parsed: number;
  counts_by_level: Record<string, number>;
  top_modules_with_errors: { module: string; count: number }[];
  top_repeated_messages: { message: string; count: number }[];
  anomalies: LogAnomaly[];
  note: string | null;
};

export type AdminAgentGraphResponse = {
  generated_at: string;
  window_hours: number;
  graph_app: AgentGraph;  // agentes de aplicación (orchestrator/analyst/...)
  graph_ops: AgentGraph;  // agentes del admin chat (admin_orchestrator/observability)
  meta: {
    langfuse: LangfuseSummary | null;
    monitor: MonitoringReport | null;
    logs: LogAnalysisReport | null;
  };
};

export async function getMyAgentGraph(windowHours = 24): Promise<UserAgentGraphResponse> {
  const res = await authedFetch(`/me/agent-graph?window_hours=${windowHours}`);
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function getMyAgentGraphDot(windowHours = 24): Promise<string> {
  const res = await authedFetch(`/me/agent-graph?window_hours=${windowHours}&format=dot`);
  if (!res.ok) throw new Error(await parseError(res));
  return res.text();
}

export async function getAdminAgentGraph(windowHours = 24): Promise<AdminAgentGraphResponse> {
  const res = await authedFetch(`/admin/agent-graph?window_hours=${windowHours}`);
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function getAdminAgentGraphDot(windowHours = 24): Promise<string> {
  const res = await authedFetch(`/admin/agent-graph?window_hours=${windowHours}&format=dot`);
  if (!res.ok) throw new Error(await parseError(res));
  return res.text();
}


// Admin chat (chat de ops con tools de observabilidad)

export type AdminChatSession = {
  id: string;
  title: string;
  created_at: string;
  last_message_at: string;
  message_count: number;
};

export type AdminChatMessage = {
  role: "user" | "assistant" | "system";
  content: string;
  created_at: string;
  sequence: number;
};

export type AdminChatResponse = {
  response: string;
  session_id: string;
  tools_used: string[];
};

export async function createAdminChatSession(): Promise<{ session_id: string; title: string; created_at: string }> {
  const res = await authedFetch("/admin/chat/sessions", { method: "POST" });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function listAdminChatSessions(): Promise<AdminChatSession[]> {
  const res = await authedFetch("/admin/chat/sessions");
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function getAdminChatMessages(sessionId: string): Promise<AdminChatMessage[]> {
  const res = await authedFetch(`/admin/chat/sessions/${sessionId}/messages`);
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function sendAdminChat(message: string, sessionId?: string): Promise<AdminChatResponse> {
  const res = await authedFetch("/admin/chat", {
    method: "POST",
    body: JSON.stringify({ message, session_id: sessionId }),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function deleteAdminChatSession(sessionId: string): Promise<void> {
  const res = await authedFetch(`/admin/chat/sessions/${sessionId}`, { method: "DELETE" });
  if (!res.ok && res.status !== 204) throw new Error(await parseError(res));
}


// Helpers de session_id en localStorage (clave: 'current_session_id').
// Sirven para que al cambiar de pestaña Chat ↔ Pendientes se recupere
// automáticamente la conversación abierta.

export function getCurrentSessionId(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem("current_session_id");
}

export function setCurrentSessionId(sessionId: string | null) {
  if (typeof window === "undefined") return;
  if (sessionId === null) {
    localStorage.removeItem("current_session_id");
  } else {
    localStorage.setItem("current_session_id", sessionId);
  }
}

// Transacciones

export async function extractFromImage(
  file: File,
  hint?: string,
  dateHint?: string,
): Promise<OCRExtracted> {
  const fd = new FormData();
  fd.append("image", file);
  if (hint) fd.append("description_hint", hint);
  if (dateHint) fd.append("date_hint", dateHint);
  // No fijamos Content-Type: el navegador añade el boundary automáticamente.
  const res = await authedFetch("/transactions/ocr-extract", {
    method: "POST",
    body: fd,
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function addManualTransaction(
  input: ManualTransactionInput,
): Promise<ManualTransactionResponse> {
  const res = await authedFetch("/transactions", {
    method: "POST",
    body: JSON.stringify(input),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function listPending(): Promise<PendingReview[]> {
  const res = await authedFetch("/transactions/pending");
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function confirmPending(id: string): Promise<TransactionRecord> {
  const res = await authedFetch(`/transactions/pending/${id}/confirm`, {
    method: "POST",
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function rejectPending(id: string): Promise<{ id: string; status: string }> {
  const res = await authedFetch(`/transactions/pending/${id}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export type CategoryStat = { name: string; count: number };
export type UserCategories = {
  income: CategoryStat[];
  expenses: CategoryStat[];
};

/**
 * Categorías que el usuario ya tiene en sus transacciones, separadas por
 * tipo y ordenadas por frecuencia. La UI las usa como sugerencias en los
 * modales de alta. Si la cuenta es nueva el backend devuelve un fallback
 * canónico (Salary/Deposit, Food/Leisure/Invoice/Investment/Vacations).
 */
export async function listUserCategories(): Promise<UserCategories> {
  const res = await authedFetch("/transactions/categories");
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function listTransactions(): Promise<TransactionRecord[]> {
  const res = await authedFetch("/transactions");
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function classifyArea(description: string): Promise<string> {
  const fd = new FormData();
  fd.append("text", description);
  const res = await authedFetch("/modules/p2/classify-area", {
    method: "POST",
    body: fd,
  });
  if (!res.ok) throw new Error(await parseError(res));
  const data = await res.json();
  return data.predicted_category;
}
