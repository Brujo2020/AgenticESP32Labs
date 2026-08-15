#pragma once
#include <stdbool.h>
#include "esp_err.h"

// Estado que reporta el servidor. voice NO depende de hud: seria circular.
typedef enum { VOZ_IDLE, VOZ_ESCUCHANDO, VOZ_PENSANDO, VOZ_HABLANDO, VOZ_ERROR } voice_state_t;
voice_state_t voice_state(void);

// Conecta con el puente de voz del Mac (websocket_bridge.py).
esp_err_t voice_init(const char *host, int port);
bool voice_connected(void);

// Pulsar para hablar: mientras esta activo, el microfono viaja al servidor.
void voice_talk_start(void);
void voice_talk_stop(void);
bool voice_talking(void);

// Ultimo texto recibido del servidor (para pintarlo en la pantalla VOZ).
const char *voice_text(void);

// Historial de conversacion: lineas cortas, las mas recientes al final.
#define VOZ_LINEAS   6
#define VOZ_ANCHO   34
int  voice_hist_num(void);
const char *voice_hist(int i);      // 0 = mas antigua
bool voice_hist_es_mio(int i);      // true si la dijo el usuario

// Titulares de noticias que empuja el servidor.
#define VOZ_NOTICIAS 5
int  voice_news_num(void);
const char *voice_news(int i);

// Telemetria del Mac y de las apps creativas (Unity / Blender)
#define VOZ_TELE 7
int  voice_mac_num(void);
const char *voice_mac(int i);
int  voice_creativo_num(void);
const char *voice_creativo(int i);

// ============================================================
//  Protocolo v2 — vistas declarativas, pregunta, notifica
//  Ver servidor/PROTOCOLO.md. El handshake "hola" se manda solo al
//  conectar; el servidor detecta asi que este firmware entiende v2 y
//  deja de degradar a los tres canales fijos (noticia/mac/creativo).
// ============================================================
#define VISTA_MAX        8      // limite real: buffers estaticos, no preferencia
#define VISTA_FILAS_MAX  6
#define VISTA_ID_LEN     16
#define VISTA_TITULO_LEN 11
#define VISTA_TXT_LEN    27
#define VISTA_BADGE_LEN  4
#define VISTA_ACENTO_LEN 8

typedef struct {
    char txt[VISTA_TXT_LEN];
    char color[VISTA_ACENTO_LEN];
    char badge[VISTA_BADGE_LEN];
    bool con_badge;
} vista_fila_t;

typedef struct {
    bool activa;
    char id[VISTA_ID_LEN];
    char titulo[VISTA_TITULO_LEN];
    char acento[VISTA_ACENTO_LEN];
    int  orden;
    int  n_filas;
    vista_fila_t filas[VISTA_FILAS_MAX];
    int64_t vence_ms;      // 0 = permanente; si no, instante de auto-borrado
} vista_t;

// Vistas activas, ya ordenadas por 'orden'. i en [0, voice_vistas_num()).
int voice_vistas_num(void);
const vista_t *voice_vista(int i);

// Pregunta bloqueante: aprobacion fisica. Mientras este activa, el HUD debe
// tomar toda la pantalla (ver hud_render / hud_touch_*).
bool voice_pregunta_activa(void);
const char *voice_pregunta_txt(void);
int  voice_pregunta_num_opciones(void);
const char *voice_pregunta_opcion(int i);
int  voice_pregunta_segundos_restantes(void);
// Envia la respuesta al servidor (o -1 si vencio el plazo) y cierra la pregunta.
void voice_pregunta_responde(int opcion);

// Notificacion: banda superior temporal, no roba la navegacion.
bool voice_notifica_activa(void);
const char *voice_notifica_txt(void);
const char *voice_notifica_nivel(void);   // "info" "ok" "warn" "error"

// Protocolo v2 detectado (el servidor no manda 'hola', lo recibe: esto
// refleja si el firmware YA emitio su propio handshake al conectar).
bool voice_v2_activo(void);
