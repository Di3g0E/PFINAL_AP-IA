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

export type ChatResponse = {
  response: string;
  session_id: string;
  last_action?: string | null;
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
  face?: Blob,  // Opcional - desactivado temporalmente
): Promise<TokenResponse> {
  const fd = new FormData();
  fd.append("email", email);
  fd.append("passphrase", passphrase);
  
  // BIOMETRÍA DESACTIVADA TEMPORALMENTE
  if (face) {
    fd.append("face", face, "face.jpg");
  }
  // Si no hay face, no lo añadimos - el backend lo manejará como opcional
  
  const res = await fetch(`${API_BASE}/auth/login`, {
    method: "POST",
    body: fd,
    headers: { "ngrok-skip-browser-warning": "true" },
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
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
