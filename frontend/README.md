# P6 Frontend (Next.js)

Cliente web del sistema multiagente. Consume los endpoints de la API FastAPI
(ver el README del proyecto raíz para arrancar el backend).

## Stack

- **Next.js 14** (App Router) + **TypeScript**
- **Tailwind CSS** para layout (clases utilitarias)
- **Fetch nativo** del navegador (sin axios)
- Captura de webcam via `navigator.mediaDevices.getUserMedia` + `<canvas>`

## Instalación

Necesitas Node.js ≥ 18 (recomendado 20+).

```bash
cd frontend
npm install
cp .env.local.example .env.local
# editar .env.local si la API no está en localhost:8000
```

## Arranque en dev

Asegúrate primero de que el backend está corriendo:

```bash
# en otra terminal, desde la raíz del proyecto:
.venv/Scripts/python.exe -m uvicorn src.api.main:app --reload --port 8000
```

Y luego el frontend:

```bash
npm run dev
# → http://localhost:3000
```

## Estructura

```
frontend/
├── src/
│   ├── app/
│   │   ├── layout.tsx        # Header + nav + main
│   │   ├── page.tsx          # Landing (redirige a /chat si hay token)
│   │   ├── login/page.tsx    # email + passphrase + webcam
│   │   ├── register/page.tsx # email + passphrase + consent + webcam
│   │   ├── chat/page.tsx     # Conversación con el orquestador
│   │   ├── pending/page.tsx  # Revisión de transacciones pendientes
│   │   └── globals.css       # Tailwind + clases utilitarias propias
│   ├── components/
│   │   └── WebcamCapture.tsx # getUserMedia → canvas → Blob JPEG
│   └── lib/
│       └── api.ts            # Cliente HTTP del backend
├── package.json
├── tsconfig.json
├── tailwind.config.ts
├── postcss.config.mjs
├── next.config.mjs
└── .env.local.example
```

## Flujo de uso

1. **/register**: nuevo usuario. Webcam + email + contraseña + consentimiento
   biométrico (RGPD). El backend almacena el embedding facial cifrado.
2. **/login**: usuario existente. Webcam para verificar la cara contra la
   plantilla almacenada + contraseña con bcrypt. Devuelve un JWT que se
   guarda en `localStorage`.
3. **/chat**: conversación con el orquestador. El `session_id` se mantiene
   en estado para que el `MemorySaver` del backend pueda dar contexto a
   turnos sucesivos.
4. **/pending**: lista las transacciones que el agente Security marcó como
   posibles anomalías. El usuario las aprueba (cuentan en analytics) o las
   rechaza (no contabilizan).

## Notas operativas

- **HTTPS o localhost requeridos** para `getUserMedia`. En navegadores
  modernos no se puede acceder a la webcam por HTTP excepto en `localhost`.
- **CORS**: el backend ya permite `http://localhost:3000` por defecto
  (configurable vía `CORS_ORIGINS` en el `.env`).
- **Token**: se guarda en `localStorage`. Cuando expira (60 min por
  defecto, configurable con `JWT_EXPIRE_MINUTES`), las próximas peticiones
  devolverán 401 y la app redirige automáticamente a `/login`.

## Producción

```bash
npm run build
npm run start    # sirve en :3000
```

Despliegue recomendado para la versión gratuita: Vercel (importa el repo,
detecta Next.js automáticamente). Configura `NEXT_PUBLIC_API_BASE_URL` con
la URL pública del backend (Cloudflare Tunnel, Railway, etc.).
