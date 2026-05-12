# Configuración de Notificaciones de Telegram

Esta guía explica cómo configurar las notificaciones de Telegram para recibir alertas de seguridad y financieras en tiempo real.

## 🚀 Pasos Rápidos

### 1. Crear un Bot de Telegram (Administrador del Sistema)

Si eres el administrador del sistema P6_AP-IA, necesitas crear un bot primero:

1. **Busca @BotFather en Telegram**
2. **Envía `/newbot`**
3. **Sigue las instrucciones:**
   - Nombre del bot: `P6 Security Bot`
   - Username del bot: `P6SecurityBot` (o el que prefieras)
4. **Copia el token** que te proporciona BotFather
5. **Configura el token en el archivo `.env`:**
   ```bash
   TELEGRAM_BOT_TOKEN=tu_token_aqui
   ```

### 2. Obtener tu Chat ID (Usuario Final)

Cada usuario necesita obtener su Chat ID único:

1. **Busca tu bot en Telegram** (ej: `@P6SecurityBot`)
2. **Envía `/start`** al bot
3. **El bot te responderá con tu Chat ID** (solo números)
4. **Copia ese ID** para configurarlo en la aplicación

### 3. Configurar en la Aplicación

#### Durante el Registro:
- ✅ Activa "Notificaciones de seguridad"
- ✅ Pega tu Chat ID en el campo correspondiente
- ✅ Elige el nivel de privacidad (recomendado: "Modo seguro")

#### Después del Registro:
1. **Ve a la página de Configuración** (`/settings`)
2. **Activa las notificaciones**
3. **Introduce tu Chat ID de Telegram**
4. **Guarda la configuración**
5. **Prueba las notificaciones** con el botón "Probar"

## 🔧 Configuración Avanzada

### Niveles de Privacidad

#### Modo Seguro (Redacted) - Recomendado 🛡️
- **Eventos**: "Login exitoso", "Anomalía detectada"
- **Categorías**: "Shopping", "Food", "Utilities"
- **Sin**: Importes exactos, descripciones detalladas

#### Modo Completo (Full) - Opcional 📊
- **Todos los datos**: Importes exactos, descripciones, fechas
- **Ideal para**: Usuarios que quieren máximo detalle
- **Precaución**: Más información sensible en las notificaciones

### Tipos de Notificaciones

#### 🚨 Alertas de Seguridad
- **Login exitoso**: Cuando inicias sesión correctamente
- **Login fallido**: Intentos de acceso no autorizados
- **Nuevo registro**: Cuando creas una nueva cuenta

#### 💰 Alertas Financieras
- **Anomalías detectadas**: Transacciones inusuales o sospechosas
- **Objetivos**: Cuando alcanzas el 80% de un límite de gasto
- **Resúmenes**: (Opcional) Resúmenes periódicos de tu actividad

## 🛠️ Solución de Problemas

### "No recibo notificaciones"

1. **Verifica el token del bot**: Asegúrate de que `TELEGRAM_BOT_TOKEN` esté configurado correctamente
2. **Verifica tu Chat ID**: Debe ser solo números, sin `@` ni otros caracteres
3. **Prueba manualmente**: Usa el botón "Probar" en la configuración
4. **Revisa los logs**: Revisa `logs/app.log` para ver errores

### "El bot no responde"

1. **Verifica que el bot esté activo**: El token debe ser válido
2. **Revisa la configuración CORS**: El frontend debe poder acceder al backend
3. **Comprueba la conexión**: Asegúrate de que el backend esté corriendo

### "Chat ID incorrecto"

1. **Envía `/start` de nuevo** al bot para obtener tu ID correcto
2. **No uses tu username** (@usuario), usa solo el ID numérico
3. **Verifica que no haya espacios** ni caracteres extra

## 📋 Ejemplo de Configuración

### Archivo `.env` (Administrador)
```bash
# Configuración del bot
TELEGRAM_BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrsTUVwxyz

# Otras configuraciones
DATABASE_URL=sqlite:///./data/p6.db
MASTER_FERNET_KEY=tu_clave_fernet
GROQ_API_KEY=tu_api_key_groq
```

### Configuración de Usuario (Frontend)
- **Notificaciones activadas**: ✅
- **Chat ID**: `123456789`
- **Nivel de privacidad**: `redacted`

## 🔒 Consideraciones de Seguridad

### RGPD y Privacidad
- **Opt-in explícito**: Las notificaciones están desactivadas por defecto
- **Control del usuario**: Puedes desactivarlas en cualquier momento
- **Niveles de privacidad**: Elige qué información compartir
- **Datos cifrados**: Los datos sensibles se almacenan cifrados

### Mejores Prácticas
- **Usa el modo seguro** para la mayoría de usuarios
- **Guarda tu Chat ID** como información personal
- **Revisa las notificaciones** periódicamente
- **Desactiva si no las necesitas**

## 🆘 Soporte

Si tienes problemas con las notificaciones:

1. **Revisa esta guía** para soluciones comunes
2. **Consulta los logs** del sistema para errores específicos
3. **Contacta al administrador** si el problema persiste
4. **Usa el chat de la aplicación** para reportar problemas

---

**Nota**: Las notificaciones de Telegram son opcionales. Puedes usar el sistema P6_AP-IA perfectamente sin activarlas.
