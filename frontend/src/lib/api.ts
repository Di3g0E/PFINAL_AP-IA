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

async function parseError(res: Response): Promise<string> {
  try {
    const body = await res.json();
    return body.detail ?? body.message ?? `Error ${res.status}`;
  } catch {
    return `Error ${res.status} ${res.statusText}`;
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
  const res = await fetch(`${API_BASE}${path}`, { ...init, headers });
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
  
  const res = await fetch(`${API_BASE}/auth/register`, {
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
  
  const res = await fetch(`${API_BASE}/auth/login`, {
    method: "POST",
    body: fd,
    headers: { "ngrok-skip-browser-warning": "true" },
  });
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
