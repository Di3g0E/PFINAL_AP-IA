"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";

/**
 * Landing: si el usuario tiene token, lo manda a /chat. Si no, muestra
 * tarjetas con los CTAs (login / register) y un breve resumen del sistema.
 */
export default function LandingPage() {
  const router = useRouter();
  const [hasToken, setHasToken] = useState<boolean | null>(null);

  useEffect(() => {
    const t = localStorage.getItem("token");
    if (t) {
      router.replace("/chat");
    } else {
      setHasToken(false);
    }
  }, [router]);

  if (hasToken === null) {
    return (
      <div className="flex items-center justify-center min-h-[60vh]">
        <div className="loading w-16 h-16 rounded-full"></div>
      </div>
    );
  }

  return (
    <section className="space-y-8 animate-fadeIn">
      <header className="text-center space-y-4">
        <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-gradient-to-r from-blue-50 to-purple-50 border border-blue-200/50 text-sm">
          <span className="w-2 h-2 bg-green-500 rounded-full animate-pulse"></span>
          <span className="text-blue-700 font-medium">Sistema en línea</span>
        </div>
        <h1 className="text-4xl md:text-5xl font-bold tracking-tight">
          <span className="gradient-text">Sistema multiagente</span>
          <br />
          <span className="text-slate-900">de gestión financiera</span>
        </h1>
        <p className="mt-4 max-w-3xl mx-auto text-lg text-slate-600 leading-relaxed">
          Cuatro agentes especializados (Orchestrator, Security, Registrar, Analyst) 
          construidos sobre LangGraph. Autenticación robusta con passphrase y biometría facial. 
          Cumplimiento RGPD con notificaciones opt-in.
        </p>
      </header>

      <div className="grid gap-6 md:grid-cols-2">
        <div className="card animate-slideIn" style={{ animationDelay: '0.2s' }}>
          <div className="flex items-center gap-3 mb-4">
            <div className="w-12 h-12 rounded-lg bg-gradient-to-br from-blue-500 to-purple-600 flex items-center justify-center">
              <svg className="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M18 9v3m0 0v3m0-3h3m-3 0h-3m-2-5a4 4 0 11-8 0 4 4 0 018 0zM3 20a6 6 0 0112 0v1H3v-1z" />
              </svg>
            </div>
            <h2 className="text-xl font-bold text-slate-900">¿Eres usuario nuevo?</h2>
          </div>
          <p className="text-slate-600 leading-relaxed mb-6">
            Crea tu cuenta con email, contraseña y captura facial. Tu biometría se 
            convierte en tu llave de acceso segura para futuras sesiones.
          </p>
          <div className="space-y-3">
            <div className="flex items-center gap-2 text-sm text-slate-500">
              <svg className="w-4 h-4 text-green-500" fill="currentColor" viewBox="0 0 20 20">
                <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clipRule="evenodd" />
              </svg>
              <span>Registro en menos de 2 minutos</span>
            </div>
            <div className="flex items-center gap-2 text-sm text-slate-500">
              <svg className="w-4 h-4 text-green-500" fill="currentColor" viewBox="0 0 20 20">
                <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clipRule="evenodd" />
              </svg>
              <span>Biometría cifrada y segura</span>
            </div>
            <div className="flex items-center gap-2 text-sm text-slate-500">
              <svg className="w-4 h-4 text-green-500" fill="currentColor" viewBox="0 0 20 20">
                <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clipRule="evenodd" />
              </svg>
              <span>Acceso inmediato al chat</span>
            </div>
          </div>
          <Link href="/register" className="btn-primary mt-6 inline-block w-full text-center animate-pulse-hover">
            Crear cuenta
          </Link>
        </div>

        <div className="card animate-slideIn" style={{ animationDelay: '0.4s' }}>
          <div className="flex items-center gap-3 mb-4">
            <div className="w-12 h-12 rounded-lg bg-gradient-to-br from-green-500 to-teal-600 flex items-center justify-center">
              <svg className="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 16l-4-4m0 0l4-4m-4 4h14m-5 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h7a3 3 0 013 3v1" />
              </svg>
            </div>
            <h2 className="text-xl font-bold text-slate-900">¿Ya tienes cuenta?</h2>
          </div>
          <p className="text-slate-600 leading-relaxed mb-6">
            Inicia sesión con tu contraseña y una nueva captura facial. El sistema 
            verifica tu identidad de forma segura y rápida.
          </p>
          <div className="space-y-3">
            <div className="flex items-center gap-2 text-sm text-slate-500">
              <svg className="w-4 h-4 text-blue-500" fill="currentColor" viewBox="0 0 20 20">
                <path fillRule="evenodd" d="M2.166 4.999A11.954 11.954 0 0010 1.944 11.954 11.954 0 0017.834 5c.11.65.166 1.32.166 2.001 0 5.225-3.34 9.67-8 11.317C5.34 16.67 2 12.225 2 7c0-.682.057-1.35.166-2.001zm11.541 3.708a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clipRule="evenodd" />
              </svg>
              <span>Login biométrico instantáneo</span>
            </div>
            <div className="flex items-center gap-2 text-sm text-slate-500">
              <svg className="w-4 h-4 text-blue-500" fill="currentColor" viewBox="0 0 20 20">
                <path fillRule="evenodd" d="M2.166 4.999A11.954 11.954 0 0010 1.944 11.954 11.954 0 0017.834 5c.11.65.166 1.32.166 2.001 0 5.225-3.34 9.67-8 11.317C5.34 16.67 2 12.225 2 7c0-.682.057-1.35.166-2.001zm11.541 3.708a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clipRule="evenodd" />
              </svg>
              <span>Verificación multifactor</span>
            </div>
            <div className="flex items-center gap-2 text-sm text-slate-500">
              <svg className="w-4 h-4 text-blue-500" fill="currentColor" viewBox="0 0 20 20">
                <path fillRule="evenodd" d="M2.166 4.999A11.954 11.954 0 0010 1.944 11.954 11.954 0 0017.834 5c.11.65.166 1.32.166 2.001 0 5.225-3.34 9.67-8 11.317C5.34 16.67 2 12.225 2 7c0-.682.057-1.35.166-2.001zm11.541 3.708a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clipRule="evenodd" />
              </svg>
              <span>Acceso a todos los agentes</span>
            </div>
          </div>
          <Link href="/login" className="btn-secondary mt-6 inline-block w-full text-center animate-pulse-hover">
            Iniciar sesión
          </Link>
        </div>
      </div>

      <div className="mt-12 text-center">
        <div className="inline-flex items-center gap-6 text-sm text-slate-500">
          <div className="flex items-center gap-2">
            <div className="w-2 h-2 bg-green-500 rounded-full"></div>
            <span>Seguridad de nivel bancario</span>
          </div>
          <div className="flex items-center gap-2">
            <div className="w-2 h-2 bg-blue-500 rounded-full"></div>
            <span>IA avanzada LangGraph</span>
          </div>
          <div className="flex items-center gap-2">
            <div className="w-2 h-2 bg-purple-500 rounded-full"></div>
            <span>RGPD compliant</span>
          </div>
        </div>
      </div>
    </section>
  );
}
