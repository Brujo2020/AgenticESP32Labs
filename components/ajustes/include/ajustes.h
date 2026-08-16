#pragma once
#include <stdbool.h>

// Preferencias que sobreviven al reinicio (se guardan en NVS).
// 'tema' usa el mismo orden que ACENTOS en servidor/nucleo/canal.py, para que
// el panel web y el firmware hablen del mismo indice sin tabla de traduccion:
//   0=cyan 1=magenta 2=lima 3=ambar 4=hielo 5=sangre 6=gris 7=blanco
// Pantallas fijas del carrusel (SCR_NUCLEO..SCR_SISTEMA en hud.h).
#define AJ_PANTALLAS_FIJAS 10

typedef struct {
    int  brillo;        // 0..100
    int  volumen;       // 0..100
    int  tema;          // 0..7, ver arriba
    bool scanlines;
    bool rejilla;
    bool efectos;        // pitidos de confirmacion/notificacion

    // Carrusel configurable desde el panel web. 'mascara' es un bit por
    // pantalla fija: 1 = visible en el carrusel, 0 = se salta al navegar.
    // 'orden' es la secuencia en la que se recorren (indices de pantalla);
    // las que no aparezcan quedan al final en su orden natural.
    // Por defecto: todas visibles, orden natural -- identico al de siempre.
    unsigned short mascara;
    signed char    orden[AJ_PANTALLAS_FIJAS];
} ajustes_t;

void ajustes_cargar(void);
void ajustes_guardar(void);
ajustes_t *ajustes(void);

// Aplica brillo y volumen al hardware
void ajustes_aplicar(void);

// Color de acento del tema activo
unsigned short ajustes_acento(void);

// ¿esta pantalla fija visible en el carrusel? Fuera de rango -> true, para
// que un indice inesperado nunca haga desaparecer una pantalla.
bool ajustes_pantalla_visible(int indice);

// Posicion de una pantalla en el orden configurado (menor = antes).
int ajustes_pantalla_pos(int indice);
