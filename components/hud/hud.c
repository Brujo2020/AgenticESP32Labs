// ============================================================
//  HUD circular: cuatro pantallas conmutables al tacto
//    RELOJ  · CLIMA · VOZ · SISTEMA
// ============================================================
#include "hud.h"
#include "display.h"
#include "audio.h"
#include "touch.h"
#include "net.h"
#include "voice.h"
#include <stdio.h>
#include <string.h>
#include <time.h>
#include "esp_timer.h"
#include "esp_heap_caps.h"
#include "esp_system.h"   // esp_get_free_heap_size

#define CX 120
#define CY 120

// Azul corporativo NTT DATA aproximado en RGB565
#define C_NTT  0x019F

static hud_state_t  s_state = ST_IDLE;
static hud_screen_t s_scr   = SCR_RELOJ;
static int s_ang = 0;

void hud_init(void) { s_state = ST_IDLE; s_scr = SCR_RELOJ; }
void hud_set_state(hud_state_t s) { s_state = s; }
hud_state_t hud_get_state(void) { return s_state; }
hud_screen_t hud_screen(void) { return s_scr; }
void hud_next_screen(void) { s_scr = (s_scr + 1) % SCR_TOTAL; }

static uint16_t color_estado(void)
{
    switch (s_state) {
        case ST_LISTENING:  return C_CYAN;
        case ST_PROCESSING: return C_ORANGE;
        case ST_SPEAKING:   return C_GREEN;
        case ST_ERROR:      return C_RED;
        default:            return C_GREY;
    }
}

// Marco comun: anillos, arcos girando e indicadores de conexion
static void marco(void)
{
    display_clear(C_DARK);
    display_circle(CX, CY, 118, C_CYAN);
    display_circle(CX, CY, 114, C_GREY);

    display_arc(CX, CY, 105, 3,  s_ang,           s_ang + 110,          C_CYAN);
    display_arc(CX, CY,  99, 2, -s_ang * 2,      -s_ang * 2 + 80,       C_ORANGE);

    // Puntitos de estado del hardware, arriba
    display_fill_circle(CX - 22, 26, 4, net_connected() ? C_GREEN : C_RED);
    display_fill_circle(CX,      26, 4, audio_ready()   ? C_GREEN : C_RED);
    display_fill_circle(CX + 22, 26, 4, touch_ready()   ? C_GREEN : C_RED);

    // Marca NTT DATA, discreta en la parte baja
    display_text_center(CX, 196, "NTT DATA", C_NTT, 1);
    display_rect(CX - 26, 205, 52, 1, C_NTT);

    // Marcador de pantalla activa, abajo
    for (int i = 0; i < SCR_TOTAL; i++)
        display_fill_circle(CX - 35 + i * 10, 218, i == s_scr ? 3 : 2,
                            i == s_scr ? C_CYAN : C_GREY);
}

static void pantalla_reloj(void)
{
    char txt[32];
    time_t now; time(&now);
    struct tm tm; localtime_r(&now, &tm);

    if (net_time_valid()) {
        snprintf(txt, sizeof(txt), "%02d:%02d", tm.tm_hour, tm.tm_min);
        display_text_center(CX, 92, txt, C_CYAN, 5);
        snprintf(txt, sizeof(txt), "%02d", tm.tm_sec);
        display_text_center(CX, 140, txt, C_ORANGE, 2);
        static const char *dias[] = {"DOM","LUN","MAR","MIE","JUE","VIE","SAB"};
        snprintf(txt, sizeof(txt), "%s %02d/%02d",
                 dias[tm.tm_wday % 7], tm.tm_mday, tm.tm_mon + 1);
        display_text_center(CX, 166, txt, C_YELLOW, 1);
    } else {
        display_text_center(CX, 100, "SIN HORA", C_GREY, 2);
        display_text_center(CX, 130, "SIN RED", C_RED, 1);
    }
}

static void pantalla_clima(void)
{
    clima_t c = net_weather();
    display_text_center(CX, 64, "CLIMA", C_GREY, 1);
    if (c.valid) {
        char t[24];
        snprintf(t, sizeof(t), "%d", (int)(c.temp_c + 0.5f));
        display_text_center(CX - 8, 90, t, C_ORANGE, 5);
        display_text(CX + 42, 92, "C", C_ORANGE, 2);
        display_text_center(CX, 142, net_weather_desc(c.codigo), C_CYAN, 1);
        snprintf(t, sizeof(t), "VIENTO %d KMH", (int)(c.viento_kmh + 0.5f));
        display_text_center(CX, 162, t, C_YELLOW, 1);
    } else {
        display_text_center(CX, 110, "SIN DATOS", C_GREY, 2);
    }
}

static void pantalla_voz(void)
{
    // El estado real lo manda el servidor a traves de voice
    switch (voice_state()) {
        case VOZ_ESCUCHANDO: s_state = ST_LISTENING;  break;
        case VOZ_PENSANDO:   s_state = ST_PROCESSING; break;
        case VOZ_HABLANDO:   s_state = ST_SPEAKING;   break;
        case VOZ_ERROR:      s_state = ST_ERROR;      break;
        default:             s_state = ST_IDLE;       break;
    }

    const char *txt;
    switch (s_state) {
        case ST_LISTENING:  txt = "ESCUCHANDO";  break;
        case ST_PROCESSING: txt = "PENSANDO";    break;
        case ST_SPEAKING:   txt = "HABLANDO";    break;
        case ST_ERROR:      txt = "ERROR";       break;
        default:            txt = "EN ESPERA";   break;
    }
    uint16_t col = color_estado();

    // Circulo que late con el nivel del microfono
    int lvl = audio_mic_level();
    display_fill_circle(CX, 104, 16 + lvl / 4, col);
    display_fill_circle(CX, 104, 10, C_DARK);
    display_text_center(CX, 140, txt, col, 2);

    // Barra VU
    int bw = 130, bx = CX - bw / 2;
    for (int i = 0; i < bw; i++) {
        uint16_t c = (i < lvl * bw / 100)
                   ? (i > bw*3/4 ? C_RED : (i > bw/2 ? C_YELLOW : C_GREEN))
                   : 0x2104;
        display_rect(bx + i, 172, 1, 7, c);
    }
    // Texto que manda el servidor (lo que entendio o lo que responde)
    const char *msg = voice_text();
    if (msg && msg[0]) display_text_center(CX, 186, msg, C_WHITE, 1);
    else display_text_center(CX, 186, voice_connected() ? "MANTEN PARA HABLAR" : "SIN SERVIDOR",
                             voice_connected() ? C_GREY : C_RED, 1);
}

static void pantalla_sistema(void)
{
    char t[32];
    display_text_center(CX, 60, "SISTEMA", C_GREY, 1);

    display_text(46, 84, "IP", C_GREY, 1);
    display_text(78, 84, net_ip(), C_CYAN, 1);

    snprintf(t, sizeof(t), "%d DBM", net_rssi());
    display_text(46, 102, "WIFI", C_GREY, 1);
    display_text(90, 102, net_connected() ? t : "NO", net_connected() ? C_GREEN : C_RED, 1);

    snprintf(t, sizeof(t), "%d KB", (int)(esp_get_free_heap_size() / 1024));
    display_text(46, 120, "RAM", C_GREY, 1);
    display_text(90, 120, t, C_YELLOW, 1);

    int s = (int)(esp_timer_get_time() / 1000000);
    snprintf(t, sizeof(t), "%02d:%02d:%02d", s / 3600, (s / 60) % 60, s % 60);
    display_text(46, 138, "ON", C_GREY, 1);
    display_text(90, 138, t, C_PINK, 1);

    display_text_center(CX, 166, audio_ready() ? "AUDIO OK" : "AUDIO --",
                        audio_ready() ? C_GREEN : C_RED, 1);
    display_text_center(CX, 182, touch_ready() ? "TACTIL OK" : "TACTIL --",
                        touch_ready() ? C_GREEN : C_RED, 1);
}

static void pantalla_chat(void)
{
    display_text_center(CX, 52, "CONVERSACION", C_GREY, 1);
    int n = voice_hist_num();
    if (n == 0) {
        display_text_center(CX, 110, "MANTEN PARA", C_GREY, 1);
        display_text_center(CX, 124, "HABLARME", C_GREY, 1);
        return;
    }
    // Las mas recientes abajo; el ancho util se estrecha por ser circular
    int y = 70;
    for (int i = 0; i < n && y < 190; i++, y += 20) {
        bool mio = voice_hist_es_mio(i);
        display_text(mio ? 30 : 40, y, mio ? ">" : "<", mio ? C_CYAN : C_GREEN, 1);
        display_text(mio ? 42 : 52, y, voice_hist(i), mio ? C_WHITE : C_GREEN, 1);
    }
}

static void pantalla_noticias(void)
{
    display_text_center(CX, 50, "NOTICIAS IA", C_ORANGE, 1);
    int n = voice_news_num();
    if (n == 0) {
        display_text_center(CX, 110, voice_connected() ? "CARGANDO" : "SIN SERVIDOR",
                            voice_connected() ? C_GREY : C_RED, 1);
        return;
    }
    int y = 74;
    for (int i = 0; i < n && y < 190; i++, y += 22) {
        display_fill_circle(32, y + 3, 2, C_ORANGE);
        display_text(42, y, voice_news(i), C_WHITE, 1);
    }
}

static void lista(const char *titulo, uint16_t col,
                  int n, const char *(*get)(int), const char *vacio)
{
    display_text_center(CX, 48, titulo, col, 1);
    if (n == 0) {
        display_text_center(CX, 112, vacio, C_GREY, 1);
        return;
    }
    int y = 68;
    for (int i = 0; i < n && y < 192; i++, y += 18) {
        display_fill_circle(30, y + 3, 2, col);
        display_text(38, y, get(i), C_WHITE, 1);
    }
}

static void pantalla_mac(void)
{
    lista("MI MAC", C_CYAN, voice_mac_num(), voice_mac,
          voice_connected() ? "MIDIENDO" : "SIN SERVIDOR");
}

static void pantalla_creativo(void)
{
    lista("UNITY / BLENDER", C_PINK, voice_creativo_num(), voice_creativo,
          voice_connected() ? "CONSULTANDO" : "SIN SERVIDOR");
}

void hud_render(void)
{
    marco();
    switch (s_scr) {
        case SCR_RELOJ:   pantalla_reloj();   break;
        case SCR_CLIMA:   pantalla_clima();   break;
        case SCR_VOZ:     pantalla_voz();     break;
        case SCR_CHAT:    pantalla_chat();    break;
        case SCR_NOTICIAS:pantalla_noticias();break;
        case SCR_MAC:     pantalla_mac();     break;
        case SCR_CREATIVO:pantalla_creativo();break;
        default:          pantalla_sistema(); break;
    }

    // Punto donde el dedo toca
    int tx, ty;
    if (touch_get(&tx, &ty)) display_fill_circle(tx, ty, 5, C_WHITE);

    display_flush();
    s_ang = (s_ang + 4) % 360;
}
