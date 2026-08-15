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

// Hora real por SNTP. Requiere WiFi. Rellena la hora del sistema.
void net_sync_time(void);
bool net_time_valid(void);

// Clima por HTTP (Open-Meteo, sin API key). Bloquea unos segundos.
void net_weather_update(void);
clima_t net_weather(void);
const char *net_weather_desc(int codigo);
