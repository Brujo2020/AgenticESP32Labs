#pragma once
#include <stdbool.h>
#include "esp_err.h"

typedef struct {
    bool  valid;
    float temp_c;
    float viento_kmh;
    int   codigo;        // codigo WMO de Open-Meteo
} clima_t;

// WiFi en modo estacion. No bloquea mas de ~10 s.
esp_err_t net_init(void);
bool net_connected(void);
const char *net_ip(void);
int  net_rssi(void);

// SSID al que se esta conectado ahora, o "" si no hay WiFi. Lo usa el HUD y
// el reporte de estado al panel: saber a QUE red esta conectada la placa es
// la mitad del diagnostico cuando no aparece.
const char *net_ssid_actual(void);

// Hora real por SNTP. Requiere WiFi. Rellena la hora del sistema.
void net_sync_time(void);
bool net_time_valid(void);

// Clima por HTTP (Open-Meteo, sin API key). Bloquea unos segundos.
void net_weather_update(void);
clima_t net_weather(void);
const char *net_weather_desc(int codigo);

// Descubre el servidor de voz por mDNS (_hud._tcp). Devuelve true y
// escribe la IP encontrada; si no aparece nadie, devuelve false.
bool net_descubre_servidor(char *ip_out, int largo, int *puerto_out);
