# Configuración de Notificaciones de Telegram

Esta guía explica cómo configurar las notificaciones de Telegram para recibir alertas de seguridad y financieras en tiempo real.

## 🚀 Pasos Rápidos

### 1. Crear un Bot de Telegram (Administrador del Sistema)

Si eres el administrador del sistema P6_AP-IA, necesitas crear un bot primero:

1. **Buscar @BotFather en Telegram**
2. **Enviar `/newbot`**
3. **Seguir las instrucciones:**
   - Nombre del bot: `P6 Security Bot`
   - Username del bot: `P6SecurityBot` (o el que prefieras)
4. **Copiar el token** que te proporciona BotFather
5. **Configurar el token en el archivo `.env`:**
   ```bash
   TELEGRAM_BOT_TOKEN=tu_token_aqui
   ```

### 2. Obtener tu Chat ID (Usuario Final)

Cada usuario necesita obtener su Chat ID único:

1. **Buscar tu bot en Telegram** (ej: `@P6SecurityBot`)
2. **Enviar `/start`** al bot
3. **El bot responderá con tu Chat ID** (solo números)
4. **Copiar ese ID** para configurarlo en la aplicación

### 3. Configurar en la Aplicación

#### Durante el Registro:
- ✅ Activar "Notificaciones de seguridad"
- ✅ Pegar el Chat ID en el campo correspondiente
- ✅ Elegir el nivel de privacidad (recomendado: "Modo seguro")

#### Después del Registro:
1. **Ir a la página de Configuración** (`/settings`)
2. **Activar las notificaciones**
3. **Introducir el Chat ID de Telegram**
4. **Guardar la configuración**
5. **Probar las notificaciones** con el botón "Probar"

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

1. **Verificar el token del bot**: Asegurarse de que `TELEGRAM_BOT_TOKEN` esté configurado correctamente
2. **Verificar el Chat ID**: Debe ser solo números, sin `@` ni otros caracteres
3. **Probar manualmente**: Usar el botón "Probar" en la configuración
4. **Revisar los logs**: Revisar `logs/app.log` para ver errores

### "El bot no responde"

1. **Verificar que el bot esté activo**: El token debe ser válido
2. **Revisar la configuración CORS**: El frontend debe poder acceder al backend
3. **Comprobar la conexión**: Asegurarse de que el backend esté corriendo

### "Chat ID incorrecto"

1. **Enviar `/start` de nuevo** al bot para obtener el ID correcto
2. **No usar el username** (@usuario), usar solo el ID numérico
3. **Verificar que no haya espacios** ni caracteres extra

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
- **Control del usuario**: Se pueden desactivar en cualquier momento
- **Niveles de privacidad**: Se puede elegir qué información compartir
- **Datos cifrados**: Los datos sensibles se almacenan cifrados

### Mejores Prácticas
- **Usar el modo seguro** para la mayoría de usuarios
- **Guardar el Chat ID** como información personal
- **Revisar las notificaciones** periódicamente
- **Desactivar si no son necesarias**

## 🆘 Soporte

Si hay problemas con las notificaciones:

1. **Revisar esta guía** para soluciones comunes
2. **Consultar los logs** del sistema para errores específicos
3. **Contactar al administrador** si el problema persiste
4. **Usar el chat de la aplicación** para reportar problemas

---

**Nota**: Las notificaciones de Telegram son opcionales. Puedes usar el sistema P6_AP-IA perfectamente sin activarlas.
