# Kit de marca — Maroru's Assistance (aplicado a Logiassist)

Fuente de verdad: `DESIGN.md` y `src/styles/global.css` del repo de la web (Marorus-Assistance-Web). Este documento resume cómo se aplica ese sistema a la interfaz de Logiassist ([app-service/static](../app-service/static)): misma paleta, mismas fuentes, mismas reglas de forma y menos tamaño de display que la web.

## Identidad

Papel cálido y carbón, una sola tinta granate, serif fina para nombres y títulos, grotesca ligera para todo lo que se lee u opera, reglas finas de 1 px en lugar de tarjetas. **Plano, cuadrado, sobrio.**

## Colores

| Token | Hex | Uso |
|---|---|---|
| `--paper` | `#f6f3ee` | Fondo principal |
| `--bone` | `#ebe5dc` | Panel lateral, mensajes del usuario |
| `--charcoal` | `#2f2b28` | Barra superior |
| `--ink` | `#1f1c1a` | Texto principal |
| `--ink-muted` | `#5f5852` | Texto secundario, metadatos, etiquetas |
| `--on-dark` / `--on-dark-muted` | `#efe9e1` / `#b8ada1` | Texto sobre carbón |
| `--maroon` | `#b80000` | Único acento: acción principal, foco, selección, bloqueos y errores |
| `--maroon-deep` | `#8a0000` | Hover del botón granate |
| `--maroon-on-dark` | `#e8a9a0` | Granate legible sobre carbón |
| `--rule` / `--rule-dark` | `#d6cec3` / `#4a443f` | Líneas finas |

- Una sola tinta: nada de azul de enlaces ni verde de "ok".
- Estados sin colores nuevos: evidencia suficiente en `ink`, débil en `ink-muted` cursiva, sin evidencia o bloqueo en granate.

## Tipografía

- **Instrument Serif** (400): nombre del producto, título del estado vacío, cabeceras de bloque ("Fuentes", "Sistemas"). `line-height` 0.9–1, `letter-spacing: -0.02em`.
- **Hanken Grotesk** (300–600, base 350): mensajes, botones, inputs, etiquetas.
- **Monoespaciada**: solo para identificadores reales; la interfaz del chat no muestra ninguno.
- Sin etiquetas en mayúsculas encima de los títulos. Ancho de lectura máximo 68ch. Cifras con `tabular-nums`.

## Forma

- Radio 0 en todo. Sin sombras, blur, glass ni degradados: la separación es un cambio de fondo o una línea de 1 px.
- Listas = filas entre líneas finas, no tarjetas.
- Márgenes laterales 20 px en móvil y 48 px en escritorio; espaciado en múltiplos de 4 px.
- Transiciones de color de 150–200 ms; lo único que se mueve es el streaming. Se respeta `prefers-reduced-motion`.

## Cómo se aplica en el chat

- Barra superior carbón de 56 px: "Logiassist" en serif y "por Maroru's Assistance" enlazando a https://marorusassistance.com.
- Aviso de demo en una fila fina: datos ficticios y modelos con datos en la UE.
- Estado vacío: título serif grande y preguntas de ejemplo como filas clicables.
- Usuario: bloque `--bone` a la derecha. Asistente: texto directo sobre papel. Etiquetas "Tú" / "Logiassist" en minúscula normal y una línea entre turnos.
- Bajo cada respuesta: nivel de evidencia en texto, bloque "Fuentes" con filas numeradas (las 4 primeras visibles, el resto desplegable) y "¿Te ha servido? Útil · No útil".
- Respuesta bloqueada: texto granate con línea izquierda de 1 px.
- Streaming: cursor fino granate al final del texto.
- Input: textarea con borde inferior de 1 px (granate con foco) y botón "Enviar" granate de 48 px. Enter envía, Shift+Enter salta de línea.

## Qué no hacer

Ni tarjetas con sombra, ni esquinas redondeadas, ni burbujas; ni degradados ni neón; ni Inter/Roboto/Space Grotesk; ni un segundo acento; ni eyebrows en mayúsculas o numeración decorativa; ni emojis como iconos; ni logos de empresas cliente.
