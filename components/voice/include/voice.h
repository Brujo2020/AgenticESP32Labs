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
