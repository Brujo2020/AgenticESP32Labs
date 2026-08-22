#pragma once
#include <stdbool.h>
#include "esp_err.h"

// Redes WiFi guardadas en el propio dispositivo (NVS).
//
// Por que existe esto:
//
// 1. La contrasena estaba escrita en net.c, o sea versionada y subida a
//    GitHub. Una clave de WiFi domestica en un repositorio es un problema
//    aunque el repo sea privado hoy: los repos cambian de visibilidad, se
//    clonan y se comparten.
//
// 2. Cambiar de red exigia recompilar y reflashear. Con la placa moviendose
//    entre casa y el trabajo, eso es reflashear dos veces por semana.
//
// La solucion no es "poder cambiar la red comoda mente", es no tener que
// cambiarla: se guardan VARIAS redes y al arrancar se conecta a la que este
// disponible. Con casa y trabajo grabadas, la placa funciona en los dos
// sitios sin tocar nada.

#define WIFI_MAX_REDES 5
#define WIFI_SSID_MAX  33      // 32 + terminador
#define WIFI_PASS_MAX  65      // 64 + terminador

typedef struct {
    char ssid[WIFI_SSID_MAX];
    char pass[WIFI_PASS_MAX];
} wifi_red_t;

// Carga las redes de NVS. Si no hay ninguna guardada y el firmware se compilo
// con credenciales por defecto, las siembra para que la placa no quede
// incomunicada tras actualizar.
void wifi_redes_carga(void);

// Cuantas hay guardadas, y el SSID de la i-esima (nunca la contrasena: no
// hay ninguna razon legitima para leerla desde fuera).
int  wifi_redes_num(void);
const char *wifi_red_ssid(int i);

// Anade o actualiza una red. Si el SSID ya existe se le cambia la clave en
// vez de duplicarla. Con la lista llena se descarta la mas antigua.
esp_err_t wifi_red_guarda(const char *ssid, const char *pass);

// Borra una red por SSID. No deja borrar la ultima: quedarse sin ninguna
// dejaria la placa sin forma de volver a conectarse.
esp_err_t wifi_red_borra(const char *ssid);

// Elige que red usar: escanea el entorno y devuelve la guardada con mejor
// senal de entre las que estan al alcance. -1 si no hay ninguna visible.
int wifi_red_mejor_visible(void);

// Acceso a la credencial completa. Solo para el propio arranque del WiFi.
const wifi_red_t *wifi_red(int i);
