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
#include "ui.h"
#include "vistas.h"
#include "trig.h"
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

static hud_state_t  s_state = ST_IDLE;
// int, no hud_screen_t: el indice ya supera el enum cuando hay vistas
// dinamicas. El enum solo nombra las pantallas fijas.
static int          s_scr   = SCR_NUCLEO;
static int s_t = 0;                 // contador de fotogramas
static int s_trans = 0;             // fotogramas restantes de transicion
static int s_sel = 0;               // control seleccionado en AJUSTES
static int  s_pulsado = -1;         // boton bajo el dedo ahora mismo
static bool s_hablando = false;
static int64_t s_t_down = 0;

// ---- Botones fijos de navegacion (48 px: tamano de pulgar) ----
#define BTN_PREV 100
#define BTN_NEXT 101
#define BTN_ACC  102        // accion principal, cambia segun pantalla

// Se dibujan discretos, pero ui_dentro() amplia el area de toque:
// pequeno a la vista, comodo para el pulgar.
static const boton_t B_PREV = { .x = 8,   .y = 96, .w = 26, .h = 48,
                                .txt = "<", .color = C_GREY, .escala_txt = 1 };
static const boton_t B_NEXT = { .x = 206, .y = 96, .w = 26, .h = 48,
                                .txt = ">", .color = C_GREY, .escala_txt = 1 };

// Cuantos caracteres caben en la fila 'y' empezando en 'x0' sin salirse
// del cristal. La pantalla es redonda: a y=62 el borde util cae en x~225,
// asi que 33 caracteres desde x=38 se pierden por fuera.
static int ancho_seguro(int y, int x0)
{
    int dy = y + 3 - CY;
    int semi2 = 118 * 118 - dy * dy;
    if (semi2 <= 0) return 0;
    int semi = 0;
    while ((semi + 1) * (semi + 1) <= semi2) semi++;   // sqrt entera
    int n = ((CX + semi) - x0) / 6;
    return n < 0 ? 0 : n;
}

// Pinta recortando al circulo en vez de dejar que el texto se salga
static void texto_recortado(int x, int y, const char *s, uint16_t c)
{
    char buf[40];
    int n = ancho_seguro(y, x);
    if (n <= 0) return;
    if (n > (int)sizeof(buf) - 1) n = sizeof(buf) - 1;
    snprintf(buf, n + 1, "%s", s);
    display_text(x, y, buf, c, 1);
}

static const char *NOMBRES[SCR_TOTAL] = {
    "NUCLEO","CRONO","ATMOS","VOZ","REGISTRO",
    "SENALES","MAQUINA","FORJA","AJUSTES","DIAG"
};

void hud_init(void) { s_scr = SCR_NUCLEO; s_state = ST_IDLE; }
void hud_set_state(hud_state_t s) { s_state = s; }
hud_state_t hud_get_state(void) { return s_state; }
hud_screen_t hud_screen(void) { return (hud_screen_t)s_scr; }
bool hud_en_ajustes(void) { return s_scr == SCR_AJUSTES; }
// Pantallas fijas + las que el servidor haya declarado. Esto es lo que
// permite anadir funciones sin reflashear: una vista nueva es una entrada
// mas del carrusel, no un case nuevo en el switch.
static int hud_total(void) { return SCR_TOTAL + vistas_num(); }
void hud_next_screen(void) { int n = hud_total(); s_scr = (s_scr + 1) % n; s_trans = 8; }
static void hud_prev_screen(void) { int n = hud_total(); s_scr = (s_scr + n - 1) % n; s_trans = 8; }
bool hud_hablando(void) { return s_hablando; }

// El boton grande del centro-abajo: hablar, o ajustar el control activo
static boton_t boton_accion(void)
{
    uint16_t ac = ajustes_acento();
    if (s_scr == SCR_AJUSTES)
        return (boton_t){ .x = 96, .y = 182, .w = 48, .h = 26,
                          .txt = "MAS", .color = ac, .escala_txt = 1 };
    return (boton_t){ .x = 100, .y = 176, .w = 40, .h = 40, .redondo = true,
                      .txt = s_hablando ? ".." : "VOZ",
                      .color = s_hablando ? C_LIME : ac, .escala_txt = 1 };
}
void hud_ajuste_siguiente(void) { s_sel = (s_sel + 1) % 5; }

void hud_ajuste_incrementa(void)
{
    ajustes_t *a = ajustes();
    switch (s_sel) {
        case 0: a->brillo  = (a->brillo  >= 100) ? 20 : a->brillo  + 20; break;
        case 1: a->volumen = (a->volumen >= 100) ? 0  : a->volumen + 20; break;
        case 2: a->tema    = (a->tema + 1) % 4;                          break;
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
        // Barrido radial
        int a = f * 16;                       // ~0.28 rad por fotograma
        display_line(CX, CY, CX + trig_mul_cos(115, a), CY + trig_mul_sin(115, a),
                     display_escala(ac, 150));

        // La marca aparece letra a letra
        if (f > 14) {
            int n = (f - 14) / 2;
            if (n > (int)strlen(marca)) n = (int)strlen(marca);
            // snprintf en vez de strncpy: con n == strlen(marca) strncpy copia
            // los 11 bytes sin NUL y GCC 14 no puede probar que el buffer ya
            // estaba a cero, asi que -Werror=stringop-truncation tumba el build.
            char buf[16];
            snprintf(buf, (size_t)n + 1, "%s", marca);
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

static void marco(void)
{
    uint16_t ac = ajustes_acento();
    display_clear(C_VOID);

    // Un aro fino y un arco de actividad corto. Nada mas.
    display_arc(CX, CY, 117, 1, 0, 359, display_escala(ac, 60));
    int a0 = s_t % 360;
    display_arc(CX, CY, 117, 2, a0, a0 + 40, ac);

    const char *titulo = "";
    if (s_scr < SCR_TOTAL) titulo = NOMBRES[s_scr];
    else { vista_t *v = vistas_get(s_scr - SCR_TOTAL); if (v) titulo = v->titulo; }
    display_text_center(CX, 18, titulo, display_escala(ac, 230), 1);

    // Indicadores de enlace, pequenos y bien separados
    display_fill_circle(CX - 30, 34, 2, net_connected()   ? C_LIME : C_BLOOD);
    display_fill_circle(CX,      34, 2, audio_ready()     ? C_LIME : C_BLOOD);
    display_fill_circle(CX + 30, 34, 2, voice_connected() ? C_LIME : C_BLOOD);

    // Paso adaptativo: con vistas dinamicas el numero de pantallas crece y
    // un paso fijo de 10 px se saldria del cristal a partir de 12 pantallas.
    int n = hud_total();
    int paso = (n > 1) ? 110 / (n - 1) : 0;
    if (paso > 10) paso = 10;
    int x0 = CX - paso * (n - 1) / 2;
    for (int i = 0; i < n; i++) {
        int x = x0 + i * paso;
        if (i == s_scr) display_fill_circle(x, 224, 2, ac);
        else display_px(x, 224, display_escala(ac, 130));
    }
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
        int a = i * 45 / 2;                   // 22.5 grados por barra
        int alto = 5 + (lvl * 5) / 30;
        if (alto > 18) alto = 18;
        for (int k = 0; k < alto; k++) {
            int r = 84 + k;
            display_px(CX + trig_mul_cos(r, a), CY + trig_mul_sin(r, a), col);
        }
    }
    display_text_center(CX, 104, txt, col, 1);

    const char *msg = voice_text();
    if (msg && msg[0]) display_text_center(CX, 126, msg, C_WHITE, 1);
    else display_text_center(CX, 126, voice_connected() ? "MANTEN PARA HABLAR" : "SIN SERVIDOR",
                             voice_connected() ? C_GREY : C_BLOOD, 1);
}

static void lista(int n, const char *(*get)(int), uint16_t col, const char *vacio)
{
    if (n == 0) { display_text_center(CX, 112, vacio, C_GREY, 1); return; }
    int y = 62;
    for (int i = 0; i < n && i < 6; i++, y += 20) {
        display_fill_circle(28, y + 3, 2, col);
        texto_recortado(38, y, get(i), C_WHITE);
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
        display_text(26, y, mio ? ">" : "<", mio ? C_CYAN : C_LIME, 1);
        texto_recortado(38, y, voice_hist(i), mio ? C_WHITE : C_LIME);
    }
}

static void p_noticias(void) { lista(voice_news_num(), voice_news, C_AMBER,
                                     voice_connected() ? "CARGANDO" : "SIN SERVIDOR"); }
static void p_mac(void)      { lista(voice_mac_num(), voice_mac, C_CYAN,
                                     voice_connected() ? "MIDIENDO" : "SIN SERVIDOR"); }
static void p_creativo(void) { lista(voice_creativo_num(), voice_creativo, C_MAGENTA,
                                     voice_connected() ? "CONSULTANDO" : "SIN SERVIDOR"); }

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
    static const char *TEMAS[] = {"CIAN","MAGENTA","LIMA","AMBAR"};

    barra(74,  "BRILLO",  a->brillo,  ac, s_sel == 0);
    barra(102, "VOLUMEN", a->volumen, ac, s_sel == 1);

    int bx = CX - 52;
    display_text(bx, 122, "TEMA", s_sel == 2 ? C_WHITE : C_GREY, 1);
    display_text(bx + 74, 122, TEMAS[a->tema], ac, 1);

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
    display_text(52, y, "IP", C_GREY, 1);  display_text(86, y, net_ip(), ac, 1); y += 20;
    snprintf(t, sizeof(t), "%d DBM", net_rssi());
    display_text(52, y, "RF", C_GREY, 1);
    display_text(86, y, net_connected() ? t : "NO", net_connected() ? C_LIME : C_BLOOD, 1); y += 20;
    snprintf(t, sizeof(t), "%d KB", (int)(esp_get_free_heap_size() / 1024));
    display_text(52, y, "RAM", C_GREY, 1); display_text(86, y, t, C_AMBER, 1); y += 20;
    int s = (int)(esp_timer_get_time() / 1000000);
    snprintf(t, sizeof(t), "%02d:%02d:%02d", s / 3600, (s / 60) % 60, s % 60);
    display_text(52, y, "ON", C_GREY, 1);  display_text(86, y, t, C_MAGENTA, 1); y += 24;
    display_text_center(CX, y, audio_ready() ? "AUDIO OK" : "AUDIO --",
                        audio_ready() ? C_LIME : C_BLOOD, 1);
}

// Pinta una vista declarada por el servidor. El firmware no sabe que
// significan las filas: solo las coloca. Ahi esta la ganancia.
static void p_vista(vista_t *v)
{
    if (!v) { display_text_center(CX, 112, "VISTA VACIA", C_GREY, 1); return; }
    if (v->n_filas == 0) { display_text_center(CX, 112, "SIN DATOS", C_GREY, 1); return; }
    int y = 62;
    for (int i = 0; i < v->n_filas; i++, y += 20) {
        display_fill_circle(28, y + 3, 2, v->acento);
        texto_recortado(38, y, v->filas[i].txt, v->filas[i].color);
        if (v->filas[i].badge[0]) {
            int bx = CX + 62;
            display_fill_circle(bx, y + 3, 6, display_escala(v->acento, 90));
            display_text_center(bx, y, v->filas[i].badge, C_WHITE, 1);
        }
    }
}

// Pregunta a pantalla completa. Se come la navegacion a proposito: si el
// agente esta esperando una decision, no debe poder ignorarse por accidente.
static boton_t preg_boton(int i)
{
    int n = preg_num_opciones();
    if (n < 1) n = 1;
    int hueco = 8, total = 190;
    int w = (total - (n - 1) * hueco) / n;
    return (boton_t){ .x = CX - total / 2 + i * (w + hueco), .y = 150,
                      .w = w, .h = 48,
                      .txt = preg_opcion(i),
                      .color = (i == 0) ? C_LIME : C_BLOOD, .escala_txt = 1 };
}

static void p_pregunta(void)
{
    display_clear(C_VOID);
    uint16_t ac = ajustes_acento();
    display_arc(CX, CY, 117, 2, 0, 359, ac);

    display_text_center(CX, 30, "CONFIRMAR", display_escala(C_AMBER, 240), 1);

    // El texto se parte por palabras: cortar a medias se lee fatal
    const char *t = preg_texto();
    char linea[28]; int li = 0, y = 66;
    for (const char *p = t; ; p++) {
        if (*p && *p != ' ' && li < (int)sizeof(linea) - 1) { linea[li++] = *p; continue; }
        if (li > 0 && y < 140) {
            linea[li] = 0;
            display_text_center(CX, y, linea, C_WHITE, 1);
            y += 16; li = 0;
        }
        if (!*p) break;
    }

    char seg[12];
    snprintf(seg, sizeof(seg), "%ds", preg_segundos());
    display_text_center(CX, 132, seg, display_escala(C_GREY, 200), 1);

    for (int i = 0; i < preg_num_opciones(); i++) {
        boton_t b = preg_boton(i);
        ui_boton(&b, s_pulsado == 200 + i);
    }
    ui_ripple_dibuja();
    display_flush();
}

// Banda superior de aviso. No roba la navegacion: solo informa.
static void banda_noti(void)
{
    if (!noti_activa()) return;
    uint16_t c = noti_color();
    display_rect(0, 44, 240, 18, display_escala(c, 40));
    display_rect(0, 44, 240, 1, c);
    display_rect(0, 61, 240, 1, c);
    display_text_center(CX, 48, noti_texto(), C_WHITE, 1);
}

// ============================================================
//  Tactil
// ============================================================
void hud_touch_down(int x, int y)
{
    s_t_down = esp_timer_get_time() / 1000;

    if (preg_activa()) {                       // una decision pendiente manda
        for (int i = 0; i < preg_num_opciones(); i++) {
            boton_t b = preg_boton(i);
            if (ui_dentro(&b, x, y)) {
                s_pulsado = 200 + i;
                ui_ripple_lanza(x, y, b.color);
                return;
            }
        }
        s_pulsado = -1;
        return;
    }
    boton_t acc = boton_accion();
    uint16_t ac = ajustes_acento();

    if (ui_dentro(&B_PREV, x, y))      { s_pulsado = BTN_PREV; ui_ripple_lanza(x, y, ac); }
    else if (ui_dentro(&B_NEXT, x, y)) { s_pulsado = BTN_NEXT; ui_ripple_lanza(x, y, ac); }
    else if (ui_dentro(&acc, x, y))    { s_pulsado = BTN_ACC;  ui_ripple_lanza(x, y, ac); }
    else {
        s_pulsado = -1;
        // Toque directo sobre una fila de AJUSTES: la selecciona
        if (s_scr == SCR_AJUSTES) {
            // Puntos medios entre las filas reales (62/90/122/142/160).
            // Las bandas anteriores (112/145/168/192) quedaron obsoletas
            // al compactar el layout y seleccionaban la fila anterior.
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
    // Mantener el boton de voz activa el microfono
    if (s_pulsado == BTN_ACC && s_scr != SCR_AJUSTES && !s_hablando) {
        int64_t t = esp_timer_get_time() / 1000;
        if (t - s_t_down > 250) {
            s_hablando = true;
            voice_talk_start();
        }
    }
}

void hud_touch_up(int x, int y)
{
    if (preg_activa()) {
        if (s_pulsado >= 200) {
            boton_t b = preg_boton(s_pulsado - 200);
            if (ui_dentro(&b, x, y)) preg_responde(s_pulsado - 200);
        }
        s_pulsado = -1;
        return;
    }
    if (s_hablando) {
        voice_talk_stop();
        s_hablando = false;
        s_pulsado = -1;
        return;
    }
    // Soltar fuera del boton cancela: el gesto estandar es poder arrastrar
    // el dedo afuera para arrepentirse. Antes se ejecutaba igual.
    if (s_pulsado >= 0) {
        boton_t acc = boton_accion();
        const boton_t *b = (s_pulsado == BTN_PREV) ? &B_PREV
                         : (s_pulsado == BTN_NEXT) ? &B_NEXT : &acc;
        if (!ui_dentro(b, x, y)) { s_pulsado = -1; return; }
    }
    switch (s_pulsado) {
        case BTN_PREV: hud_prev_screen(); break;
        case BTN_NEXT: hud_next_screen(); break;
        case BTN_ACC:
            if (s_scr == SCR_AJUSTES) {
                // Toque corto mueve la seleccion, mantenido incrementa.
                // hud_ajuste_siguiente() estaba exportada y muerta.
                if (esp_timer_get_time() / 1000 - s_t_down < 400) hud_ajuste_siguiente();
                else hud_ajuste_incrementa();
            }
            else { s_scr = SCR_VOZ; s_trans = 8; }   // atajo, ahora con transicion
            break;
    }
    s_pulsado = -1;
}

void hud_render(void)
{
    vistas_purga();
    preg_tick();

    // Una pregunta pendiente toma la pantalla entera y sale por su cuenta
    if (preg_activa()) { p_pregunta(); s_t++; return; }

    if (s_scr >= hud_total()) s_scr = 0;      // una vista caduco bajo los pies

    marco();
    if (s_scr >= SCR_TOTAL) { p_vista(vistas_get(s_scr - SCR_TOTAL)); goto encima; }
    switch (s_scr) {
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

encima:
    banda_noti();
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
    display_flush();
    s_t++;
}
