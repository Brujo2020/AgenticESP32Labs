// ============================================================
//  Conectividad: WiFi + hora real (SNTP) + clima (Open-Meteo)
//  Open-Meteo no necesita API key, por eso se usa aqui igual
//  que en el MCP de clima del backend.
// ============================================================
#include "net.h"
#include "wifi_redes.h"
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
#include "mdns.h"

// Las credenciales WiFi YA NO viven aqui.
//
// Estaban escritas en este fichero, que esta versionado: la contrasena del
// WiFi de casa acababa subida a GitHub en cada commit. Ahora se guardan en
// NVS, en el propio dispositivo, y se admiten varias (ver wifi_redes.h): la
// placa se conecta a la que encuentre, asi que funciona en casa y en el
// trabajo sin recompilar nada.
//
// Para sembrar la primera red en una placa recien flasheada se puede
// compilar una vez con:
//     idf.py build -DWIFI_SSID_DEFECTO='"MiRed"' -DWIFI_PASS_DEFECTO='"clave"'
// Solo se usa si NVS esta vacio, y no queda en el repositorio.
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

// Durante net_init se prueban varias redes a mano, y ahi la reconexion
// automatica del manejador ESTORBA: esp_wifi_disconnect() dispara
// STA_DISCONNECTED, el manejador llama a esp_wifi_connect() con la
// configuracion ANTERIOR, y esa reconexion se pisa con la que se acaba de
// pedir. En el log se ve como un bucle auth->assoc->run->init que nunca
// llega a IP: la placa se asocia una y otra vez pero el DHCP no completa.
//
// Se deja apagada mientras se elige red y se enciende al conectar, que es
// cuando reconectar solo si tiene sentido.
static bool s_autoreconecta = false;

static void on_evt(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        // Nada: quien decide a que red conectarse es net_init(). Antes se
        // conectaba aqui a lo que hubiera configurado, compitiendo con la
        // seleccion de red.
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        s_conn = false;
        if (!s_autoreconecta) {
            xEventGroupSetBits(s_evt, BIT_FAIL);   // lo gestiona intenta()
        } else if (s_retry < 5) {
            esp_wifi_connect(); s_retry++;
        } else {
            xEventGroupSetBits(s_evt, BIT_FAIL);
        }
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *e = (ip_event_got_ip_t *)data;
        snprintf(s_ip, sizeof(s_ip), IPSTR, IP2STR(&e->ip_info.ip));
        s_retry = 0; s_conn = true;
        xEventGroupSetBits(s_evt, BIT_OK);
    }
}

// Aplica una de las redes guardadas y espera a ver si conecta.
static bool intenta(int idx, int espera_ms)
{
    const wifi_red_t *r = wifi_red(idx);
    if (!r) return false;

    // memcpy y no snprintf: los campos de wifi_config_t son buffers de tamano
    // fijo (32 y 64 bytes) que NO exigen terminador nulo -- si el SSID ocupa
    // los 32, no hay sitio para el. snprintf siempre reserva uno, asi que con
    // -Werror=format-truncation el compilador avisa (con razon) de que puede
    // truncar. Con memset previo, lo que sobra queda a cero, que es lo que el
    // driver espera.
    wifi_config_t wc = {0};
    memcpy(wc.sta.ssid, r->ssid, strnlen(r->ssid, sizeof(wc.sta.ssid)));
    memcpy(wc.sta.password, r->pass, strnlen(r->pass, sizeof(wc.sta.password)));

    s_retry = 0;
    s_autoreconecta = false;          // que el manejador no se adelante
    esp_wifi_disconnect();
    // Un respiro para que el disconnect se procese ANTES de reconfigurar. Sin
    // esto, el evento de desconexion llega ya con la configuracion nueva
    // puesta y se mezclan los dos intentos.
    vTaskDelay(pdMS_TO_TICKS(200));
    xEventGroupClearBits(s_evt, BIT_OK | BIT_FAIL);
    if (esp_wifi_set_config(WIFI_IF_STA, &wc) != ESP_OK) return false;
    esp_wifi_connect();

    ESP_LOGI(TAG, "probando '%s'...", r->ssid);
    EventBits_t b = xEventGroupWaitBits(s_evt, BIT_OK | BIT_FAIL, pdFALSE, pdFALSE,
                                        pdMS_TO_TICKS(espera_ms));
    return (b & BIT_OK) != 0;
}

esp_err_t net_init(void)
{
    // NVS ya lo inicializa main antes de cargar los ajustes
    s_evt = xEventGroupCreate();
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    esp_event_handler_instance_register(WIFI_EVENT, ESP_EVENT_ANY_ID, &on_evt, NULL, NULL);
    esp_event_handler_instance_register(IP_EVENT, IP_EVENT_STA_GOT_IP, &on_evt, NULL, NULL);
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_start());

    // Las credenciales ya no viven en este fichero: se guardan en NVS y hay
    // varias. Asi la placa funciona en casa y en el trabajo sin reflashear,
    // y la contrasena deja de estar en el repositorio.
    wifi_redes_carga();
    if (wifi_redes_num() == 0) {
        ESP_LOGE(TAG, "no hay ninguna red WiFi configurada");
        return ESP_FAIL;
    }

    // Primero la de mejor senal de entre las que estan al alcance: evita
    // gastar 12 s intentando conectarse a una red que no esta presente.
    // 15 s: asociarse es rapido pero el DHCP de algunos routers tarda varios
    // segundos, y quedarse corto hace que se descarte una red que si
    // funcionaba. En el log se veia "assoc -> run" seguido de "run -> init":
    // asociado, pero sin darle tiempo a la IP.
    int mejor = wifi_red_mejor_visible();
    if (mejor >= 0 && intenta(mejor, 15000)) {
        s_autoreconecta = true;       // ya conectados: reconectar si se cae
        ESP_LOGI(TAG, "WiFi OK (%s), IP %s", wifi_red_ssid(mejor), s_ip);
        return ESP_OK;
    }

    // El escaneo puede fallar o mentir (redes ocultas, AP que no responde al
    // probe). Como respaldo se prueban todas por orden, con menos espera.
    for (int i = 0; i < wifi_redes_num(); i++) {
        if (i == mejor) continue;
        if (intenta(i, 12000)) {
            s_autoreconecta = true;
            ESP_LOGI(TAG, "WiFi OK (%s), IP %s", wifi_red_ssid(i), s_ip);
            return ESP_OK;
        }
    }

    // Aunque no haya conectado ahora, se deja la reconexion automatica puesta
    // con la ultima red probada: si el router estaba arrancando o la red
    // aparece mas tarde, la placa entra sola sin necesidad de reiniciarla.
    s_autoreconecta = true;
    esp_wifi_connect();
    ESP_LOGW(TAG, "ninguna de las %d redes guardadas conecto; se seguira "
                  "reintentando en segundo plano", wifi_redes_num());
    return ESP_FAIL;
}

const char *net_ssid_actual(void)
{
    wifi_ap_record_t ap;
    static char ssid[WIFI_SSID_MAX];
    if (s_conn && esp_wifi_sta_get_ap_info(&ap) == ESP_OK) {
        snprintf(ssid, sizeof(ssid), "%s", (const char *)ap.ssid);
        return ssid;
    }
    return "";
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


bool net_descubre_servidor(char *ip_out, int largo, int *puerto_out)
{
    if (!s_conn) return false;
    if (mdns_init() != ESP_OK) return false;
    mdns_hostname_set("mario-esp32");

    mdns_result_t *r = NULL;
    // Tres segundos son suficientes en una red domestica
    if (mdns_query_ptr("_hud", "_tcp", 3000, 4, &r) != ESP_OK || !r) {
        ESP_LOGW(TAG, "no se encontro ningun servidor por mDNS");
        return false;
    }
    bool ok = false;
    for (mdns_result_t *it = r; it; it = it->next) {
        if (it->addr) {
            snprintf(ip_out, largo, IPSTR, IP2STR(&it->addr->addr.u_addr.ip4));
            if (puerto_out) *puerto_out = it->port;
            ESP_LOGI(TAG, "servidor encontrado: %s:%d", ip_out, it->port);
            ok = true;
            break;
        }
    }
    mdns_query_results_free(r);
    return ok;
}
