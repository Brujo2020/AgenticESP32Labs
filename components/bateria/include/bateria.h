#pragma once
#include <stdbool.h>
#include "esp_err.h"

// Lectura de la bateria de la placa (BOARD_BAT_ADC / BOARD_CHARGE_DET).
//
// El pin ya estaba declarado en board_pins.h desde el principio pero nadie lo
// leia: el HUD no tenia forma de decir cuanta bateria queda. Esto lo cierra.
//
// La medida se hace por muestreo con media movil: el ADC de un ESP32 es
// ruidoso y un valor crudo hace bailar el porcentaje varios puntos entre
// fotogramas, que en pantalla se ve como un fallo aunque no lo sea.

esp_err_t bateria_init(void);

// true si el ADC quedo configurado. Si es false, el resto devuelve valores
// neutros y el HUD simplemente no pinta el indicador.
bool bateria_disponible(void);

// Carga estimada 0..100. -1 si no hay lectura fiable todavia.
int bateria_pct(void);

// Voltaje del pack en milivoltios (ya compensado el divisor). 0 si no hay.
int bateria_mv(void);

// true si esta enchufada y cargando (BOARD_CHARGE_DET).
bool bateria_cargando(void);

// Relee el ADC y actualiza la media movil. Barato: pensado para llamarse
// desde el bucle del HUD, que ya corre a 30 fps. Internamente solo muestrea
// cada ~2 s, asi que llamarlo en cada fotograma no cuesta nada.
void bateria_actualiza(void);
