// ============================================================
//  Conectividad: WiFi + hora real (SNTP) + clima (Open-Meteo)
//  Open-Meteo no necesita API key, por eso se usa aqui igual
//  que en el MCP de clima del backend.
// ============================================================
#include "net.h"
#include <string.h>
#include <time.h>
#include <sys/time.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/event_groups.h"
#include "esp_log.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_netif.h"
#include "esp_sntp.h"
#include "esp_http_client.h"
#include "nvs_flash.h"
#include "cJSON.h"

// ---- Ajusta esto a tu red y tu ubicacion ----
#define WIFI_SSID   "(:<BrUjO>:)"
#define WIFI_PASS   "_BrUjO_The_Best_2020+$$$!"
#define ZONA_HORARIA "<-05>5"          // UTC-5
#define LAT  "-12.05"
#define LON  "-77.04"

static const char *TAG = "net";
static EventGroupHandle_t s_evt;
#define BIT_OK   BIT0
#define BIT_FAIL BIT1

static bool s_conn = false;
static char s_ip[16] = "0.0.0.0";
static clima_t s_clima = {0};
static int s_retry = 0;

static void on_evt(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        s_conn = false;
        if (s_retry < 5) { esp_wifi_connect(); s_retry++; }
        else xEventGroupSetBits(s_evt, BIT_FAIL);
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *e = (ip_event_got_ip_t *)data;
        snprintf(s_ip, sizeof(s_ip), IPSTR, IP2STR(&e->ip_info.ip));
        s_retry = 0; s_conn = true;
        xEventGroupSetBits(s_evt, BIT_OK);
    }
}

esp_err_t net_init(void)
{
    esp_err_t r = nvs_flash_init();
    if (r == ESP_ERR_NVS_NO_FREE_PAGES || r == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        nvs_flash_erase(); nvs_flash_init();
    }
    s_evt = xEventGroupCreate();
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    esp_event_handler_instance_register(WIFI_EVENT, ESP_EVENT_ANY_ID, &on_evt, NULL, NULL);
    esp_event_handler_instance_register(IP_EVENT, IP_EVENT_STA_GOT_IP, &on_evt, NULL, NULL);

    wifi_config_t wc = { .sta = { .ssid = WIFI_SSID, .password = WIFI_PASS } };
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wc));
    ESP_ERROR_CHECK(esp_wifi_start());

    EventBits_t b = xEventGroupWaitBits(s_evt, BIT_OK | BIT_FAIL, pdFALSE, pdFALSE,
                                        pdMS_TO_TICKS(12000));
    if (b & BIT_OK) { ESP_LOGI(TAG, "WiFi OK, IP %s", s_ip); return ESP_OK; }
    ESP_LOGW(TAG, "WiFi no conecto");
    return ESP_FAIL;
}

bool net_connected(void) { return s_conn; }
const char *net_ip(void) { return s_ip; }

int net_rssi(void)
{
    wifi_ap_record_t ap;
    if (esp_wifi_sta_get_ap_info(&ap) == ESP_OK) return ap.rssi;
    return 0;
}

void net_sync_time(void)
{
    if (!s_conn) return;
    esp_sntp_setoperatingmode(ESP_SNTP_OPMODE_POLL);
    esp_sntp_setservername(0, "pool.ntp.org");
    esp_sntp_init();
    setenv("TZ", ZONA_HORARIA, 1);
    tzset();
    for (int i = 0; i < 15 && !net_time_valid(); i++) vTaskDelay(pdMS_TO_TICKS(500));
    ESP_LOGI(TAG, "hora %s", net_time_valid() ? "sincronizada" : "sin sincronizar");
}

bool net_time_valid(void)
{
    time_t now; time(&now);
    return now > 1700000000;      // cualquier fecha posterior a 2023
}

// Acumula el cuerpo de la respuesta HTTP
typedef struct { char *buf; int len; int cap; } resp_t;

static esp_err_t http_evt(esp_http_client_event_t *e)
{
    if (e->event_id == HTTP_EVENT_ON_DATA) {
        resp_t *r = (resp_t *)e->user_data;
        int n = e->data_len;
        if (r->len + n < r->cap) {
            memcpy(r->buf + r->len, e->data, n);
            r->len += n;
            r->buf[r->len] = 0;
        }
    }
    return ESP_OK;
}

void net_weather_update(void)
{
    if (!s_conn) return;
    static char buf[1024];
    resp_t r = { .buf = buf, .len = 0, .cap = sizeof(buf) };
    buf[0] = 0;

    esp_http_client_config_t cfg = {
        .url = "http://api.open-meteo.com/v1/forecast?latitude=" LAT
               "&longitude=" LON "&current=temperature_2m,wind_speed_10m,weather_code",
        .event_handler = http_evt,
        .user_data = &r,
        .timeout_ms = 8000,
    };
    esp_http_client_handle_t c = esp_http_client_init(&cfg);
    if (esp_http_client_perform(c) == ESP_OK) {
        cJSON *root = cJSON_Parse(buf);
        cJSON *cur = root ? cJSON_GetObjectItem(root, "current") : NULL;
        if (cur) {
            cJSON *t = cJSON_GetObjectItem(cur, "temperature_2m");
            cJSON *w = cJSON_GetObjectItem(cur, "wind_speed_10m");
            cJSON *k = cJSON_GetObjectItem(cur, "weather_code");
            if (t) s_clima.temp_c = (float)t->valuedouble;
            if (w) s_clima.viento_kmh = (float)w->valuedouble;
            if (k) s_clima.codigo = k->valueint;
            s_clima.valid = true;
            ESP_LOGI(TAG, "clima %.1fC viento %.0f cod %d",
                     s_clima.temp_c, s_clima.viento_kmh, s_clima.codigo);
        }
        if (root) cJSON_Delete(root);
    } else {
        ESP_LOGW(TAG, "fallo la peticion de clima");
    }
    esp_http_client_cleanup(c);
}

clima_t net_weather(void) { return s_clima; }

const char *net_weather_desc(int c)
{
    if (c == 0) return "DESPEJADO";
    if (c <= 2) return "POCAS NUBES";
    if (c == 3) return "NUBLADO";
    if (c <= 48) return "NIEBLA";
    if (c <= 57) return "LLOVIZNA";
    if (c <= 67) return "LLUVIA";
    if (c <= 77) return "NIEVE";
    if (c <= 82) return "CHUBASCOS";
    if (c <= 99) return "TORMENTA";
    return "N D";
}
