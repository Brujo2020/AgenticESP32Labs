#pragma once
#include <stdbool.h>

typedef enum { ST_IDLE, ST_LISTENING, ST_PROCESSING, ST_SPEAKING, ST_ERROR } hud_state_t;

typedef enum {
    SCR_NUCLEO,      // portada: identidad + reactor animado
    SCR_RELOJ,
    SCR_CLIMA,
    SCR_VOZ,
    SCR_CHAT,
    SCR_NOTICIAS,
    SCR_MAC,
    SCR_CREATIVO,
    SCR_AJUSTES,
    SCR_SISTEMA,
    SCR_TOTAL
} hud_screen_t;

void hud_init(void);
void hud_boot_anim(void);              // secuencia de arranque
void hud_set_state(hud_state_t s);
hud_state_t hud_get_state(void);
void hud_next_screen(void);
hud_screen_t hud_screen(void);

// En AJUSTES, el toque actua sobre el control seleccionado
void hud_ajuste_siguiente(void);
void hud_ajuste_incrementa(void);
bool hud_en_ajustes(void);

// Gestion tactil: main solo reenvia, la logica vive aqui
void hud_touch_down(int x, int y);
void hud_touch_hold(int x, int y);
void hud_touch_up(int x, int y);
bool hud_hablando(void);

void hud_render(void);
