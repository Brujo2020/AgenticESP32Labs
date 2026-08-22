// ============================================================
//  HUD circular — interfaz neon
//  Diez pantallas, animacion de arranque, transiciones y ajustes.
// ============================================================
#include "hud.h"
#include "display.h"
#include "audio.h"
#include "touch.h"
#include "net.h"
#include "voice.h"
#include "ajustes.h"
#include "bateria.h"
#include "version.h"
#include "ui.h"
#include <stdio.h>
#include <string.h>
#include <math.h>
#include <time.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_timer.h"
#include "esp_system.h"

#define CX 120
#define CY 120

// Push-to-talk: tocar el boton central para hablarle al asistente.
//
// Estuvo en 0 durante una demo, cuando la conversacion por voz no era fiable:
// el HUD seguia anunciando hora, clima y noticias (eso no depende de este
// boton) pero el toque no hacia nada. Se reactiva ahora que el camino
// completo funciona -- WiFi, puente, STT con el microfono ya sin
// interferencias, y TTS en espanol con Piper.
//
// Se deja como interruptor y no se borra: volver a apagarlo para una demo es
// cambiar un 1 por un 0.
#define VOZ_ENTRANTE_ACTIVA 1

static hud_state_t  s_state = ST_IDLE;
static hud_screen_t s_scr   = SCR_NUCLEO;
static int s_t = 0;                 // contador de fotogramas
static int s_trans = 0;             // fotogramas restantes de transicion
static int s_sel = 0;               // control seleccionado en AJUSTES
static int  s_pulsado = -1;         // boton bajo el dedo ahora mismo
static bool s_hablando = false;
static int64_t s_t_down = 0;
// Corte automatico por silencio: si nadie sabe que soltar el dedo detiene la
// escucha (o el toque de "soltar" no se registra bien en el tactil real),
// el microfono se quedaba grabando indefinidamente. Se corta solo tras unos
// segundos sin nivel de voz, y ademas el boton pasa a ser un cuadrado rojo
// de "STOP" bien reconocible mientras se escucha (ver boton_accion()).
static int64_t s_ultimo_sonido = 0;
#define SILENCIO_UMBRAL 4        // audio_mic_level() es 0..100
#define SILENCIO_MS     4000
// Anti-rebote del boton de voz: sin esto, un toque un poco largo o un dedo
// que tiembla puede registrarse como "toca, suelta, toca" en pocos ms y
// arrancar/parar la escucha varias veces seguidas (el famoso "se vuelve
// loco"). Tras CADA cambio de estado (arrancar o parar) se ignora el boton
// durante COOLDOWN_MS, tanto para volver a arrancar como para volver a
// parar -- asi un segundo toque pegado al primero no hace nada.
static int64_t s_cooldown_hasta = 0;
#define COOLDOWN_MS 500
// Ultima posicion valida del dedo. Hace falta porque al soltar, touch_get()
// devuelve false sin escribir las coordenadas, y main.c reenvia los ceros con
// que las inicializa. Es decir: hud_touch_up() SIEMPRE recibe (0,0) y esas
// coordenadas no significan nada. Para saber si se solto dentro o fuera del
// boton hay que mirar donde estaba el dedo la ultima vez que se supo.
static int s_ux = -1, s_uy = -1;

// ---- Botones fijos de navegacion (48 px: tamano de pulgar) ----
#define BTN_PREV 100
#define BTN_NEXT 101
#define BTN_ACC  102        // accion principal, cambia segun pantalla
#define BTN_PREG0 110       // opciones de una 'pregunta' (protocolo v2), hasta 3
#define BTN_PREG1 111
#define BTN_PREG2 112

// Se dibujan discretos, pero ui_dentro() amplia el area de toque:
// pequeno a la vista, comodo para el pulgar.
static const boton_t B_PREV = { .x = 6,   .y = 96, .w = 22, .h = 48,
                                .txt = "<", .color = C_GREY, .escala_txt = 1 };
static const boton_t B_NEXT = { .x = 212, .y = 96, .w = 22, .h = 48,
                                .txt = ">", .color = C_GREY, .escala_txt = 1 };

// ---- Margenes laterales del contenido ----
// Los botones de navegacion ocupan y 96..144, justo la banda donde caen las
// filas 3 y 4 de cualquier lista. El contenido empezaba en x=28, que esta
// DENTRO del boton izquierdo: la vineta se pintaba encima de el. Ahora todo
// lo que sea contenido vive entre estos dos limites, con holgura por ambos
// lados. ui_dentro() sigue ampliando el area de toque 14 px, asi que los
// botones se dibujan mas finos pero no se vuelven mas dificiles de acertar.
#define MARGEN_IZQ 40      // el boton < acaba en 28
#define MARGEN_DER 202     // el boton > empieza en 212
#define TXT_IZQ    (MARGEN_IZQ + 10)   // hueco para la vineta

// Cuantos caracteres caben en la fila 'y' desde 'x0' sin salirse del cristal
// ni invadir el margen derecho. La pantalla es redonda: cerca del borde
// superior e inferior cabe mucho menos que en el centro.
static int ancho_seguro(int y, int x0)
{
    int dy = y + 3 - CY;
    int semi2 = 118 * 118 - dy * dy;
    if (semi2 <= 0) return 0;
    int semi = 0;
    while ((semi + 1) * (semi + 1) <= semi2) semi++;      // sqrt entera
    int limite = CX + semi;
    if (limite > MARGEN_DER) limite = MARGEN_DER;          // respeta el margen
    int n = (limite - x0) / 6;
    return n < 0 ? 0 : n;
}

// Pinta recortando, en vez de dejar que el texto se salga o pise el boton.
//
// Si la linea NO cabe, se desplaza sola en bucle (marquesina) en vez de
// quedarse cortada para siempre: un titular de noticias o una respuesta larga
// del agente eran ilegibles a partir del caracter 26. El desplazamiento se
// calcula por fotograma global (s_t), asi que todas las filas van
// sincronizadas y el conjunto no parece un caos de textos moviendose.
//
// Ritmo: ~4 fotogramas por caracter (a 30 fps, unos 7 caracteres/segundo,
// que es velocidad de lectura comoda) y una pausa al principio y al final
// para poder leer el arranque sin perseguirlo.
#define MARQ_FRAMES_CAR  4
#define MARQ_PAUSA       18      // fotogramas quieto en cada extremo

// Avanza 'n' caracteres UTF-8 (no bytes) dentro de una cadena. En espanol una
// vocal acentuada ocupa dos bytes: cortar por bytes parte la letra por la
// mitad y deja un byte suelto que se pinta como '?'.
static const char *salta_chars(const char *s, int n)
{
    while (*s && n > 0) {
        s++;
        while ((*s & 0xC0) == 0x80) s++;    // saltar bytes de continuacion
        n--;
    }
    return s;
}

// Copia como mucho 'n' caracteres UTF-8 (completos) en el buffer.
static void copia_chars(char *dst, size_t cap, const char *src, int n)
{
    const char *fin = salta_chars(src, n);
    size_t bytes = (size_t)(fin - src);
    if (bytes > cap - 1) bytes = cap - 1;
    memcpy(dst, src, bytes);
    dst[bytes] = 0;
}

static void texto_recortado(int x, int y, const char *t, uint16_t c)
{
    char buf[96];                            // hasta 3 bytes por caracter
    int n = ancho_seguro(y, x);
    if (n <= 0 || !t) return;

    // Se mide en CARACTERES dibujables, no en bytes: con strlen() una linea
    // con tildes se creia mas larga de lo que ocupa y se recortaba de mas.
    int largo = display_text_largo(t);
    int sobra = largo - n;
    if (sobra <= 0) {                       // cabe entera: nada que animar
        display_text(x, y, t, c, 1);
        return;
    }

    // Ciclo: pausa + recorrido + pausa, y vuelta a empezar.
    int recorrido = sobra * MARQ_FRAMES_CAR;
    int ciclo = recorrido + MARQ_PAUSA * 2;
    int f = s_t % ciclo;
    int desde;
    if (f < MARQ_PAUSA)                     desde = 0;
    else if (f < MARQ_PAUSA + recorrido)    desde = (f - MARQ_PAUSA) / MARQ_FRAMES_CAR;
    else                                    desde = sobra;
    if (desde > sobra) desde = sobra;

    copia_chars(buf, sizeof(buf), salta_chars(t, desde), n);
    display_text(x, y, buf, c, 1);

    // Marca de continuacion mientras quede texto a la derecha: sin ella no
    // se distingue "esto sigue" de "esto acaba justo aqui".
    if (desde < sobra)
        display_px(x + n * 6 - 1, y + 3, display_escala(c, 160));
}

static const char *NOMBRES_FIJAS[SCR_VISTA0] = {
    "NUCLEO","CRONO","ATMOS","VOZ","REGISTRO",
    "SENALES","MAQUINA","FORJA","AJUSTES","DIAG"
};

// ============================================================
//  Protocolo v2 — vistas declarativas (Grupo 3)
// ============================================================
// Las pantallas fijas siempre existen. Los slots SCR_VISTA0.. solo existen
// mientras haya una vista activa en esa posicion del carrusel: sin servidor
// v2 (o sin vistas emitidas), pantalla_existe() los descarta todos y el HUD
// navega exactamente igual que antes de este protocolo.
static bool es_vista_slot(hud_screen_t s) { return s >= SCR_VISTA0 && s <= SCR_VISTA_FIN; }

static bool pantalla_existe(hud_screen_t s)
{
    // Las fijas ahora pueden apagarse desde el panel web (ajustes.mascara).
    // AJUSTES es la excepcion deliberada: si se pudiera ocultar, y ademas se
    // ocultara todo lo demas, el usuario se quedaria sin forma de recuperar
    // el HUD desde el propio dispositivo. Siempre queda una puerta.
    if (!es_vista_slot(s))
        return (s == SCR_AJUSTES) || ajustes_pantalla_visible((int)s);
    return (s - SCR_VISTA0) < voice_vistas_num();
}

// Orden de recorrido del carrusel. Las fijas se ordenan por ajustes.orden;
// los slots de vista v2 van siempre despues, en su orden de llegada, para
// que reordenar las fijas no mueva lo que un agente acaba de publicar.
static int pos_carrusel(hud_screen_t s)
{
    if (es_vista_slot(s)) return 100 + (int)(s - SCR_VISTA0);
    return ajustes_pantalla_pos((int)s);
}

// Vecina en el carrusel siguiendo el orden configurado. 'paso' es +1 o -1.
// Si no hay ninguna por delante, da la vuelta al extremo opuesto.
static hud_screen_t vecina(hud_screen_t desde, int paso)
{
    int mejor = -1, mejor_pos = 0;
    int pos_actual = pos_carrusel(desde);
    int extremo = -1, extremo_pos = 0;

    for (int s = 0; s < SCR_TOTAL; s++) {
        if (s == (int)desde) continue;
        if (!pantalla_existe((hud_screen_t)s)) continue;
        int p = pos_carrusel((hud_screen_t)s);

        if (extremo < 0 || (paso > 0 ? p < extremo_pos : p > extremo_pos)) {
            extremo = s; extremo_pos = p;
        }
        bool en_direccion = (paso > 0) ? (p > pos_actual) : (p < pos_actual);
        if (!en_direccion) continue;
        if (mejor < 0 || (paso > 0 ? p < mejor_pos : p > mejor_pos)) {
            mejor = s; mejor_pos = p;
        }
    }
    if (mejor  >= 0) return (hud_screen_t)mejor;
    if (extremo >= 0) return (hud_screen_t)extremo;
    return desde;    // era la unica visible
}

static const char *nombre_pantalla(hud_screen_t s)
{
    if (!es_vista_slot(s)) return NOMBRES_FIJAS[s];
    const vista_t *v = voice_vista(s - SCR_VISTA0);
    return v ? v->titulo : "VACIO";
}

static uint16_t color_por_nombre(const char *n)
{
    if (!strcmp(n, "cyan"))    return C_CYAN;
    if (!strcmp(n, "magenta")) return C_MAGENTA;
    if (!strcmp(n, "lime"))    return C_LIME;
    if (!strcmp(n, "amber"))   return C_AMBER;
    if (!strcmp(n, "ice"))     return C_ICE;
    if (!strcmp(n, "blood"))   return C_BLOOD;
    if (!strcmp(n, "grey"))    return C_GREY;
    return C_WHITE;    // "white" y cualquier valor desconocido: nunca un color al azar
}

void hud_init(void)
{
    // NUCLEO puede estar oculta desde el panel: arrancar en una pantalla que
    // no existe dejaria el HUD en negro hasta el primer toque.
    s_scr = pantalla_existe(SCR_NUCLEO) ? SCR_NUCLEO : vecina(SCR_NUCLEO, +1);
    s_state = ST_IDLE;
}
void hud_set_state(hud_state_t s) { s_state = s; }
hud_state_t hud_get_state(void) { return s_state; }
hud_screen_t hud_screen(void) { return s_scr; }
bool hud_en_ajustes(void) { return s_scr == SCR_AJUSTES; }

void hud_next_screen(void)
{
    s_scr = vecina(s_scr, +1);
    s_trans = 8;
    // Beep corto al navegar: feedback tactil/auditivo inmediato, dos tonos
    // distintos (mas agudo=siguiente, mas grave=anterior) para que se
    // note la direccion sin mirar la pantalla. Mismo patron que ya usa
    // voice_talk_stop() en voice.c, respeta el mismo ajuste de "efectos".
    if (ajustes()->efectos) audio_beep(1800, 40);
}
static void hud_prev_screen(void)
{
    s_scr = vecina(s_scr, -1);
    s_trans = 8;
    if (ajustes()->efectos) audio_beep(900, 40);
}
bool hud_hablando(void) { return s_hablando; }

// El boton grande del centro-abajo: hablar, o ajustar el control activo
static boton_t boton_accion(void)
{
    uint16_t ac = ajustes_acento();
    if (s_scr == SCR_AJUSTES)
        return (boton_t){ .x = 96, .y = 182, .w = 48, .h = 26,
                          .txt = "MAS", .color = ac, .escala_txt = 1 };
    // Mientras escucha, el boton cambia de circulo a CUADRADO ROJO: una
    // forma de "stop" reconocible al vuelo, en vez de un circulo casi igual
    // al de reposo con solo el texto distinto.
    if (s_hablando)
        return (boton_t){ .x = 100, .y = 176, .w = 40, .h = 40, .redondo = false,
                          .txt = "STOP", .color = C_BLOOD, .escala_txt = 1 };
    return (boton_t){ .x = 100, .y = 176, .w = 40, .h = 40, .redondo = true,
                      .txt = "VOZ", .color = ac, .escala_txt = 1 };
}
void hud_ajuste_siguiente(void) { s_sel = (s_sel + 1) % 5; }

void hud_ajuste_incrementa(void)
{
    ajustes_t *a = ajustes();
    switch (s_sel) {
        case 0: a->brillo  = (a->brillo  >= 100) ? 20 : a->brillo  + 20; break;
        case 1: a->volumen = (a->volumen >= 100) ? 0  : a->volumen + 20; break;
        case 2: a->tema    = (a->tema + 1) % 8;                          break;
        case 3: a->scanlines = !a->scanlines;                            break;
        case 4: a->rejilla   = !a->rejilla;                              break;
    }
    ajustes_aplicar();
    ajustes_guardar();
}

// ============================================================
//  Animacion de arranque
// ============================================================
void hud_boot_anim(void)
{
    const char *marca = "MARIO ESP32";
    for (int f = 0; f < 46; f++) {
        display_clear(C_VOID);
        uint16_t ac = ajustes_acento();

        // Anillos que se expanden
        for (int k = 0; k < 3; k++) {
            int r = (f * 5 + k * 26) % 130;
            uint8_t br = (uint8_t)(255 - r * 180 / 130);
            display_circle(CX, CY, r, display_escala(ac, br));
        }
        // Barrido radial. f*0.28f eran radianes arbitrarios; se redondea al
        // grado entero mas cercano para leer de la tabla en vez de llamar a
        // cosf/sinf (Grupo 2: fuera del render, ver display_sin_q/cos_q).
        int a = (int)(f * 0.28f * (180.0f / (float)M_PI) + 0.5f);
        display_line(CX, CY, CX + 115 * display_cos_q(a) / 4096,
                     CY + 115 * display_sin_q(a) / 4096,
                     display_escala(ac, 150));

        // La marca aparece letra a letra
        if (f > 14) {
            int n = (f - 14) / 2;
            if (n > (int)strlen(marca)) n = strlen(marca);
            char buf[16] = {0};
            strncpy(buf, marca, n);
            display_text_center(CX, 112, buf, C_WHITE, 2);
        }
        if (f > 32) display_text_center(CX, 140, "SISTEMA EN LINEA",
                                        display_escala(ac, 200), 1);
        display_flush();
        vTaskDelay(pdMS_TO_TICKS(28));
    }
}

// ============================================================
//  Marco comun
// ============================================================
static uint16_t color_estado(void)
{
    switch (s_state) {
        case ST_LISTENING:  return C_CYAN;
        case ST_PROCESSING: return C_AMBER;
        case ST_SPEAKING:   return C_LIME;
        case ST_ERROR:      return C_BLOOD;
        default:            return C_GREY;
    }
}

// Bateria SIEMPRE visible, en cualquier pantalla: pila pequena arriba a la
// derecha con el porcentaje. Va dentro del circulo util (r=118) para que no
// se coma el borde en la pantalla redonda.
//
// Color por carga, no decorativo: verde normal, ambar por debajo de 30,
// rojo por debajo de 15. Cargando pulsa en cian, que se distingue de un
// vompletamente cargado sin tener que leer el numero.
static void pinta_bateria(void)
{
    if (!bateria_disponible()) return;
    int pct = bateria_pct();
    if (pct < 0) return;
    // Acotar aqui no es paranoia: sin esto el compilador no puede probar que
    // el "%d" de abajo cabe en el buffer y -Werror=format-truncation tumba
    // la compilacion. Ademas deja el ancho de la barra siempre en rango.
    if (pct > 100) pct = 100;

    const int x = 158, y = 30, an = 22, al = 11;   // cuerpo de la pila
    bool cargando = bateria_cargando();

    uint16_t col = (pct <= 15) ? C_BLOOD : (pct <= 30) ? C_AMBER : C_LIME;
    if (cargando) {
        // Parpadeo lento mientras carga: se ve "vivo" sin ser molesto.
        col = display_escala(C_CYAN, (s_t % 60 < 30) ? 255 : 120);
    }

    display_rect(x, y, an, al, col);                          // carcasa
    for (int i = 0; i < 2; i++)                                // borne
        display_line(x + an + i, y + 3, x + an + i, y + al - 4, col);

    // Relleno proporcional. No hay primitiva de rectangulo macizo en
    // display.h, asi que se pinta con lineas horizontales: son 7 como mucho.
    int relleno = (an - 4) * pct / 100;
    for (int i = 0; i < relleno; i++)
        display_line(x + 2 + i, y + 2, x + 2 + i, y + al - 3, col);

    char t[8];
    snprintf(t, sizeof(t), "%d", pct);
    display_text(x - 6 - (int)strlen(t) * 6, y + 2, t, col, 1);
}

static void marco(void)
{
    uint16_t ac = ajustes_acento();
    // Una vista declarativa trae su propio 'acento' (protocolo v2): se
    // respeta en vez del acento global mientras se este viendo esa vista.
    if (es_vista_slot(s_scr)) {
        const vista_t *v = voice_vista(s_scr - SCR_VISTA0);
        if (v) ac = color_por_nombre(v->acento);
    }
    display_clear(C_VOID);

    // Un aro fino y un arco de actividad corto. Nada mas.
    display_arc(CX, CY, 117, 1, 0, 359, display_escala(ac, 60));
    int a0 = s_t % 360;
    display_arc(CX, CY, 117, 2, a0, a0 + 40, ac);

    display_text_center(CX, 18, nombre_pantalla(s_scr), display_escala(ac, 230), 1);

    // Indicadores de enlace, pequenos y bien separados
    display_fill_circle(CX - 30, 34, 2, net_connected()   ? C_LIME : C_BLOOD);
    display_fill_circle(CX,      34, 2, audio_ready()     ? C_LIME : C_BLOOD);
    display_fill_circle(CX + 30, 34, 2, voice_connected() ? C_LIME : C_BLOOD);

    // Puntos del carrusel: solo por las pantallas que existen de verdad (las
    // fijas, mas las vistas activas). Sin eso, 8 huecos vacios de protocolo
    // v2 desparramarian los puntos aunque no haya ninguna vista emitida.
    int total = 0, actual = 0;
    for (int s = 0; s < SCR_TOTAL; s++) {
        if (!pantalla_existe((hud_screen_t)s)) continue;
        if (s == s_scr) actual = total;
        total++;
    }
    for (int i = 0; i < total; i++) {
        int x = CX - (total * 10) / 2 + i * 10 + 5;
        if (i == actual) display_fill_circle(x, 224, 2, ac);
        else display_px(x, 224, display_escala(ac, 130));
    }

    // Ultimo, para que quede por encima del aro y no lo tape nada del marco.
    pinta_bateria();
}

// ============================================================
//  Pantallas
// ============================================================
static void p_nucleo(void)
{
    uint16_t ac = ajustes_acento();
    int lvl = audio_mic_level();
    int r = 30 + lvl / 6;

    display_arc(CX, CY - 10, r, 1, 0, 359, display_escala(ac, 200));
    display_text_center(CX, CY - 24, "MARIO", C_WHITE, 2);
    display_text_center(CX, CY - 4, "ESP32", display_escala(ac, 220), 1);

    display_text_center(CX, 176, net_connected() ? "EN LINEA" : "SIN RED",
                        net_connected() ? C_LIME : C_BLOOD, 1);
}

static void p_reloj(void)
{
    char txt[32];
    uint16_t ac = ajustes_acento();
    time_t now; time(&now);
    struct tm tm; localtime_r(&now, &tm);

    if (!net_time_valid()) {
        display_text_center(CX, 112, "SIN HORA", C_GREY, 1);
        return;
    }
    display_arc(CX, CY, 104, 2, -90, -90 + tm.tm_sec * 6, ac);

    snprintf(txt, sizeof(txt), "%02d:%02d", tm.tm_hour, tm.tm_min);
    display_text_center(CX, 96, txt, C_WHITE, 4);

    snprintf(txt, sizeof(txt), "%02d", tm.tm_sec);
    display_text_center(CX, 132, txt, display_escala(C_MAGENTA, 220), 1);

    static const char *dias[] = {"DOM","LUN","MAR","MIE","JUE","VIE","SAB"};
    snprintf(txt, sizeof(txt), "%s %02d/%02d", dias[tm.tm_wday % 7], tm.tm_mday, tm.tm_mon + 1);
    display_text_center(CX, 154, txt, display_escala(ac, 200), 1);
}

static void p_clima(void)
{
    clima_t c = net_weather();
    if (!c.valid) { display_text_center(CX, 112, "SIN DATOS", C_GREY, 1); return; }

    char t[24];
    int temp = (int)(c.temp_c + 0.5f);
    int pct = temp < 0 ? 0 : (temp > 40 ? 100 : temp * 100 / 40);
    display_arc(CX, CY, 104, 2, 150, 150 + pct * 24 / 10, C_AMBER);

    snprintf(t, sizeof(t), "%dC", temp);
    display_text_center(CX, 96, t, C_WHITE, 4);
    display_text_center(CX, 138, net_weather_desc(c.codigo), C_ICE, 1);
    snprintf(t, sizeof(t), "%d KMH", (int)(c.viento_kmh + 0.5f));
    display_text_center(CX, 158, t, display_escala(C_ICE, 190), 1);
}

static void p_voz(void)
{
    const char *txt;
    switch (voice_state()) {
        case VOZ_ESCUCHANDO: s_state = ST_LISTENING;  break;
        case VOZ_PENSANDO:   s_state = ST_PROCESSING; break;
        case VOZ_HABLANDO:   s_state = ST_SPEAKING;   break;
        case VOZ_ERROR:      s_state = ST_ERROR;      break;
        default:             s_state = ST_IDLE;       break;
    }
    switch (s_state) {
        case ST_LISTENING:  txt = "ESCUCHANDO"; break;
        case ST_PROCESSING: txt = "PROCESANDO"; break;
        case ST_SPEAKING:   txt = "RESPONDIENDO"; break;
        case ST_ERROR:      txt = "ERROR"; break;
        default:            txt = "EN ESPERA"; break;
    }
    uint16_t col = color_estado();
    int lvl = audio_mic_level();

    // Corona de barras radiales que reacciona a la voz
    // 16 barras finas: sugieren el nivel sin comerse la pantalla
    for (int i = 0; i < 16; i++) {
        // 22.5 grados por barra: fraccion de grado, redondeada al entero mas
        // cercano para caer en la tabla (1 px de desviacion medida, aceptable
        // para una barra de nivel).
        int a = (int)(i * 22.5f + 0.5f);
        int alto = 5 + (lvl * 5) / 30;
        if (alto > 18) alto = 18;
        int32_t cs = display_cos_q(a), sn = display_sin_q(a);
        for (int k = 0; k < alto; k++) {
            int r = 84 + k;
            display_px(CX + r * cs / 4096, CY + r * sn / 4096, col);
        }
    }
    display_text_center(CX, 104, txt, col, 1);

    const char *msg = voice_text();
    if (msg && msg[0]) display_text_center(CX, 126, msg, C_WHITE, 1);
    else display_text_center(CX, 126, voice_connected() ? "TOCA PARA HABLAR" : "SIN SERVIDOR",
                             voice_connected() ? C_GREY : C_BLOOD, 1);
}

static void lista(int n, const char *(*get)(int), uint16_t col, const char *vacio)
{
    if (n == 0) { display_text_center(CX, 112, vacio, C_GREY, 1); return; }
    int y = 62;
    for (int i = 0; i < n && i < 6; i++, y += 20) {
        display_fill_circle(MARGEN_IZQ, y + 3, 2, col);
        texto_recortado(TXT_IZQ, y, get(i), C_WHITE);
    }
}

static void p_chat(void)
{
    int n = voice_hist_num();
    if (n == 0) { display_text_center(CX, 112, "SIN CHARLA", C_GREY, 1); return; }
    int y = 62;
    int desde = (n > 6) ? n - 6 : 0;          // solo lo mas reciente
    for (int i = desde; i < n; i++, y += 20) {
        bool mio = voice_hist_es_mio(i);
        display_text(MARGEN_IZQ - 4, y, mio ? ">" : "<", mio ? C_CYAN : C_LIME, 1);
        texto_recortado(TXT_IZQ, y, voice_hist(i), mio ? C_WHITE : C_LIME);
    }
}

static void p_noticias(void) { lista(voice_news_num(), voice_news, C_AMBER,
                                     voice_connected() ? "CARGANDO" : "SIN SERVIDOR"); }
static void p_mac(void)      { lista(voice_mac_num(), voice_mac, C_CYAN,
                                     voice_connected() ? "MIDIENDO" : "SIN SERVIDOR"); }
static void p_creativo(void) { lista(voice_creativo_num(), voice_creativo, C_MAGENTA,
                                     voice_connected() ? "CONSULTANDO" : "SIN SERVIDOR"); }

// Vista declarativa (protocolo v2): cada fila trae su propio color y un
// badge opcional, a diferencia de 'lista()' que pinta todo con un color fijo.
static void p_vista(int idx)
{
    const vista_t *v = voice_vista(idx);
    if (!v || v->n_filas == 0) {
        display_text_center(CX, 112, "SIN DATOS", C_GREY, 1);
        return;
    }
    int y = 62;
    for (int i = 0; i < v->n_filas; i++, y += 20) {
        uint16_t col = color_por_nombre(v->filas[i].color);
        display_fill_circle(MARGEN_IZQ, y + 3, 2, col);
        texto_recortado(TXT_IZQ, y, v->filas[i].txt, col);
        if (v->filas[i].con_badge)
            display_text(MARGEN_DER - 18, y, v->filas[i].badge, col, 1);
    }
}

// ============================================================
//  Pregunta bloqueante (protocolo v2): pantalla completa, sin marco ni
//  navegacion. Es el canal de aprobacion fisica: mientras este activa, el
//  HUD no hace otra cosa.
// ============================================================
static boton_t boton_pregunta(int i, int n)
{
    int w = 60, h = 44, gap = 8;
    int total_w = n * w + (n - 1) * gap;
    int x0 = CX - total_w / 2;
    boton_t b = { .x = x0 + i * (w + gap), .y = 150, .w = w, .h = h,
                  .txt = voice_pregunta_opcion(i), .color = C_AMBER, .escala_txt = 1 };
    return b;
}

static void p_pregunta(void)
{
    display_clear(C_VOID);
    display_arc(CX, CY, 117, 2, 0, 359, display_escala(C_AMBER, 140));
    display_text_center(CX, 30, "APROBACION", C_AMBER, 1);

    // El texto puede ocupar mas de una linea corta: se parte en 2 por
    // sencillez, en vez de meter un word-wrap completo para esto.
    char buf[64];
    snprintf(buf, sizeof(buf), "%s", voice_pregunta_txt());
    size_t n = strlen(buf);
    if (n > 22) {
        char l1[24], l2[24];
        snprintf(l1, sizeof(l1), "%.22s", buf);
        // Precision explicita tambien aqui: "buf+22" puede tener hasta 41
        // caracteres restantes (buf mide 64) y l2 solo 24 — sin el ".*"
        // GCC lo marca como -Werror=format-truncation, igual que en voice.c.
        snprintf(l2, sizeof(l2), "%.*s", (int)sizeof(l2) - 1, buf + 22);
        display_text_center(CX, 70, l1, C_WHITE, 1);
        display_text_center(CX, 88, l2, C_WHITE, 1);
    } else {
        display_text_center(CX, 80, buf, C_WHITE, 1);
    }

    char t[16];
    snprintf(t, sizeof(t), "%ds", voice_pregunta_segundos_restantes());
    display_text_center(CX, 110, t, C_GREY, 1);

    int n_op = voice_pregunta_num_opciones();
    for (int i = 0; i < n_op; i++) {
        boton_t b = boton_pregunta(i, n_op);
        ui_boton(&b, s_pulsado == BTN_PREG0 + i);
    }
}

static void barra(int y, const char *etiqueta, int pct, uint16_t col, bool sel)
{
    int bw = 104, bx = CX - bw / 2;
    display_text(bx, y - 12, etiqueta, sel ? C_WHITE : display_escala(col, 160), 1);
    char t[8]; snprintf(t, sizeof(t), "%d", pct);
    display_text(bx + bw - 18, y - 12, t, sel ? C_WHITE : C_GREY, 1);

    display_rect(bx, y, bw, 5, 0x2124);
    display_rect(bx, y, bw * pct / 100, 5, col);
    if (sel) {
        display_rect(bx - 3, y - 2, 1, 9, C_WHITE);
        display_rect(bx + bw + 2, y - 2, 1, 9, C_WHITE);
    }
}

static void conmutador(int y, const char *etiqueta, bool on, bool sel)
{
    int bx = CX - 52;
    display_text(bx, y, etiqueta, sel ? C_WHITE : C_GREY, 1);
    uint16_t c = on ? C_LIME : C_GREY;
    display_rect(bx + 74, y + 1, 20, 7, display_escala(c, 90));
    display_fill_circle(bx + (on ? 89 : 79), y + 4, 3, c);
}

static void p_ajustes(void)
{
    ajustes_t *a = ajustes();
    uint16_t ac = ajustes_acento();
    // Ocho nombres, uno por tema. Antes solo habia cuatro mientras
    // ajustes_acento() ya manejaba ocho: con tema >= 4 esto leia fuera del
    // array y pintaba basura (o reiniciaba la placa).
    static const char *TEMAS[] = {"CIAN","MAGENTA","LIMA","AMBAR",
                                  "HIELO","SANGRE","GRIS","BLANCO"};
    int tema = a->tema;
    if (tema < 0 || tema >= (int)(sizeof(TEMAS)/sizeof(TEMAS[0]))) tema = 0;

    // Version y momento de compilacion: responde de un vistazo "¿lo que hay
    // flasheado es lo que acabo de compilar?", que mirando la placa no habia
    // forma de saber.
    char ver[40];
    snprintf(ver, sizeof(ver), "V%s  %s %s", FW_VERSION, FW_FECHA, FW_HORA);
    display_text_center(CX, 50, ver, display_escala(C_GREY, 200), 1);

    barra(74,  "BRILLO",  a->brillo,  ac, s_sel == 0);
    barra(102, "VOLUMEN", a->volumen, ac, s_sel == 1);

    int bx = CX - 52;
    display_text(bx, 122, "TEMA", s_sel == 2 ? C_WHITE : C_GREY, 1);
    display_text(bx + 74, 122, TEMAS[tema], ac, 1);

    conmutador(142, "LINEAS",  a->scanlines, s_sel == 3);
    conmutador(160, "REJILLA", a->rejilla,   s_sel == 4);

    // Coinciden con la y real de cada etiqueta, no 4 px por debajo
    int marcas[5] = {62, 90, 122, 142, 160};
    display_text(bx - 12, marcas[s_sel], ">", C_WHITE, 1);
}

static void p_sistema(void)
{
    char t[32];
    uint16_t ac = ajustes_acento();
    int y = 66;
    display_text(MARGEN_IZQ, y, "IP", C_GREY, 1);
    texto_recortado(MARGEN_IZQ + 34, y, net_ip(), ac); y += 20;
    snprintf(t, sizeof(t), "%d DBM", net_rssi());
    display_text(MARGEN_IZQ, y, "RF", C_GREY, 1);
    display_text(MARGEN_IZQ + 34, y, net_connected() ? t : "NO",
                 net_connected() ? C_LIME : C_BLOOD, 1); y += 20;
    snprintf(t, sizeof(t), "%d KB", (int)(esp_get_free_heap_size() / 1024));
    display_text(MARGEN_IZQ, y, "RAM", C_GREY, 1);
    display_text(MARGEN_IZQ + 34, y, t, C_AMBER, 1); y += 20;
    int s = (int)(esp_timer_get_time() / 1000000);
    snprintf(t, sizeof(t), "%02d:%02d:%02d", s / 3600, (s / 60) % 60, s % 60);
    display_text(MARGEN_IZQ, y, "ON", C_GREY, 1);
    display_text(MARGEN_IZQ + 34, y, t, C_MAGENTA, 1); y += 24;
    display_text_center(CX, y, audio_ready() ? "AUDIO OK" : "AUDIO --",
                        audio_ready() ? C_LIME : C_BLOOD, 1);
}

// ============================================================
//  Tactil
// ============================================================
void hud_touch_down(int x, int y)
{
    s_t_down = esp_timer_get_time() / 1000;
    s_ux = x; s_uy = y;

    // La pregunta bloqueante toma toda la pantalla: mientras este activa, ni
    // los botones de navegacion ni el de accion existen para el tacto.
    if (voice_pregunta_activa()) {
        int n_op = voice_pregunta_num_opciones();
        s_pulsado = -1;
        for (int i = 0; i < n_op; i++) {
            boton_t b = boton_pregunta(i, n_op);
            if (ui_dentro(&b, x, y)) { s_pulsado = BTN_PREG0 + i; ui_ripple_lanza(x, y, C_AMBER); break; }
        }
        return;
    }

    boton_t acc = boton_accion();
    uint16_t ac = ajustes_acento();

    if (ui_dentro(&B_PREV, x, y))      { s_pulsado = BTN_PREV; ui_ripple_lanza(x, y, ac); }
    else if (ui_dentro(&B_NEXT, x, y)) { s_pulsado = BTN_NEXT; ui_ripple_lanza(x, y, ac); }
    else if (ui_dentro(&acc, x, y))    { s_pulsado = BTN_ACC;  ui_ripple_lanza(x, y, ac); }
    else {
        s_pulsado = -1;
        // La onda se lanza en cualquier toque, no solo sobre un boton. Sirve
        // de retroalimentacion siempre, y hace visible DONDE cree el sistema
        // que has tocado: si aparece en el lado contrario al dedo, los ejes
        // del tactil estan mal, que es justo lo que pasaba.
        ui_ripple_lanza(x, y, display_escala(ac, 120));
        // Toque directo sobre una fila de AJUSTES: la selecciona
        if (s_scr == SCR_AJUSTES) {
            // Puntos medios entre las filas reales (62/90/122/142/160).
            // Las bandas anteriores (112/145/168/192) quedaron obsoletas al
            // compactar el layout: de TEMA hacia abajo seleccionaban la fila
            // anterior, y REJILLA era directamente inalcanzable.
            if      (y < 84)  s_sel = 0;
            else if (y < 113) s_sel = 1;
            else if (y < 133) s_sel = 2;
            else if (y < 152) s_sel = 3;
            else              s_sel = 4;
            ui_ripple_lanza(x, y, ac);
        }
    }
}

void hud_touch_hold(int x, int y)
{
    s_ux = x; s_uy = y;
    // Ya no hace falta mantener presionado para empezar a hablar (ver
    // hud_touch_up): un solo toque basta. Antes esto exigia un gesto de
    // "mantener 250ms" ademas del toque normal para navegar, y en el
    // tactil real el evento de "hold" no siempre llega de forma fiable
    // -- resultado: a veces no arrancaba la escucha por mas que se
    // mantuviera el dedo. Se deja la funcion (main.c la sigue llamando)
    // pero sin logica propia: solo actualiza la ultima posicion del dedo.
}

void hud_touch_up(int x, int y)
{
    // Las coordenadas al soltar no son fiables, ver s_ux/s_uy
    (void)x; (void)y;
    if (voice_pregunta_activa()) {
        if (s_pulsado >= BTN_PREG0 && s_pulsado <= BTN_PREG2 && s_ux >= 0) {
            int i = s_pulsado - BTN_PREG0;
            boton_t b = boton_pregunta(i, voice_pregunta_num_opciones());
            if (ui_dentro(&b, s_ux, s_uy)) voice_pregunta_responde(i);
        }
        s_pulsado = -1;
        return;
    }
    if (s_hablando) {
        int64_t ahora = esp_timer_get_time() / 1000;
        if (ahora < s_cooldown_hasta) { s_pulsado = -1; return; }   // toque pegado, se ignora
        voice_talk_stop();
        s_hablando = false;
        s_pulsado = -1;
        s_cooldown_hasta = ahora + COOLDOWN_MS;   // margen antes de poder volver a arrancar
        return;
    }
    // Soltar fuera del boton cancela: el gesto estandar es poder arrastrar el
    // dedo afuera para arrepentirse. Se comprueba contra la ULTIMA posicion
    // conocida, no contra los argumentos: al soltar llegan siempre (0,0).
    if (s_pulsado >= 0 && s_ux >= 0) {
        boton_t acc = boton_accion();
        const boton_t *b = (s_pulsado == BTN_PREV) ? &B_PREV
                         : (s_pulsado == BTN_NEXT) ? &B_NEXT : &acc;
        if (!ui_dentro(b, s_ux, s_uy)) { s_pulsado = -1; return; }
    }
    switch (s_pulsado) {
        case BTN_PREV: hud_prev_screen(); break;
        case BTN_NEXT: hud_next_screen(); break;
        case BTN_ACC:
            if (s_scr == SCR_AJUSTES) {
                // Toque corto mueve la seleccion, mantenido incrementa.
                // hud_ajuste_siguiente() estaba exportada y nunca se llamaba.
                if (esp_timer_get_time() / 1000 - s_t_down < 400) hud_ajuste_siguiente();
                else hud_ajuste_incrementa();
            }
            else if (VOZ_ENTRANTE_ACTIVA) {
                // Un solo toque arranca a escuchar (antes hacia falta
                // mantener presionado 250ms, ver hud_touch_hold). Al
                // arrancar, salta a la pantalla VOZ para ver el estado.
                int64_t ahora = esp_timer_get_time() / 1000;
                if (ahora < s_cooldown_hasta) break;   // muy pegado a un stop reciente, se ignora
                s_hablando = true;
                s_ultimo_sonido = ahora;
                voice_talk_start();
                s_scr = SCR_VOZ;
                s_trans = 8;
                s_cooldown_hasta = ahora + COOLDOWN_MS;   // bloquea un segundo toque inmediato
            }
            break;
    }
    s_pulsado = -1;
}

// Banda superior de 'notifica': interrumpe 4 s sobre lo que se este viendo,
// sin tocar la navegacion ni el resto del contenido (ver PROTOCOLO.md).
static void banda_notifica(void)
{
    if (!voice_notifica_activa()) return;
    const char *niv = voice_notifica_nivel();
    uint16_t col = !strcmp(niv, "ok")    ? C_LIME
                 : !strcmp(niv, "warn")  ? C_AMBER
                 : !strcmp(niv, "error") ? C_BLOOD
                 : C_CYAN;                              // "info" y por defecto
    display_rect(0, 0, 240, 22, display_escala(col, 60));
    display_rect(0, 20, 240, 2, col);
    display_text_center(CX, 8, voice_notifica_txt(), C_WHITE, 1);
}

void hud_render(void)
{
    // La pregunta bloqueante sustituye TODA la pantalla: nada de marco, nada
    // de navegacion. Se comprueba antes que cualquier otra cosa porque es
    // literalmente el canal de aprobacion fisica del agente.
    if (voice_pregunta_activa()) {
        p_pregunta();
        ui_ripple_dibuja();
        display_flush();
        s_t++;
        return;
    }

    // Corte automatico por silencio: sin esto, si el toque de "soltar" no se
    // registra en el tactil real o simplemente no queda claro que hay que
    // soltar el dedo, el microfono se queda escuchando para siempre. Tras
    // SILENCIO_MS sin nivel de voz, se corta solo -- igual que si se hubiera
    // soltado el boton STOP a mano.
    if (s_hablando) {
        int64_t ahora = esp_timer_get_time() / 1000;
        if (audio_mic_level() > SILENCIO_UMBRAL) s_ultimo_sonido = ahora;
        if (ahora - s_ultimo_sonido > SILENCIO_MS) {
            voice_talk_stop();
            s_hablando = false;
            s_pulsado = -1;
            s_cooldown_hasta = ahora + COOLDOWN_MS;   // mismo margen que un stop manual
        }
    }

    marco();
    if (es_vista_slot(s_scr)) {
        p_vista(s_scr - SCR_VISTA0);
    } else switch (s_scr) {
        case SCR_NUCLEO:   p_nucleo();   break;
        case SCR_RELOJ:    p_reloj();    break;
        case SCR_CLIMA:    p_clima();    break;
        case SCR_VOZ:      p_voz();      break;
        case SCR_CHAT:     p_chat();     break;
        case SCR_NOTICIAS: p_noticias(); break;
        case SCR_MAC:      p_mac();      break;
        case SCR_CREATIVO: p_creativo(); break;
        case SCR_AJUSTES:  p_ajustes();  break;
        default:           p_sistema();  break;
    }

    // Botones siempre encima del contenido
    ui_boton(&B_PREV, s_pulsado == BTN_PREV);
    ui_boton(&B_NEXT, s_pulsado == BTN_NEXT);
    boton_t acc = boton_accion();
    ui_boton(&acc, s_pulsado == BTN_ACC || s_hablando);
    ui_ripple_dibuja();

    // Transicion al cambiar de pantalla: destello que se apaga
    if (s_trans > 0) {
        display_fade((uint8_t)(255 - s_trans * 22));
        s_trans--;
    }
    if (ajustes()->scanlines) display_scanlines(14);
    if (ajustes()->rejilla) display_vineta();   // la vineta pasa a ser opcional
    banda_notifica();     // por encima de todo, incluida la vineta
    display_flush();
    s_t++;
}
