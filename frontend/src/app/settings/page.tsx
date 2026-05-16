"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { getIsAdmin, getUserId, getUserRole, updateUserRole, type UserRole } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/Card";

export default function SettingsPage() {
  const router = useRouter();
  const [userId, setUserId] = useState<string | null>(null);
  const [notificationsEnabled, setNotificationsEnabled] = useState(false);
  const [telegramChatId, setTelegramChatId] = useState("");
  const [notificationLevel, setNotificationLevel] = useState<"redacted" | "full">("redacted");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  // Rol del usuario (basic|advanced)
  const [role, setRole] = useState<UserRole>("basic");
  const [roleSaving, setRoleSaving] = useState(false);

  // Protege la ruta
  useEffect(() => {
    const uid = getUserId();
    if (!uid) {
      router.replace("/login");
    } else if (getIsAdmin()) {
      router.replace("/admin/monitor");
    } else {
      setUserId(uid);
      loadSettings();
      loadRole();
    }
  }, [router]);

  const loadRole = async () => {
    try {
      const r = await getUserRole();
      setRole(r);
    } catch (err) {
      console.warn("getUserRole falló:", err);
    }
  };

  const onChangeRole = async (newRole: UserRole) => {
    if (newRole === role || roleSaving) return;
    setRoleSaving(true);
    setError(null);
    setSuccess(null);
    try {
      const saved = await updateUserRole(newRole);
      setRole(saved);
      setSuccess(`Rol actualizado a "${saved}". El asistente adapta sus respuestas en tiempo real.`);
    } catch (err) {
      setError(`No se pudo actualizar el rol: ${(err as Error).message}`);
    } finally {
      setRoleSaving(false);
    }
  };

  const loadSettings = async () => {
    try {
      const token = localStorage.getItem("token");
      const response = await fetch(`${process.env.NEXT_PUBLIC_API_BASE_URL}/api/user/settings`, {
        headers: {
          "Authorization": `Bearer ${token}`,
          "ngrok-skip-browser-warning": "true",
        },
      });

      if (response.ok) {
        const settings = await response.json();
        setNotificationsEnabled(settings.notifications_enabled || false);
        setTelegramChatId(settings.telegram_chat_id || "");
        setNotificationLevel(settings.notification_level || "redacted");
      }
    } catch (err) {
      console.error("Error loading settings:", err);
    }
  };

  const saveSettings = async () => {
    setError(null);
    setSuccess(null);
    setLoading(true);

    try {
      const token = localStorage.getItem("token");
      const response = await fetch(`${process.env.NEXT_PUBLIC_API_BASE_URL}/api/user/settings`, {
        method: "PUT",
        headers: {
          "Authorization": `Bearer ${token}`,
          "Content-Type": "application/json",
          "ngrok-skip-browser-warning": "true",
        },
        body: JSON.stringify({
          notifications_enabled: notificationsEnabled,
          telegram_chat_id: notificationsEnabled ? telegramChatId : null,
          notification_level: notificationLevel,
        }),
      });

      if (!response.ok) {
        throw new Error("Error al guardar configuración");
      }

      setSuccess("Configuración guardada correctamente");

      // Si se activaron las notificaciones, enviar una de prueba
      if (notificationsEnabled && telegramChatId) {
        await testNotification();
      }
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  };

  const testNotification = async () => {
    setError(null);
    setSuccess(null);
    try {
      const token = localStorage.getItem("token");
      const response = await fetch(
        `${process.env.NEXT_PUBLIC_API_BASE_URL}/api/user/test-notification`,
        {
          method: "POST",
          headers: {
            "Authorization": `Bearer ${token}`,
            "ngrok-skip-browser-warning": "true",
          },
        }
      );

      if (!response.ok) {
        // El backend devuelve {detail: "Telegram falló: ..."} en errores
        let detail = `Error ${response.status}`;
        try {
          const body = await response.json();
          detail = body.detail ?? detail;
        } catch {
          // ignore
        }
        throw new Error(detail);
      }

      setSuccess("✅ Notificación de prueba enviada a Telegram");
    } catch (err) {
      const msg = (err as Error).message;
      console.error("Error sending test notification:", msg);
      setError(`No se pudo enviar la notificación de prueba: ${msg}`);
    }
  };

  const runDiagnostic = async () => {
    setError(null);
    setSuccess(null);
    try {
      const token = localStorage.getItem("token");
      const response = await fetch(
        `${process.env.NEXT_PUBLIC_API_BASE_URL}/api/user/telegram-status`,
        {
          headers: {
            "Authorization": `Bearer ${token}`,
            "ngrok-skip-browser-warning": "true",
          },
        }
      );
      if (!response.ok) {
        throw new Error(`Error ${response.status}`);
      }
      const data = await response.json();

      const lines: string[] = [];
      lines.push(`Token configurado en servidor: ${data.token_configured ? "✅" : "❌"}`);
      lines.push(`Chat ID guardado: ${data.chat_id_saved ?? "—"}`);
      lines.push(`Notificaciones activas: ${data.notifications_enabled ? "✅" : "❌"}`);
      lines.push(`Bot accesible: ${data.bot_reachable ? "✅" : "❌"}`);
      if (data.bot_info) {
        lines.push(`Bot real del servidor: @${data.bot_info.username} (${data.bot_info.first_name})`);
      }
      if (data.error) {
        lines.push(`Error: ${data.error}`);
      }

      const text = lines.join("\n");
      if (data.bot_info && data.bot_info.username !== "finances_guy_bot") {
        setError(text + `\n⚠️ El bot del servidor (@${data.bot_info.username}) no coincide con @finances_guy_bot.`);
      } else if (!data.token_configured || !data.bot_reachable) {
        setError(text);
      } else {
        setSuccess(text);
      }
    } catch (err) {
      setError(`Diagnóstico falló: ${(err as Error).message}`);
    }
  };

  if (!userId) {
    return <p className="text-sm text-slate-500">Cargando…</p>;
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-blue-50 via-white to-purple-50">
      {/* Main Content */}
      <main className="max-w-4xl mx-auto px-4 py-6">
        <div className="mb-6">
          <h1 className="text-2xl font-bold gradient-text">Configuración</h1>
          <p className="text-sm text-slate-500 mt-1">
            Gestiona tus notificaciones y preferencias
          </p>
        </div>
        <div className="space-y-6">
          {/* Perfil del asistente */}
          <Card variant="glass">
            <CardHeader>
              <CardTitle className="flex items-center">
                <svg className="w-5 h-5 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
                </svg>
                Perfil del asistente
              </CardTitle>
              <CardDescription>
                Cómo el asistente adapta el tono y nivel de detalle de las respuestas a ti
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <label
                  className={`p-4 rounded-lg border-2 cursor-pointer transition-all ${
                    role === "basic"
                      ? "border-blue-500 bg-blue-50/70"
                      : "border-slate-200 bg-white/60 hover:border-slate-300"
                  }`}
                >
                  <input
                    type="radio"
                    name="role"
                    value="basic"
                    checked={role === "basic"}
                    onChange={() => onChangeRole("basic")}
                    disabled={roleSaving}
                    className="sr-only"
                  />
                  <div className="flex items-start space-x-3">
                    <div className="text-2xl">🌱</div>
                    <div className="flex-1">
                      <p className="font-semibold text-slate-800">Básico</p>
                      <p className="text-xs text-slate-500 mt-1">
                        Lenguaje cotidiano, frases cortas, cifras redondeadas. Sin tecnicismos.
                      </p>
                    </div>
                  </div>
                </label>

                <label
                  className={`p-4 rounded-lg border-2 cursor-pointer transition-all ${
                    role === "advanced"
                      ? "border-purple-500 bg-purple-50/70"
                      : "border-slate-200 bg-white/60 hover:border-slate-300"
                  }`}
                >
                  <input
                    type="radio"
                    name="role"
                    value="advanced"
                    checked={role === "advanced"}
                    onChange={() => onChangeRole("advanced")}
                    disabled={roleSaving}
                    className="sr-only"
                  />
                  <div className="flex items-start space-x-3">
                    <div className="text-2xl">🎓</div>
                    <div className="flex-1">
                      <p className="font-semibold text-slate-800">Avanzado</p>
                      <p className="text-xs text-slate-500 mt-1">
                        Detallado: cifras con decimales, porcentajes, varianzas y términos financieros.
                      </p>
                    </div>
                  </div>
                </label>
              </div>
              {roleSaving && (
                <p className="mt-3 text-xs text-slate-500">Guardando…</p>
              )}
            </CardContent>
          </Card>

          {/* Notificaciones Telegram */}
          <Card variant="glass">
            <CardHeader>
              <CardTitle className="flex items-center">
                <svg className="w-5 h-5 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
                </svg>
                Notificaciones de Telegram
              </CardTitle>
              <CardDescription>
                Recibe alertas de seguridad y financieras en tiempo real
              </CardDescription>
            </CardHeader>
            
            <CardContent className="space-y-6">
              <div className="flex items-center justify-between p-4 bg-slate-50/50 rounded-lg">
                <div>
                  <h3 className="font-medium text-slate-800">Activar notificaciones</h3>
                  <p className="text-sm text-slate-600">Recibe alertas cuando se detecten eventos importantes</p>
                </div>
                <label className="relative inline-flex items-center cursor-pointer">
                  <input
                    type="checkbox"
                    checked={notificationsEnabled}
                    onChange={(e) => setNotificationsEnabled(e.target.checked)}
                    className="sr-only peer"
                  />
                  <div className="w-11 h-6 bg-gray-200 peer-focus:outline-none peer-focus:ring-4 peer-focus:ring-blue-300 rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-blue-600"></div>
                </label>
              </div>

              {notificationsEnabled && (
                <div className="space-y-4 p-4 bg-blue-50/50 rounded-lg border border-blue-200/50">
                  <div className="space-y-3">
                    <h4 className="font-medium text-blue-800">Configurar Telegram:</h4>
                    <ol className="text-sm text-blue-700 space-y-1 list-decimal list-inside">
                      <li>Busca el bot <code>@finances_guy_bot</code> en Telegram</li>
                      <li>Envía <code>/start</code> al bot</li>
                      <li>Copia el ID que te muestra el bot</li>
                      <li>Pégalo en el campo de abajo</li>
                    </ol>
                    <Input
                      type="text"
                      placeholder="123456789"
                      value={telegramChatId}
                      onChange={(e) => setTelegramChatId(e.target.value)}
                      helperText="Tu Chat ID de Telegram (solo números)"
                      icon={
                        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
                        </svg>
                      }
                    />
                  </div>
                </div>
              )}

              {/* Nivel de privacidad */}
              <div className="space-y-3">
                <h4 className="font-medium text-slate-800">Nivel de privacidad</h4>
                <div className="space-y-2">
                  <label className="flex items-center space-x-3 p-3 bg-slate-50/50 rounded-lg cursor-pointer hover:bg-slate-100/50">
                    <input
                      type="radio"
                      name="privacy"
                      value="redacted"
                      checked={notificationLevel === "redacted"}
                      onChange={() => setNotificationLevel("redacted")}
                      className="text-blue-600"
                    />
                    <div className="flex-1">
                      <span className="text-sm font-medium text-slate-700">Modo seguro (recomendado)</span>
                      <p className="text-xs text-slate-500">Solo eventos y categorías, sin importes ni detalles</p>
                    </div>
                  </label>
                  <label className="flex items-center space-x-3 p-3 bg-slate-50/50 rounded-lg cursor-pointer hover:bg-slate-100/50">
                    <input
                      type="radio"
                      name="privacy"
                      value="full"
                      checked={notificationLevel === "full"}
                      onChange={() => setNotificationLevel("full")}
                      className="text-blue-600"
                    />
                    <div className="flex-1">
                      <span className="text-sm font-medium text-slate-700">Modo completo</span>
                      <p className="text-xs text-slate-500">Incluye todos los detalles de las transacciones</p>
                    </div>
                  </label>
                </div>
              </div>

              {/* Botones de acción */}
              <div className="flex gap-3">
                <Button
                  variant="primary"
                  onClick={saveSettings}
                  disabled={loading || (notificationsEnabled && !telegramChatId.trim())}
                  loading={loading}
                  className="flex-1"
                >
                  {loading ? "Guardando..." : "Guardar configuración"}
                </Button>
                
                {notificationsEnabled && telegramChatId && (
                  <Button
                    variant="secondary"
                    onClick={testNotification}
                    icon={
                      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
                      </svg>
                    }
                  >
                    Probar
                  </Button>
                )}
                <Button
                  variant="ghost"
                  onClick={runDiagnostic}
                  title="Comprueba el estado del bot, token y chat_id"
                >
                  Diagnóstico
                </Button>
              </div>

              {/* Mensajes de estado */}
              {error && (
                <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-lg flex items-start">
                  <svg className="w-4 h-4 mr-2 mt-0.5 shrink-0" fill="currentColor" viewBox="0 0 20 20">
                    <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clipRule="evenodd" />
                  </svg>
                  <span className="whitespace-pre-line text-sm">{error}</span>
                </div>
              )}

              {success && (
                <div className="bg-green-50 border border-green-200 text-green-700 px-4 py-3 rounded-lg flex items-start">
                  <svg className="w-4 h-4 mr-2 mt-0.5 shrink-0" fill="currentColor" viewBox="0 0 20 20">
                    <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clipRule="evenodd" />
                  </svg>
                  <span className="whitespace-pre-line text-sm">{success}</span>
                </div>
              )}
            </CardContent>
          </Card>

          {/* Información adicional */}
          <Card variant="glass">
            <CardHeader>
              <CardTitle>Información de seguridad</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="space-y-3">
                <div className="flex items-start space-x-3">
                  <svg className="w-5 h-5 text-blue-600 mt-0.5" fill="currentColor" viewBox="0 0 20 20">
                    <path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a1 1 0 000 2v3a1 1 0 001 1h1a1 1 0 100-2v-3a1 1 0 00-1-1H9z" clipRule="evenodd" />
                  </svg>
                  <div>
                    <h4 className="font-medium text-slate-800">¿Por qué necesito notificaciones?</h4>
                    <p className="text-sm text-slate-600">Recibirás alertas inmediatas cuando se detecten anomalías en tus transacciones o eventos de seguridad importantes.</p>
                  </div>
                </div>
                
                <div className="flex items-start space-x-3">
                  <svg className="w-5 h-5 text-green-600 mt-0.5" fill="currentColor" viewBox="0 0 20 20">
                    <path fillRule="evenodd" d="M2.166 4.999A11.954 11.954 0 0010 1.944 11.954 11.954 0 0017.834 5c.11.65.166 1.32.166 2.001 0 5.225-4.25 9.5-9.5 9.5s-9.5-4.275-9.5-9.5a9.635 9.635 0 01.166-2.001zm2.516 5.001h6.44a1 1 0 001-1v-5a1 1 0 00-1-1h-6.44a1 1 0 00-1 1v5a1 1 0 001 1z" clipRule="evenodd" />
                  </svg>
                  <div>
                    <h4 className="font-medium text-slate-800">¿Es seguro?</h4>
                    <p className="text-sm text-slate-600">Sí. Por defecto usamos el modo "redacted" que no incluye información sensible. Puedes cambiar al modo "completo" si lo prefieres.</p>
                  </div>
                </div>
                
                <div className="flex items-start space-x-3">
                  <svg className="w-5 h-5 text-amber-600 mt-0.5" fill="currentColor" viewBox="0 0 20 20">
                    <path fillRule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clipRule="evenodd" />
                  </svg>
                  <div>
                    <h4 className="font-medium text-slate-800">¿Puedo desactivarlo?</h4>
                    <p className="text-sm text-slate-600">Sí, puedes desactivar las notificaciones en cualquier momento desde esta misma página.</p>
                  </div>
                </div>
              </div>
            </CardContent>
          </Card>
        </div>
      </main>
    </div>
  );
}
