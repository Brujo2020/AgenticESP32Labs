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
                    if (!strcmp(t->valuestring, "estado")) aplica_estado(v->valuestring);
                    else if (!strcmp(t->valuestring, "texto"))
                        snprintf(s_text, sizeof(s_text), "%s", v->valuestring);
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
