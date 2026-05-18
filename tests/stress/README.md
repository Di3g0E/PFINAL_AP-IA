# Tests de carga (Locust)

Comprueban cómo aguanta la API con varios usuarios concurrentes haciendo
las peticiones más frecuentes: registro, login y envío de mensajes al chat.

## Cómo lanzarlos

Necesitas **dos terminales**:

```bash
# Terminal 1 — arranca el backend
uvicorn src.api.main:app --host 0.0.0.0 --port 8000

# Terminal 2 — lanza Locust contra el backend
locust -f tests/stress/locustfile.py --host http://localhost:8000
```

Abre <http://localhost:8089> y configura:

- **Number of users**: empieza con 10 e ir subiendo.
- **Spawn rate**: 2 usuarios/segundo es razonable.

### Modo headless (sin UI)

Para CI o pruebas rápidas:

```bash
locust -f tests/stress/locustfile.py --host http://localhost:8000 \
       --users 10 --spawn-rate 2 --run-time 30s --headless
```

Imprime un resumen al final con p50/p95/p99 de latencia y request/s.

## Qué mide

Dos perfiles de usuario virtual:

| Perfil | Peso | Tareas |
|---|---|---|
| `ChatUser` | 3 | Manda mensajes de small-talk al chat + lista sesiones |
| `AnalyticsUser` | 1 | Pide análisis financiero (consultas más caras al LLM) |

Cada usuario se registra al arrancar (`on_start`) con un email único
generado con UUID, así que se puede lanzar el mismo locustfile varias
veces sin que choquen los registros.

## Limitaciones conocidas

- **Biometría falsa**: los bytes de la foto son los mínimos para que la
  API no rechace por archivo vacío. El pipeline biométrico real lo
  rechazaría, pero los registros en BD sí se crean para el resto de tasks.
- **LLM real**: cada `POST /chat` consume tokens de Groq. No lanzar
  cargas grandes (>50 usuarios durante mucho rato) sin estar pendiente
  del rate limit del proveedor.
- **BD compartida**: si no aíslas la BD entre runs, los emails se
  acumulan. Para entornos limpios usa SQLite en `:memory:` o vacía la
  tabla `users` antes.
