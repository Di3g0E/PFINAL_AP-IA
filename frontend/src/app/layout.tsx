import type { Metadata } from "next";
import "./globals.css";
import Link from "next/link";

export const metadata: Metadata = {
  title: "P6 AP-IA — Sistema multiagente",
  description: "Gestión financiera con agentes LangGraph + biometría facial",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="es">
      <body className="min-h-full">
        <header className="border-b border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-950">
          <nav className="mx-auto flex max-w-5xl items-center justify-between px-4 py-3">
            <Link href="/" className="font-semibold tracking-tight">
              P6 · Multiagente Financiero
            </Link>
            <div className="flex items-center gap-3 text-sm">
              <Link href="/chat" className="hover:underline">Chat</Link>
              <Link href="/pending" className="hover:underline">Pendientes</Link>
              <Link href="/login" className="hover:underline">Login</Link>
            </div>
          </nav>
        </header>
        <main className="mx-auto max-w-5xl px-4 py-6">{children}</main>
      </body>
    </html>
  );
}
