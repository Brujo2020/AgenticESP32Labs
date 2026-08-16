#pragma once
#include <stdbool.h>

// Preferencias que sobreviven al reinicio (se guardan en NVS).
// 'tema' usa el mismo orden que ACENTOS en servidor/nucleo/canal.py, para que
// el panel web y el firmware hablen del mismo indice sin tabla de traduccion:
//   0=cyan 1=magenta 2=lima 3=ambar 4=hielo 5=sangre 6=gris 7=blanco
typedef struct {
    int  brillo;        // 0..100
    int  volumen;       // 0..100
    int  tema;          // 0..7, ver arriba
    bool scanlines;
    bool rejilla;
    bool efectos;        // pitidos de confirmacion/notificacion
} ajustes_t;

void ajustes_cargar(void);
void ajustes_guardar(void);
ajustes_t *ajustes(void);

// Aplica brillo y volumen al hardware
void ajustes_aplicar(void);

// Color de acento del tema activo
unsigned short ajustes_acento(void);
