# Prompt Engineering con ASPECCT

Este documento describe la metodología que sigue el sistema PFINAL_AP-IA
para diseñar los prompts de sus agentes y dónde se aplica en el código.
Cumple el punto 9 del enunciado: *"Prompt engineering con ASPECCT o TAREA:
los prompts de interacción con el usuario deben diseñarse siguiendo alguna
metodología estructurada"*.

## Qué es ASPECCT

ASPECCT es un acrónimo que estructura un prompt en 7 secciones explícitas:

| Letra | Sección | Qué describe |
|---|---|---|
| **A** | **Audiencia** | Quién recibe la salida del LLM. Otro componente del sistema, un usuario humano, etc. Determina el nivel de detalle. |
| **S** | **Style / Persona** | El "papel" que adopta el LLM y el registro lingüístico. Define tono, longitud, formalidad. |
| **P** | **Propósito** | El objetivo específico de esa llamada. Una sola tarea por prompt. |
| **E** | **Especificidad** | Datos concretos: lista de operaciones permitidas, formato de la respuesta, tipos de campos. |
| **C** | **Contexto** | Información de fondo que el LLM necesita pero que no es la tarea en sí (estado actual, datos del usuario, etc.). |
| **C** | **Constraints** | Reglas duras: "NUNCA hagas X", "responde en español", "máximo 4 frases". |
| **T** | **Tono** | Calibración final: profesional, neutral, cercano, telegráfico... |

La idea es que cada sección sea **explícita y marcada** en el prompt, en
vez de mezclarlas en prosa libre. Esto facilita el mantenimiento (saber
dónde tocar para cambiar el tono sin romper la lógica) y deja claro qué
intención de diseño hay detrás de cada bloque.

## Dónde se aplica en el código

### 1. Router (`_ROUTER_SYSTEM_PROMPT_TEMPLATE`)

**Archivo**: [`src/agents/orchestrator/prompts.py`](../src/agents/orchestrator/prompts.py)

Es el prompt más grande y más estructurado. Cabeceras `[A] [S] [P] [E] [C] [C] [T]`
explícitas. Su `[E] Especificidad` lista exhaustivamente:
- Las 6 acciones disponibles (`delegate_analyst`, `delegate_registrar`,
  `delegate_security`, `delegate_conversational`, `ask_user`, `respond_final`).
- Las 12 operaciones de `delegate_analyst` con su firma.
- Las 5 operaciones de `delegate_registrar`.
- Reglas de visualización dinámica (`chart_type`).

**Audiencia**: el propio grafo LangGraph (no es texto que vea el usuario).
**Tono**: telegráfico, sin saludos, decidido.

### 2. Narrator (`NARRATOR_SYSTEM_PROMPT`)

**Archivo**: [`src/agents/orchestrator/prompts.py`](../src/agents/orchestrator/prompts.py)

Convierte el `AnalysisReport` / `RegistryResult` / `SecurityVerdict` en texto
para el usuario. El `[A] Audiencia` y `[S] Style` se inyectan vía
`build_role_style_block(role)` para adaptar el tono al perfil del usuario
(`basic` vs `advanced`).

**Constraints clave**:
- Usar EXCLUSIVAMENTE las cifras del bloque `DATOS DISPONIBLES`.
- NO mencionar nombres internos de operaciones ni labels de acción.
- NO devolver JSON: solo texto natural.

### 3. Conversational (`CONVERSATIONAL_SYSTEM_PROMPT`)

**Archivo**: [`src/agents/orchestrator/prompts.py`](../src/agents/orchestrator/prompts.py)

Agente de small-talk diferenciado del orquestador técnico. Cubre saludos,
preguntas sobre el sistema, charla off-topic. También recibe el bloque de
estilo por rol (`build_role_style_block`).

**Propósito**: responder a charla "blanda" sin invocar P1-P5.
**Constraint clave**: si el usuario pide algo financiero, NO inventes
cifras; explica qué tipo de pregunta puede hacer.

### 4. Summary rolling (`_SUMMARY_PROMPT`)

**Archivo**: [`src/api/routers/chat.py`](../src/api/routers/chat.py)

Cada 8 turnos persistidos, el chat regenera un resumen rolling de la
conversación para que el orquestador pueda retomarla sin pasar todos los
mensajes históricos al contexto del LLM (token economy).

**Audiencia**: el propio orquestador en futuros turnos.
**Style**: telegráfico, tercera persona, máximo 5 frases.
**Constraint**: NO inventar datos; si una info no aparece en el texto, ignorar.

### 5. Bloques de estilo por rol (`_ROLE_STYLE_BLOCKS`)

**Archivo**: [`src/agents/orchestrator/prompts.py`](../src/agents/orchestrator/prompts.py)

Dos perfiles de usuario configurables en `/settings`:

- **basic**: lenguaje cotidiano, máx 2 frases, sin tecnicismos, cifras redondeadas.
- **advanced**: 3-4 frases, cifras con decimales, porcentajes, términos
  financieros (tasa de ahorro, percentil, varianza...).

Estos bloques implementan la sección `[A] Audiencia` y `[S] Style` de los
prompts NARRATOR y CONVERSATIONAL — se inyectan como SystemMessage
independiente. Esto permite cambiar el tono sin tocar el prompt principal.

## Por qué ASPECCT y no TAREA

[TAREA](https://promptengineering.org/) (Tarea, Audiencia, Recursos,
Estructura, Activación) es la alternativa sugerida por el enunciado.
Optamos por ASPECCT porque:

1. Separa explícitamente `[S] Style` y `[T] Tono`, lo que nos permite
   inyectar el estilo por rol como un SystemMessage independiente sin
   tocar el prompt principal.
2. La sección `[E] Especificidad` encaja perfectamente con catálogos
   cerrados de operaciones — clave en un router con structured output.
3. La doble `C` (Contexto + Constraints) refleja la realidad: el contexto
   conversacional cambia turn-a-turn, pero las constraints duras son
   inmutables.

## Cómo medir la calidad de los prompts

El sistema integra [Langfuse](https://cloud.langfuse.com) (ver Fase 6
del enunciado) para observar en cloud:
- El prompt completo que se manda al LLM en cada llamada.
- La respuesta cruda.
- Latencia y tokens consumidos.
- Metadata propia (`role`, `agent`, `session_id`, `user_id`).

Esto permite A/B testear cambios de prompt: modificas una sección
ASPECCT, mandas el mismo mensaje, comparas respuestas y métricas.

## Referencias

- [ASPECCT prompt framework](https://promptengineering.org/aspect-prompt-engineering-framework/)
- [LangChain Prompt Templates](https://python.langchain.com/docs/concepts/prompt_templates/)
- [Anthropic — Prompt Engineering Best Practices](https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/overview)
