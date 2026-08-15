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
