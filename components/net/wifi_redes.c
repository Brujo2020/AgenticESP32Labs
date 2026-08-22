#include "wifi_redes.h"

#include <string.h>
#include <stdio.h>

#include "esp_log.h"
#include "esp_wifi.h"
#include "nvs.h"
#include "nvs_flash.h"

static const char *TAG = "wifi-redes";
static const char *NVS_NS = "wifi";
static const char *NVS_KEY = "redes";

static wifi_red_t s_redes[WIFI_MAX_REDES];
static int s_n = 0;

// Credenciales de fabrica. Solo se usan si NVS esta vacio -- por ejemplo la
// primera vez que se flashea este firmware sobre uno que llevaba la red
// escrita en el codigo. A partir de ahi mandan las de NVS.
#ifndef WIFI_SSID_DEFECTO
#define WIFI_SSID_DEFECTO ""
#endif
#ifndef WIFI_PASS_DEFECTO
#define WIFI_PASS_DEFECTO ""
#endif


static void guarda_en_nvs(void)
{
    nvs_handle_t h;
    if (nvs_open(NVS_NS, NVS_READWRITE, &h) != ESP_OK) {
        ESP_LOGE(TAG, "no se pudo abrir NVS para guardar");
        return;
    }
    // Se guarda el array entero como blob: son 5 x 98 bytes, no compensa
    // inventar un esquema por campos que ademas habria que migrar.
    nvs_set_blob(h, NVS_KEY, s_redes, sizeof(wifi_red_t) * s_n);
    nvs_commit(h);
    nvs_close(h);
    ESP_LOGI(TAG, "guardadas %d red(es)", s_n);
}


void wifi_redes_carga(void)
{
    s_n = 0;
    nvs_handle_t h;
    if (nvs_open(NVS_NS, NVS_READONLY, &h) == ESP_OK) {
        size_t bytes = sizeof(s_redes);
        if (nvs_get_blob(h, NVS_KEY, s_redes, &bytes) == ESP_OK) {
            s_n = (int)(bytes / sizeof(wifi_red_t));
            if (s_n > WIFI_MAX_REDES) s_n = WIFI_MAX_REDES;
        }
        nvs_close(h);
    }

    if (s_n == 0 && WIFI_SSID_DEFECTO[0]) {
        // Siembra: evita que actualizar el firmware deje la placa sin red.
        snprintf(s_redes[0].ssid, WIFI_SSID_MAX, "%s", WIFI_SSID_DEFECTO);
        snprintf(s_redes[0].pass, WIFI_PASS_MAX, "%s", WIFI_PASS_DEFECTO);
        s_n = 1;
        guarda_en_nvs();
        ESP_LOGW(TAG, "NVS vacio: sembrada la red compilada '%s'", s_redes[0].ssid);
    }

    for (int i = 0; i < s_n; i++)
        ESP_LOGI(TAG, "red %d: %s", i, s_redes[i].ssid);
}


int wifi_redes_num(void) { return s_n; }

const char *wifi_red_ssid(int i)
{
    return (i >= 0 && i < s_n) ? s_redes[i].ssid : "";
}

const wifi_red_t *wifi_red(int i)
{
    return (i >= 0 && i < s_n) ? &s_redes[i] : NULL;
}


esp_err_t wifi_red_guarda(const char *ssid, const char *pass)
{
    if (!ssid || !ssid[0]) return ESP_ERR_INVALID_ARG;
    if (strlen(ssid) >= WIFI_SSID_MAX) return ESP_ERR_INVALID_SIZE;
    if (pass && strlen(pass) >= WIFI_PASS_MAX) return ESP_ERR_INVALID_SIZE;

    // Si ya existe, se actualiza la clave. Sin esto, reenviar la misma red
    // con la contrasena corregida creaba una segunda entrada y la vieja
    // (erronea) se seguia intentando primero.
    for (int i = 0; i < s_n; i++) {
        if (!strcmp(s_redes[i].ssid, ssid)) {
            snprintf(s_redes[i].pass, WIFI_PASS_MAX, "%s", pass ? pass : "");
            guarda_en_nvs();
            return ESP_OK;
        }
    }

    if (s_n >= WIFI_MAX_REDES) {
        // Lista llena: cae la mas antigua. Se prefiere esto a rechazar la
        // nueva, porque quien acaba de teclear una red la quiere AHORA.
        memmove(&s_redes[0], &s_redes[1], sizeof(wifi_red_t) * (WIFI_MAX_REDES - 1));
        s_n = WIFI_MAX_REDES - 1;
        ESP_LOGW(TAG, "lista llena: se descarta la red mas antigua");
    }
    snprintf(s_redes[s_n].ssid, WIFI_SSID_MAX, "%s", ssid);
    snprintf(s_redes[s_n].pass, WIFI_PASS_MAX, "%s", pass ? pass : "");
    s_n++;
    guarda_en_nvs();
    return ESP_OK;
}


esp_err_t wifi_red_borra(const char *ssid)
{
    if (!ssid || !ssid[0]) return ESP_ERR_INVALID_ARG;
    // Quedarse sin ninguna red dejaria la placa incomunicada y sin forma de
    // arreglarlo salvo reflasheando. No se permite.
    if (s_n <= 1) return ESP_ERR_INVALID_STATE;

    for (int i = 0; i < s_n; i++) {
        if (!strcmp(s_redes[i].ssid, ssid)) {
            memmove(&s_redes[i], &s_redes[i + 1],
                    sizeof(wifi_red_t) * (s_n - i - 1));
            s_n--;
            guarda_en_nvs();
            return ESP_OK;
        }
    }
    return ESP_ERR_NOT_FOUND;
}


int wifi_red_mejor_visible(void)
{
    if (s_n == 0) return -1;
    if (s_n == 1) return 0;          // una sola: no hace falta escanear

    // Escaneo activo y bloqueante. Cuesta ~2 s, pero evita el bucle de
    // intentar conectarse a una red que no esta -- cada intento fallido son
    // varios segundos y aqui se hacen todos de una vez.
    wifi_scan_config_t cfg = { .show_hidden = false };
    if (esp_wifi_scan_start(&cfg, true) != ESP_OK) {
        ESP_LOGW(TAG, "el escaneo fallo; se prueba la primera guardada");
        return 0;
    }

    uint16_t n = 0;
    esp_wifi_scan_get_ap_num(&n);
    if (n == 0) { esp_wifi_clear_ap_list(); return 0; }
    if (n > 30) n = 30;              // de sobra, y acota la pila

    wifi_ap_record_t aps[30];
    if (esp_wifi_scan_get_ap_records(&n, aps) != ESP_OK) return 0;

    int mejor = -1, mejor_rssi = -127;
    for (int i = 0; i < s_n; i++) {
        for (int a = 0; a < n; a++) {
            if (strcmp((const char *)aps[a].ssid, s_redes[i].ssid)) continue;
            if (aps[a].rssi > mejor_rssi) { mejor_rssi = aps[a].rssi; mejor = i; }
        }
    }
    if (mejor >= 0)
        ESP_LOGI(TAG, "elegida '%s' (%d dBm)", s_redes[mejor].ssid, mejor_rssi);
    else
        ESP_LOGW(TAG, "ninguna de las %d redes guardadas esta al alcance", s_n);
    return mejor;
}
