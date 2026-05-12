import type { Metadata } from "next";
import "./globals.css";

import { NavBar } from "@/components/NavBar";

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
        <NavBar />
        {/* Las páginas controlan su propio ancho y fondo (gradientes,
            min-h-screen, etc.); el layout solo aporta el NavBar global. */}
        {children}
      </body>
    </html>
  );
}
