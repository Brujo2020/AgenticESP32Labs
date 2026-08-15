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
static hud_screen_t s_scr   = SCR_NUCLEO;
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

static const boton_t B_PREV = { .x = 6,   .y = 88, .w = 40, .h = 64,
                                .txt = "<", .color = C_GREY, .escala_txt = 3 };
static const boton_t B_NEXT = { .x = 194, .y = 88, .w = 40, .h = 64,
                                .txt = ">", .color = C_GREY, .escala_txt = 3 };

static const char *NOMBRES[SCR_TOTAL] = {
    "NUCLEO","CRONO","ATMOS","VOZ","REGISTRO",
    "SENALES","MAQUINA","FORJA","AJUSTES","DIAG"
};

void hud_init(void) { s_scr = SCR_NUCLEO; s_state = ST_IDLE; }
void hud_set_state(hud_state_t s) { s_state = s; }
hud_state_t hud_get_state(void) { return s_state; }
hud_screen_t hud_screen(void) { return s_scr; }
bool hud_en_ajustes(void) { return s_scr == SCR_AJUSTES; }
void hud_next_screen(void) { s_scr = (s_scr + 1) % SCR_TOTAL; s_trans = 8; }
static void hud_prev_screen(void) { s_scr = (s_scr + SCR_TOTAL - 1) % SCR_TOTAL; s_trans = 8; }
bool hud_hablando(void) { return s_hablando; }

// El boton grande del centro-abajo: hablar, o ajustar el control activo
static boton_t boton_accion(void)
{
    uint16_t ac = ajustes_acento();
    if (s_scr == SCR_AJUSTES)
        return (boton_t){ .x = 84, .y = 168, .w = 72, .h = 48,
                          .txt = "MAS", .color = ac, .escala_txt = 2 };
    return (boton_t){ .x = 78, .y = 160, .w = 84, .h = 56, .redondo = true,
                      .txt = s_hablando ? "..." : "VOZ",
                      .color = s_hablando ? C_LIME : ac, .escala_txt = 2 };
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
        float a = f * 0.28f;
        display_line(CX, CY, CX + (int)(115 * cosf(a)), CY + (int)(115 * sinf(a)),
                     display_escala(ac, 150));

        // La marca aparece letra a letra
        if (f > 14) {
            int n = (f - 14) / 2;
            if (n > (int)strlen(marca)) n = strlen(marca);
            char buf[16] = {0};
            strncpy(buf, marca, n);
            display_text_glow(CX, 112, buf, C_WHITE, 2);
        }
        if (f > 32) display_text_center(CX, 140, "SISTEMA EN LINEA",
                                        display_escala(ac, 200), 1);
        display_scanlines(28);
        display_vineta();
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
    ajustes_t *aj = ajustes();
    uint16_t ac = ajustes_acento();
    display_clear(C_VOID);

    // Un unico aro grueso. Antes habia rejilla, satelites y tres arcos
    // superpuestos: en 240 px de diametro eso solo ensucia.
    display_arc(CX, CY, 118, 3, 0, 359, display_escala(ac, 70));

    // Arco de actividad: una sola pasada, gruesa y brillante
    int a0 = s_t % 360;
    display_arc_glow(CX, CY, 118, 4, a0, a0 + 55, ac);

    // Cabecera grande
    display_text_glow(CX, 22, NOMBRES[s_scr], ac, 2);

    // Tres indicadores de enlace, mas grandes y separados
    display_fill_circle(CX - 34, 46, 4, net_connected()   ? C_LIME : C_BLOOD);
    display_fill_circle(CX,      46, 4, audio_ready()     ? C_LIME : C_BLOOD);
    display_fill_circle(CX + 34, 46, 4, voice_connected() ? C_LIME : C_BLOOD);

    // Paginacion
    for (int i = 0; i < SCR_TOTAL; i++) {
        int x = CX - 45 + i * 10;
        if (i == s_scr) display_fill_circle(x, 222, 3, ac);
        else display_fill_circle(x, 222, 1, display_escala(ac, 110));
    }
    (void)aj;
}

// ============================================================
//  Pantallas
// ============================================================
static void p_nucleo(void)
{
    uint16_t ac = ajustes_acento();
    int lvl = audio_mic_level();
    float pulso = 0.5f + 0.5f * sinf(s_t * 0.08f);
    int r = 40 + (int)(8 * pulso) + lvl / 4;

    display_fill_circle(CX, CY - 6, r, display_escala(ac, 45));
    display_arc(CX, CY - 6, r, 3, 0, 359, ac);
    display_text_glow(CX, CY - 18, "MARIO", C_WHITE, 3);
    display_text_center(CX, CY + 6, "ESP32", display_escala(ac, 230), 2);

    display_text_center(CX, 186, net_connected() ? "EN LINEA" : "SIN RED",
                        net_connected() ? C_LIME : C_BLOOD, 2);
}

static void p_reloj(void)
{
    char txt[32];
    uint16_t ac = ajustes_acento();
    time_t now; time(&now);
    struct tm tm; localtime_r(&now, &tm);

    if (!net_time_valid()) {
        display_text_center(CX, 110, "SIN HORA", C_GREY, 2);
        return;
    }
    // Arco de segundos, grueso
    display_arc_grad(CX, CY, 100, 5, -90, -90 + tm.tm_sec * 6, ac, C_MAGENTA);

    snprintf(txt, sizeof(txt), "%02d:%02d", tm.tm_hour, tm.tm_min);
    display_text_glow(CX, 92, txt, C_WHITE, 6);

    snprintf(txt, sizeof(txt), "%02d", tm.tm_sec);
    display_text_center(CX, 146, txt, C_MAGENTA, 3);

    static const char *dias[] = {"DOM","LUN","MAR","MIE","JUE","VIE","SAB"};
    snprintf(txt, sizeof(txt), "%s %02d/%02d", dias[tm.tm_wday % 7], tm.tm_mday, tm.tm_mon + 1);
    display_text_center(CX, 184, txt, ac, 2);
}

static void p_clima(void)
{
    clima_t c = net_weather();
    if (!c.valid) { display_text_center(CX, 110, "SIN DATOS", C_GREY, 2); return; }

    char t[24];
    int temp = (int)(c.temp_c + 0.5f);
    int pct = temp < 0 ? 0 : (temp > 40 ? 100 : temp * 100 / 40);
    display_arc_grad(CX, CY, 100, 5, 150, 150 + pct * 24 / 10, C_CYAN, C_BLOOD);

    snprintf(t, sizeof(t), "%d", temp);
    display_text_glow(CX - 10, 88, t, C_WHITE, 6);
    display_text(CX + 44, 96, "C", C_AMBER, 3);

    display_text_center(CX, 150, net_weather_desc(c.codigo), C_ICE, 2);
    snprintf(t, sizeof(t), "%d KMH", (int)(c.viento_kmh + 0.5f));
    display_text_center(CX, 182, t, display_escala(C_ICE, 200), 2);
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
    // 24 barras gruesas en lugar de 48 finas: se leen mucho mejor
    for (int i = 0; i < 24; i++) {
        float a = i * 15.0f * (float)M_PI / 180.0f;
        int alto = 8 + (lvl * (6 + (i % 4) * 4)) / 30;
        if (alto > 30) alto = 30;
        uint16_t cbar = display_mezcla(col, C_MAGENTA, (uint8_t)(alto * 255 / 30));
        for (int k = 0; k < alto; k++) {
            int r = 74 + k;
            for (int w = -2; w <= 2; w++) {
                float aw = a + w * 0.010f;
                display_px(CX + (int)(r * cosf(aw)), CY + (int)(r * sinf(aw)), cbar);
            }
        }
    }
    display_text_glow(CX, 104, txt, col, 2);

    const char *msg = voice_text();
    if (msg && msg[0]) display_text_center(CX, 142, msg, C_WHITE, 2);
    else display_text_center(CX, 142, voice_connected() ? "MANTEN" : "SIN RED",
                             voice_connected() ? C_GREY : C_BLOOD, 2);
}

static void lista(int n, const char *(*get)(int), uint16_t col, const char *vacio)
{
    if (n == 0) { display_text_center(CX, 110, vacio, C_GREY, 2); return; }
    // Cuatro lineas a doble tamano: mas legible que ocho diminutas
    int y = 74;
    for (int i = 0; i < n && i < 4; i++, y += 30) {
        display_fill_circle(24, y + 7, 3, col);
        char corto[18];
        snprintf(corto, sizeof(corto), "%s", get(i));
        display_text(36, y, corto, C_WHITE, 2);
    }
}

static void p_chat(void)
{
    int n = voice_hist_num();
    if (n == 0) { display_text_center(CX, 110, "SIN CHARLA", C_GREY, 2); return; }
    int y = 74;
    int desde = (n > 4) ? n - 4 : 0;          // solo lo mas reciente
    for (int i = desde; i < n; i++, y += 30) {
        bool mio = voice_hist_es_mio(i);
        char corto[17];
        snprintf(corto, sizeof(corto), "%s", voice_hist(i));
        display_text(24, y, mio ? ">" : "<", mio ? C_CYAN : C_LIME, 2);
        display_text(46, y, corto, mio ? C_WHITE : C_LIME, 2);
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
    int bw = 150, bx = CX - bw / 2;
    display_text(bx, y - 22, etiqueta, sel ? C_WHITE : display_escala(col, 170), 2);
    char t[8]; snprintf(t, sizeof(t), "%d", pct);
    display_text(bx + bw - 34, y - 22, t, sel ? C_WHITE : C_GREY, 2);

    display_rect(bx, y, bw, 10, 0x2124);
    for (int i = 0; i < bw * pct / 100; i++)
        display_rect(bx + i, y, 1, 10, display_mezcla(col, C_MAGENTA, (uint8_t)(i * 255 / bw)));
    if (sel) {
        display_rect(bx - 2, y - 3, 2, 16, C_WHITE);
        display_rect(bx + bw, y - 3, 2, 16, C_WHITE);
    }
}

static void conmutador(int y, const char *etiqueta, bool on, bool sel)
{
    int bx = CX - 75;
    display_text(bx, y, etiqueta, sel ? C_WHITE : C_GREY, 2);
    uint16_t c = on ? C_LIME : C_GREY;
    display_rect(bx + 108, y + 2, 32, 12, display_escala(c, 80));
    display_fill_circle(bx + (on ? 132 : 116), y + 8, 6, c);
}

static void p_ajustes(void)
{
    ajustes_t *a = ajustes();
    uint16_t ac = ajustes_acento();
    static const char *TEMAS[] = {"CIAN","MAGENTA","LIMA","AMBAR"};

    barra(92,  "BRILLO",  a->brillo,  ac, s_sel == 0);
    barra(130, "VOLUMEN", a->volumen, ac, s_sel == 1);

    int bx = CX - 75;
    display_text(bx, 150, "TEMA", s_sel == 2 ? C_WHITE : C_GREY, 2);
    display_text(bx + 74, 150, TEMAS[a->tema], ac, 2);

    conmutador(174, "LINEAS", a->scanlines, s_sel == 3);
    conmutador(196, "REJILLA", a->rejilla, s_sel == 4);

    if (s_sel >= 3) display_text(bx - 14, 174 + (s_sel - 3) * 22, ">", C_WHITE, 2);
    else if (s_sel == 2) display_text(bx - 14, 150, ">", C_WHITE, 2);
}

static void p_sistema(void)
{
    char t[32];
    uint16_t ac = ajustes_acento();
    int y = 74;
    display_text(26, y, "IP", C_GREY, 2);
    display_text(74, y, net_ip(), ac, 2); y += 28;
    snprintf(t, sizeof(t), "%d", net_rssi());
    display_text(26, y, "RF", C_GREY, 2);
    display_text(74, y, net_connected() ? t : "NO", net_connected() ? C_LIME : C_BLOOD, 2); y += 28;
    snprintf(t, sizeof(t), "%dK", (int)(esp_get_free_heap_size() / 1024));
    display_text(26, y, "RAM", C_GREY, 2);
    display_text(74, y, t, C_AMBER, 2); y += 28;
    int s = (int)(esp_timer_get_time() / 1000000);
    snprintf(t, sizeof(t), "%02d:%02d", s / 3600, (s / 60) % 60);
    display_text(26, y, "ON", C_GREY, 2);
    display_text(74, y, t, C_MAGENTA, 2);
}

// ============================================================
//  Tactil
// ============================================================
void hud_touch_down(int x, int y)
{
    s_t_down = esp_timer_get_time() / 1000;
    boton_t acc = boton_accion();
    uint16_t ac = ajustes_acento();

    if (ui_dentro(&B_PREV, x, y))      { s_pulsado = BTN_PREV; ui_ripple_lanza(x, y, ac); }
    else if (ui_dentro(&B_NEXT, x, y)) { s_pulsado = BTN_NEXT; ui_ripple_lanza(x, y, ac); }
    else if (ui_dentro(&acc, x, y))    { s_pulsado = BTN_ACC;  ui_ripple_lanza(x, y, ac); }
    else {
        s_pulsado = -1;
        // Toque directo sobre una fila de AJUSTES: la selecciona
        if (s_scr == SCR_AJUSTES) {
            if      (y < 112) s_sel = 0;
            else if (y < 145) s_sel = 1;
            else if (y < 168) s_sel = 2;
            else if (y < 192) s_sel = 3;
            else              s_sel = 4;
            ui_ripple_lanza(x, y, ac);
        }
    }
}

void hud_touch_hold(int x, int y)
{
    (void)x; (void)y;
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
    (void)x; (void)y;
    if (s_hablando) {
        voice_talk_stop();
        s_hablando = false;
        s_pulsado = -1;
        return;
    }
    switch (s_pulsado) {
        case BTN_PREV: hud_prev_screen(); break;
        case BTN_NEXT: hud_next_screen(); break;
        case BTN_ACC:
            if (s_scr == SCR_AJUSTES) hud_ajuste_incrementa();
            else s_scr = SCR_VOZ;            // atajo a la pantalla de voz
            break;
    }
    s_pulsado = -1;
}

void hud_render(void)
{
    marco();
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
    if (ajustes()->scanlines) display_scanlines(18);
    display_vineta();
    display_flush();
    s_t++;
}
