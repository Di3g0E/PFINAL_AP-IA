"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { clearToken, getUserId } from "@/lib/api";

/**
 * Barra de navegación global. Cliente-only porque depende de `localStorage`.
 *
 * Visibilidad de enlaces:
 *   - No autenticado: Login
 *   - Autenticado:    Chat · Pendientes · Configuración · Cerrar sesión
 *
 * Se refresca ante:
 *   - `storage` (login/logout en otra pestaña)
 *   - `auth-change` (login/logout en la misma pestaña, lo emite api.ts)
 */
export function NavBar() {
  const pathname = usePathname();
  const router = useRouter();
  const [isAuthed, setIsAuthed] = useState(false);
  const [isAdmin, setIsAdmin] = useState(false);

  useEffect(() => {
    const refresh = () => {
      setIsAuthed(Boolean(getUserId()));
      setIsAdmin(getUserId() === "admin");
    };
    refresh();
    window.addEventListener("storage", refresh);
    window.addEventListener("auth-change", refresh);
    return () => {
      window.removeEventListener("storage", refresh);
      window.removeEventListener("auth-change", refresh);
    };
  }, []);

  const onLogout = () => {
    clearToken();
    router.replace("/login");
  };

  const linkCls = (href: string) => {
    const active = pathname === href;
    return [
      "transition-colors px-2 py-1 rounded-md",
      active
        ? "text-blue-600 font-semibold bg-blue-50"
        : "text-slate-600 hover:text-blue-600 hover:bg-slate-100",
    ].join(" ");
  };

  return (
    <header className="border-b border-slate-200 bg-white/80 backdrop-blur-sm sticky top-0 z-50">
      <nav className="mx-auto flex max-w-5xl items-center justify-between px-4 py-3">
        <Link
          href={isAuthed ? "/chat" : "/"}
          className="font-semibold tracking-tight text-slate-800"
        >
          P6 · Multiagente Financiero
        </Link>
        <div className="flex items-center gap-1 text-sm">
          {isAuthed ? (
            <>
              <Link href="/chat" className={linkCls("/chat")}>Chat</Link>
              <Link href="/pending" className={linkCls("/pending")}>Pendientes</Link>
              <Link href="/records" className={linkCls("/records")}>Registros</Link>
              {isAdmin && <Link href="/admin/monitor" className={linkCls("/admin/monitor")}>Monitor</Link>}
              <Link href="/settings" className={linkCls("/settings")}>Configuración</Link>
              <button
                onClick={onLogout}
                className="ml-1 px-2 py-1 rounded-md text-red-600 hover:bg-red-50 transition-colors"
              >
                Cerrar sesión
              </button>
            </>
          ) : (
            <Link href="/login" className={linkCls("/login")}>Login</Link>
          )}
        </div>
      </nav>
    </header>
  );
}
