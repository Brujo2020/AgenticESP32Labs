// ============================================================
//  Voz: cliente WebSocket contra el puente del Mac.
//  Pulsar para hablar: mientras el dedo esta en la pantalla VOZ
//  el microfono se transmite en crudo (PCM 16-bit mono 24 kHz).
//  El servidor devuelve estado, texto y el audio de la respuesta.
// ============================================================
#include "voice.h"
#include "audio.h"
#include "board_pins.h"
#include <string.h>
#include <stdio.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_websocket_client.h"
#include "cJSON.h"

static const char *TAG = "voice";
static esp_websocket_client_handle_t s_ws = NULL;
static volatile bool s_talking = false;
static volatile bool s_conn = false;
static char s_text[48] = "";
static voice_state_t s_state = VOZ_IDLE;

// Historial de conversacion y titulares. Buffers fijos: en un
// microcontrolador no conviene reservar memoria en cada mensaje.
static char s_hist[VOZ_LINEAS][VOZ_ANCHO];
static bool s_hist_mio[VOZ_LINEAS];
static int  s_hist_n = 0;
static char s_news[VOZ_NOTICIAS][VOZ_ANCHO];
static int  s_news_n = 0;
static char s_mac[VOZ_TELE][VOZ_ANCHO];
static int  s_mac_n = 0;
static char s_cre[VOZ_TELE][VOZ_ANCHO];
static int  s_cre_n = 0;

static void hist_push(const char *txt, bool mio)
{
    if (s_hist_n == VOZ_LINEAS) {          // lleno: desplaza y descarta la vieja
        for (int i = 0; i < VOZ_LINEAS - 1; i++) {
            memcpy(s_hist[i], s_hist[i+1], VOZ_ANCHO);
            s_hist_mio[i] = s_hist_mio[i+1];
        }
        s_hist_n--;
    }
    snprintf(s_hist[s_hist_n], VOZ_ANCHO, "%s", txt);
    s_hist_mio[s_hist_n] = mio;
    s_hist_n++;
}

int  voice_hist_num(void)      { return s_hist_n; }
const char *voice_hist(int i)  { return (i >= 0 && i < s_hist_n) ? s_hist[i] : ""; }
bool voice_hist_es_mio(int i)  { return (i >= 0 && i < s_hist_n) ? s_hist_mio[i] : false; }
int  voice_mac_num(void)       { return s_mac_n; }
const char *voice_mac(int i)   { return (i >= 0 && i < s_mac_n) ? s_mac[i] : ""; }
int  voice_creativo_num(void)  { return s_cre_n; }
const char *voice_creativo(int i) { return (i >= 0 && i < s_cre_n) ? s_cre[i] : ""; }
int  voice_news_num(void)      { return s_news_n; }
const char *voice_news(int i)  { return (i >= 0 && i < s_news_n) ? s_news[i] : ""; }

static void aplica_estado(const char *v)
{
    if      (!strcmp(v, "listening"))  s_state = VOZ_ESCUCHANDO;
    else if (!strcmp(v, "processing")) s_state = VOZ_PENSANDO;
    else if (!strcmp(v, "speaking"))   s_state = VOZ_HABLANDO;
    else if (!strcmp(v, "error"))      s_state = VOZ_ERROR;
    else                               s_state = VOZ_IDLE;
}

voice_state_t voice_state(void) { return s_state; }

static void on_ws(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    esp_websocket_event_data_t *e = (esp_websocket_event_data_t *)data;
    switch (id) {
    case WEBSOCKET_EVENT_CONNECTED:
        s_conn = true;  ESP_LOGI(TAG, "conectado");  break;
    case WEBSOCKET_EVENT_DISCONNECTED:
        s_conn = false; ESP_LOGW(TAG, "desconectado"); break;
    case WEBSOCKET_EVENT_DATA:
        if (e->op_code == 0x02) {                 // binario: audio de vuelta
            audio_play_pcm(e->data_ptr, e->data_len);
        } else if (e->op_code == 0x01 && e->data_len > 2) {
            cJSON *j = cJSON_ParseWithLength(e->data_ptr, e->data_len);
            if (j) {
                cJSON *t = cJSON_GetObjectItem(j, "t");
                cJSON *v = cJSON_GetObjectItem(j, "v");
                if (cJSON_IsString(t) && cJSON_IsString(v)) {
                    const char *tipo = t->valuestring;
                    if (!strcmp(tipo, "estado")) {
                        aplica_estado(v->valuestring);
                    } else if (!strcmp(tipo, "texto")) {
                        snprintf(s_text, sizeof(s_text), "%s", v->valuestring);
                    } else if (!strcmp(tipo, "tu")) {          // lo que se entendio
                        hist_push(v->valuestring, true);
                    } else if (!strcmp(tipo, "ia")) {          // lo que responde
                        hist_push(v->valuestring, false);
                    } else if (!strcmp(tipo, "noticia")) {     // titular
                        if (s_news_n < VOZ_NOTICIAS)
                            snprintf(s_news[s_news_n++], VOZ_ANCHO, "%s", v->valuestring);
                    } else if (!strcmp(tipo, "noticias_reset")) {
                        s_news_n = 0;
                    } else if (!strcmp(tipo, "mac")) {
                        if (s_mac_n < VOZ_TELE)
                            snprintf(s_mac[s_mac_n++], VOZ_ANCHO, "%s", v->valuestring);
                    } else if (!strcmp(tipo, "mac_reset")) {
                        s_mac_n = 0;
                    } else if (!strcmp(tipo, "creativo")) {
                        if (s_cre_n < VOZ_TELE)
                            snprintf(s_cre[s_cre_n++], VOZ_ANCHO, "%s", v->valuestring);
                    } else if (!strcmp(tipo, "creativo_reset")) {
                        s_cre_n = 0;
                    }
                }
                cJSON_Delete(j);
            }
        }
        break;
    default: break;
    }
}

// Bombea el microfono al socket mientras se mantiene el dedo
static void tarea_mic(void *arg)
{
    static int16_t buf[512];
    while (1) {
        if (s_talking && s_conn) {
            size_t got = audio_mic_read(buf, sizeof(buf), 100);
            if (got) esp_websocket_client_send_bin(s_ws, (const char *)buf, got, portMAX_DELAY);
        } else {
            vTaskDelay(pdMS_TO_TICKS(20));
        }
    }
}

esp_err_t voice_init(const char *host, int port)
{
    static char uri[64];
    snprintf(uri, sizeof(uri), "ws://%s:%d", host, port);
    esp_websocket_client_config_t cfg = {
        .uri = uri,
        .reconnect_timeout_ms = 5000,
        .network_timeout_ms = 8000,
    };
    s_ws = esp_websocket_client_init(&cfg);
    if (!s_ws) return ESP_FAIL;
    esp_websocket_register_events(s_ws, WEBSOCKET_EVENT_ANY, on_ws, NULL);
    esp_err_t r = esp_websocket_client_start(s_ws);
    xTaskCreate(tarea_mic, "mic_ws", 4096, NULL, 5, NULL);
    ESP_LOGI(TAG, "puente en %s", uri);
    return r;
}

bool voice_connected(void) { return s_conn; }
bool voice_talking(void)   { return s_talking; }
const char *voice_text(void) { return s_text; }

void voice_talk_start(void)
{
    if (!s_conn) return;
    s_talking = true;
    s_state = VOZ_ESCUCHANDO;
    snprintf(s_text, sizeof(s_text), "TE ESCUCHO");
}

void voice_talk_stop(void)
{
    if (!s_talking) return;
    s_talking = false;
    esp_websocket_client_send_text(s_ws, "{\"t\":\"fin\"}", 10, portMAX_DELAY);
}
