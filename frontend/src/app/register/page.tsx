"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";

import { WebcamCapture } from "@/components/WebcamCapture";
import { register, setToken } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/Card";

export default function RegisterPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [passphrase, setPassphrase] = useState("");
  const [consent, setConsent] = useState(false);
  const [face, setFace] = useState<Blob | null>(null);
  const [notificationsEnabled, setNotificationsEnabled] = useState(false);
  const [telegramChatId, setTelegramChatId] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    
    if (!face) {
      setError("Captura una foto primero.");
      return;
    }
    if (passphrase.length < 6) {
      setError("La contraseña debe tener al menos 6 caracteres.");
      return;
    }
    if (notificationsEnabled && !telegramChatId.trim()) {
      setError("Debes proporcionar tu Chat ID de Telegram para activar las notificaciones.");
      return;
    }
    setLoading(true);
    try {
      const r = await register(email, passphrase, consent, face, notificationsEnabled, telegramChatId);
      setToken(r.access_token, r.user_id);
      router.push("/chat");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-blue-50 via-white to-purple-50 p-4">
      <div className="w-full max-w-md space-y-8 animate-fadeIn">
        {/* Header con branding */}
        <div className="text-center space-y-2">
          <div className="inline-flex items-center justify-center w-16 h-16 bg-gradient-to-br from-emerald-600 to-teal-600 rounded-2xl shadow-lg mb-4">
            <svg className="w-8 h-8 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M18 9v3m0 0v3m0-3h3m-3 0h-3m-2-5a4 4 0 11-8 0 4 4 0 018 0zM3 20a6 6 0 0112 0v1H3v-1z" />
            </svg>
          </div>
          <h1 className="text-3xl font-bold gradient-text">Crear cuenta</h1>
          <p className="text-slate-600">
            Únete a nuestro sistema financiero inteligente con autenticación biométrica
          </p>
        </div>

        {/* Formulario */}
        <Card variant="glass" className="animate-slideIn">
          <CardHeader className="text-center pb-6">
            <CardTitle className="text-xl">Registro</CardTitle>
            <CardDescription>
              Tu rostro se usará como factor biométrico. Se cifra con AES-128 + PBKDF2 antes de almacenarse.
            </CardDescription>
          </CardHeader>
          
          <CardContent className="space-y-6">
            <form onSubmit={onSubmit} className="space-y-5">
              <Input
                type="email"
                label="Email"
                placeholder="tu@email.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                autoComplete="email"
                icon={
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M16 12a4 4 0 10-8 0 4 4 0 008 0zm0 0v1.5M16 12h4m-4 0h-4" />
                  </svg>
                }
              />

              <Input
                type="password"
                label="Contraseña"
                placeholder="••••••••"
                value={passphrase}
                onChange={(e) => setPassphrase(e.target.value)}
                required
                autoComplete="new-password"
                helperText="Mínimo 6 caracteres"
                icon={
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
                  </svg>
                }
              />

              <div className="space-y-3">
                <label className="flex items-start space-x-3 p-3 bg-slate-50/50 rounded-lg cursor-pointer hover:bg-slate-100/50 transition-colors">
                  <input
                    type="checkbox"
                    required
                    checked={consent}
                    onChange={(e) => setConsent(e.target.checked)}
                    className="mt-1 rounded border-slate-300 text-blue-600 focus:ring-blue-500"
                  />
                  <div className="flex-1">
                    <span className="text-sm font-medium text-slate-700">Consentimiento biométrico</span>
                    <p className="text-xs text-slate-500 mt-1">
                      Acepto el tratamiento de mis datos biométricos según lo establecido en el RGPD. 
                      Los datos se cifrarán antes de almacenarse.
                    </p>
                  </div>
                </label>
              </div>

              <div className="space-y-3">
                <label className="text-sm font-medium text-slate-700 flex items-center">
                  <svg className="w-4 h-4 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 9a2 2 0 012-2h.93a2 2 0 001.664-.89l.812-1.22A2 2 0 0110.07 4h3.86a2 2 0 011.664.89l.812 1.22A2 2 0 0018.07 7H19a2 2 0 012 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2V9z" />
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 13a3 3 0 11-6 0 3 3 0 016 0z" />
                  </svg>
                  Foto biométrica
                </label>
                <div className="relative">
                  {/* mode="photo" obligatorio: /auth/register espera JPEG/PNG.
                      El default del componente es "video" (3.5s WebM) y enviar
                      un WebM como face.jpg revienta el decoder del backend y
                      en payloads grandes vía ngrok produce "Failed to fetch". */}
                  <WebcamCapture mode="photo" onCapture={setFace} />
                  {face && (
                    <div className="absolute top-2 right-2 bg-green-500 text-white px-2 py-1 rounded-full text-xs flex items-center">
                      <svg className="w-3 h-3 mr-1" fill="currentColor" viewBox="0 0 20 20">
                        <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
                      </svg>
                      Capturada
                    </div>
                  )}
                </div>
              </div>

              {/* Notificaciones Telegram */}
              <div className="space-y-3">
                <label className="flex items-start space-x-3 p-3 bg-slate-50/50 rounded-lg cursor-pointer hover:bg-slate-100/50 transition-colors">
                  <input
                    type="checkbox"
                    checked={notificationsEnabled}
                    onChange={(e) => setNotificationsEnabled(e.target.checked)}
                    className="mt-1 rounded border-slate-300 text-blue-600 focus:ring-blue-500"
                  />
                  <div className="flex-1">
                    <span className="text-sm font-medium text-slate-700">Notificaciones de seguridad</span>
                    <p className="text-xs text-slate-500 mt-1">
                      Recibe alertas en Telegram cuando se detecten anomalías o eventos importantes en tu cuenta
                    </p>
                  </div>
                </label>

                {notificationsEnabled && (
                  <div className="space-y-2 p-3 bg-blue-50/50 rounded-lg border border-blue-200/50">
                    <div className="space-y-2">
                      <p className="text-sm font-medium text-blue-800">Configurar Telegram:</p>
                      <ol className="text-xs text-blue-700 space-y-1 list-decimal list-inside">
                        <li>Busca el bot <code>@P6SecurityBot</code> en Telegram</li>
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
              </div>

              {error && (
                <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-lg flex items-center">
                  <svg className="w-4 h-4 mr-2" fill="currentColor" viewBox="0 0 20 20">
                    <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clipRule="evenodd" />
                  </svg>
                  {error}
                </div>
              )}

              <Button
                type="submit"
                variant="primary"
                size="lg"
                loading={loading}
                disabled={loading || !face || !consent}
                className="w-full"
                icon={
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M18 9v3m0 0v3m0-3h3m-3 0h-3m-2-5a4 4 0 11-8 0 4 4 0 018 0zM3 20a6 6 0 0112 0v1H3v-1z" />
                  </svg>
                }
              >
                {loading ? "Creando cuenta..." : "Crear cuenta"}
              </Button>
            </form>

            <div className="text-center pt-4 border-t border-slate-200">
              <p className="text-sm text-slate-600">
                ¿Ya tienes cuenta?{" "}
                <Link
                  href="/login"
                  className="font-medium text-blue-600 hover:text-blue-500 transition-colors"
                >
                  Inicia sesión
                </Link>
              </p>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
