#pragma once
#include <stdbool.h>
#include "esp_err.h"

// Inicializa el tactil CST816. No aborta si falla: devuelve el error.
esp_err_t touch_init(void);

// Devuelve true si hay un dedo en pantalla y escribe sus coordenadas.
bool touch_get(int *x, int *y);

// true si el tactil se inicializo correctamente.
bool touch_ready(void);
