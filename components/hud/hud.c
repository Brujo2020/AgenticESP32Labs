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

    if (aj->rejilla) display_hex_grid(s_t, ac);

    // Aro exterior con degradado y arco que orbita
    display_circle(CX, CY, 118, display_escala(ac, 120));
    display_arc_grad(CX, CY, 116, 2, 0, 359, display_escala(ac, 40), display_escala(ac, 90));
    display_arc_glow(CX, CY, 116, 2, s_t % 360, (s_t % 360) + 70, ac);
    display_arc_glow(CX, CY, 110, 1, -(s_t * 2) % 360, (-(s_t * 2) % 360) + 40, C_MAGENTA);
    display_corchetes(104, display_escala(ac, 200));
    display_ring_dots(CX, CY, 100, 12, s_t, ac);

    // Cabecera: nombre de la pantalla y estado del enlace
    display_text_center(CX, 12, NOMBRES[s_scr], display_escala(ac, 220), 1);
    display_fill_circle(CX - 26, 32, 3, net_connected()   ? C_LIME : C_BLOOD);
    display_fill_circle(CX,      32, 3, audio_ready()     ? C_LIME : C_BLOOD);
    display_fill_circle(CX + 26, 32, 3, voice_connected() ? C_LIME : C_BLOOD);

    // Identidad
    display_text_center(CX, 200, "MARIO ESP32", display_escala(ac, 170), 1);

    // Indicador de pantalla activa
    for (int i = 0; i < SCR_TOTAL; i++) {
        int x = CX - 40 + i * 9;
        if (i == s_scr) display_fill_circle(x, 214, 3, ac);
        else display_fill_circle(x, 214, 1, display_escala(ac, 90));
    }
}

// ============================================================
//  Pantallas
// ============================================================
static void p_nucleo(void)
{
    uint16_t ac = ajustes_acento();
    // Reactor: anillos que respiran con el nivel del microfono
    int lvl = audio_mic_level();
    float pulso = 0.5f + 0.5f * sinf(s_t * 0.09f);
    int r = 26 + (int)(10 * pulso) + lvl / 5;

    display_fill_circle(CX, CY, r, display_escala(ac, 60));
    display_circle(CX, CY, r + 6, display_escala(ac, 180));
    display_arc_glow(CX, CY, r + 12, 2, s_t * 3 % 360, s_t * 3 % 360 + 120, ac);
    display_arc_glow(CX, CY, r + 18, 1, -s_t * 4 % 360, -s_t * 4 % 360 + 90, C_MAGENTA);
    display_fill_circle(CX, CY, 6, C_WHITE);

    display_text_center(CX, 168, net_connected() ? "ENLACE ACTIVO" : "FUERA DE LINEA",
                        net_connected() ? C_LIME : C_BLOOD, 1);
}

static void p_reloj(void)
{
    char txt[32];
    uint16_t ac = ajustes_acento();
    time_t now; time(&now);
    struct tm tm; localtime_r(&now, &tm);

    if (!net_time_valid()) {
        display_text_center(CX, 112, "SIN SINCRONIZAR", C_GREY, 1);
        return;
    }
    // Arco de segundos
    display_arc_grad(CX, CY, 92, 3, -90, -90 + tm.tm_sec * 6, ac, C_MAGENTA);

    snprintf(txt, sizeof(txt), "%02d:%02d", tm.tm_hour, tm.tm_min);
    display_text_glow(CX, 96, txt, C_WHITE, 5);
    snprintf(txt, sizeof(txt), ":%02d", tm.tm_sec);
    display_text_center(CX, 142, txt, ac, 2);

    static const char *dias[] = {"DOM","LUN","MAR","MIE","JUE","VIE","SAB"};
    snprintf(txt, sizeof(txt), "%s %02d/%02d", dias[tm.tm_wday % 7], tm.tm_mday, tm.tm_mon + 1);
    display_text_center(CX, 168, txt, display_escala(ac, 190), 1);
}

static void p_clima(void)
{
    clima_t c = net_weather();
    if (!c.valid) { display_text_center(CX, 112, "SIN DATOS", C_GREY, 1); return; }

    char t[24];
    int temp = (int)(c.temp_c + 0.5f);
    // El arco colorea de frio a calor
    uint16_t c0 = C_CYAN, c1 = C_BLOOD;
    int pct = temp < 0 ? 0 : (temp > 40 ? 100 : temp * 100 / 40);
    display_arc_grad(CX, CY, 92, 4, 130, 130 + pct * 28 / 10, c0, c1);

    snprintf(t, sizeof(t), "%d", temp);
    display_text_glow(CX - 6, 92, t, C_WHITE, 5);
    display_text(CX + 34, 96, "C", C_AMBER, 2);
    display_text_center(CX, 146, net_weather_desc(c.codigo), C_ICE, 1);
    snprintf(t, sizeof(t), "VIENTO %d KMH", (int)(c.viento_kmh + 0.5f));
    display_text_center(CX, 166, t, display_escala(C_ICE, 170), 1);
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
    for (int i = 0; i < 48; i++) {
        float a = i * 7.5f * (float)M_PI / 180.0f;
        int alto = 6 + (lvl * (4 + (i % 5) * 3)) / 40
                 + (int)(4 * sinf(s_t * 0.2f + i));
        if (alto > 34) alto = 34;
        for (int k = 0; k < alto; k++) {
            int r = 44 + k;
            display_px(CX + (int)(r * cosf(a)), CY + (int)(r * sinf(a)),
                       display_mezcla(col, C_MAGENTA, (uint8_t)(k * 255 / 34)));
        }
    }
    display_fill_circle(CX, CY, 34, C_VOID);
    display_circle(CX, CY, 34, display_escala(col, 200));
    display_text_center(CX, 116, txt, col, 1);

    const char *msg = voice_text();
    if (msg && msg[0]) display_text_center(CX, 176, msg, C_WHITE, 1);
    else display_text_center(CX, 176,
                             voice_connected() ? "MANTEN PARA HABLAR" : "SIN SERVIDOR",
                             voice_connected() ? C_GREY : C_BLOOD, 1);
}

static void lista(int n, const char *(*get)(int), uint16_t col, const char *vacio)
{
    if (n == 0) { display_text_center(CX, 112, vacio, C_GREY, 1); return; }
    int y = 64;
    for (int i = 0; i < n && y < 190; i++, y += 17) {
        display_fill_circle(30, y + 3, 2, col);
        display_px_glow(31, y + 3, col, 120);
        display_text(38, y, get(i), C_WHITE, 1);
    }
}

static void p_chat(void)
{
    int n = voice_hist_num();
    if (n == 0) { display_text_center(CX, 112, "MANTEN PARA HABLAR", C_GREY, 1); return; }
    int y = 60;
    for (int i = 0; i < n && y < 192; i++, y += 19) {
        bool mio = voice_hist_es_mio(i);
        uint16_t c = mio ? C_CYAN : C_LIME;
        display_text(28, y, mio ? ">" : "<", c, 1);
        display_text(40, y, voice_hist(i), mio ? C_WHITE : display_escala(C_LIME, 220), 1);
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
    int bw = 108, bx = CX - bw / 2;
    display_text(bx, y - 12, etiqueta, sel ? C_WHITE : display_escala(col, 160), 1);
    display_rect(bx, y, bw, 6, 0x2124);
    for (int i = 0; i < bw * pct / 100; i++)
        display_rect(bx + i, y, 1, 6, display_mezcla(col, C_MAGENTA, (uint8_t)(i * 255 / bw)));
    if (sel) {
        display_line(bx - 8, y + 3, bx - 4, y + 3, C_WHITE);
        display_rect(bx - 1, y - 2, 1, 10, C_WHITE);
        display_rect(bx + bw, y - 2, 1, 10, C_WHITE);
    }
    char t[8]; snprintf(t, sizeof(t), "%d", pct);
    display_text(bx + bw + 6, y - 1, t, sel ? C_WHITE : C_GREY, 1);
}

static void conmutador(int y, const char *etiqueta, bool on, bool sel)
{
    int bx = CX - 54;
    display_text(bx, y, etiqueta, sel ? C_WHITE : C_GREY, 1);
    uint16_t c = on ? C_LIME : C_GREY;
    display_rect(bx + 76, y, 22, 9, display_escala(c, 70));
    display_fill_circle(bx + (on ? 92 : 82), y + 4, 4, c);
    if (sel) display_text(bx - 10, y, ">", C_WHITE, 1);
}

static void p_ajustes(void)
{
    ajustes_t *a = ajustes();
    uint16_t ac = ajustes_acento();
    static const char *TEMAS[] = {"CIAN","MAGENTA","LIMA","AMBAR"};

    barra(72,  "BRILLO",  a->brillo,  ac, s_sel == 0);
    barra(102, "VOLUMEN", a->volumen, ac, s_sel == 1);

    int bx = CX - 54;
    display_text(bx, 124, "TEMA", s_sel == 2 ? C_WHITE : C_GREY, 1);
    display_text(bx + 60, 124, TEMAS[a->tema], ac, 1);
    if (s_sel == 2) display_text(bx - 10, 124, ">", C_WHITE, 1);

    conmutador(146, "SCANLINES", a->scanlines, s_sel == 3);
    conmutador(164, "REJILLA",   a->rejilla,   s_sel == 4);

    display_text_center(CX, 184, "TOQUE CAMBIA  LARGO AJUSTA", display_escala(ac, 150), 1);
}

static void p_sistema(void)
{
    char t[32];
    uint16_t ac = ajustes_acento();
    int y = 62;
    display_text(34, y, "IP", C_GREY, 1);   display_text(74, y, net_ip(), ac, 1); y += 18;
    snprintf(t, sizeof(t), "%d DBM", net_rssi());
    display_text(34, y, "RF", C_GREY, 1);
    display_text(74, y, net_connected() ? t : "NO", net_connected() ? C_LIME : C_BLOOD, 1); y += 18;
    snprintf(t, sizeof(t), "%d KB", (int)(esp_get_free_heap_size() / 1024));
    display_text(34, y, "RAM", C_GREY, 1);  display_text(74, y, t, C_AMBER, 1); y += 18;
    int s = (int)(esp_timer_get_time() / 1000000);
    snprintf(t, sizeof(t), "%02d:%02d:%02d", s / 3600, (s / 60) % 60, s % 60);
    display_text(34, y, "ON", C_GREY, 1);   display_text(74, y, t, C_MAGENTA, 1); y += 20;

    display_text_center(CX, y, audio_ready() ? "AUDIO OK" : "AUDIO --",
                        audio_ready() ? C_LIME : C_BLOOD, 1); y += 16;
    display_text_center(CX, y, touch_ready() ? "TACTIL OK" : "TACTIL --",
                        touch_ready() ? C_LIME : C_BLOOD, 1);
}

// ============================================================
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

    int tx, ty;
    if (touch_get(&tx, &ty)) {
        display_fill_circle(tx, ty, 4, C_WHITE);
        display_circle(tx, ty, 8, display_escala(ajustes_acento(), 180));
    }

    // Transicion al cambiar de pantalla: destello que se apaga
    if (s_trans > 0) {
        display_fade((uint8_t)(255 - s_trans * 22));
        s_trans--;
    }
    if (ajustes()->scanlines) display_scanlines(26);
    display_vineta();
    display_flush();
    s_t++;
}
