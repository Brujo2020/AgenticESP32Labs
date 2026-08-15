#pragma once
#include <stdbool.h>

// Estado del asistente (lo controlara el servidor mas adelante)
typedef enum { ST_IDLE, ST_LISTENING, ST_PROCESSING, ST_SPEAKING, ST_ERROR } hud_state_t;

// Pantallas: se cambia tocando la pantalla
typedef enum { SCR_RELOJ, SCR_CLIMA, SCR_VOZ, SCR_CHAT,
               SCR_NOTICIAS, SCR_MAC, SCR_CREATIVO, SCR_SISTEMA, SCR_TOTAL } hud_screen_t;

void hud_init(void);
void hud_set_state(hud_state_t s);
hud_state_t hud_get_state(void);
void hud_next_screen(void);
hud_screen_t hud_screen(void);

// Dibuja un fotograma completo (incluye el flush a pantalla)
void hud_render(void);
