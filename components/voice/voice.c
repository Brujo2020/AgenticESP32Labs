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
#include "esp_timer.h"
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

// ---- Protocolo v2 ----
static vista_t s_vistas[VISTA_MAX];
static int     s_vistas_n = 0;

typedef struct {
    bool activa;
    char qid[13];
    char txt[VOZ_ANCHO * 2];
    char opciones[3][11];
    int  n_opciones;
    int64_t vence_ms;
} pregunta_t;
static pregunta_t s_pregunta = {0};

typedef struct {
    bool activa;
    char nivel[8];
    char txt[VOZ_ANCHO * 2];
    bool beep;
    int64_t oculta_ms;
} notifica_t;
static notifica_t s_notifica = {0};

#define FW_VERSION "0.8.0"

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

// ---- Vistas ----
static void vistas_expira(void);   // forward: usada por voice_vistas_num antes de definirse

int voice_vistas_num(void) { vistas_expira(); return s_vistas_n; }
const vista_t *voice_vista(int i)
{
    return (i >= 0 && i < s_vistas_n) ? &s_vistas[i] : NULL;
}

// Mantiene s_vistas[0..n) compacto y ordenado por 'orden'. Son a lo sumo 8
// elementos: una insercion ordenada de un array pequeno es mas simple y mas
// clara que traer un qsort para esto.
static void vista_inserta_ordenada(vista_t v)
{
    int i;
    for (i = 0; i < s_vistas_n; i++) {
        if (!strcmp(s_vistas[i].id, v.id)) { s_vistas[i] = v; goto ordena; }
    }
    if (s_vistas_n < VISTA_MAX) {
        s_vistas[s_vistas_n++] = v;
    } else {
        return;   // sin hueco: el guardia del servidor ya deberia haberlo evitado
    }
ordena:
    for (int a = 1; a < s_vistas_n; a++) {
        vista_t tmp = s_vistas[a];
        int b = a - 1;
        while (b >= 0 && s_vistas[b].orden > tmp.orden) {
            s_vistas[b + 1] = s_vistas[b];
            b--;
        }
        s_vistas[b + 1] = tmp;
    }
}

static void vista_borra_por_id(const char *id)
{
    for (int i = 0; i < s_vistas_n; i++) {
        if (!strcmp(s_vistas[i].id, id)) {
            for (int j = i; j < s_vistas_n - 1; j++) s_vistas[j] = s_vistas[j + 1];
            s_vistas_n--;
            return;
        }
    }
}

// TTL: se comprueba de forma perezosa (al listar), no con una tarea aparte.
static void vistas_expira(void)
{
    int64_t ahora = esp_timer_get_time() / 1000;
    for (int i = 0; i < s_vistas_n; ) {
        if (s_vistas[i].vence_ms > 0 && ahora >= s_vistas[i].vence_ms) {
            for (int j = i; j < s_vistas_n - 1; j++) s_vistas[j] = s_vistas[j + 1];
            s_vistas_n--;
        } else i++;
    }
}

static void parsea_vista(cJSON *j)
{
    vista_t v = {0};
    cJSON *id = cJSON_GetObjectItem(j, "id");
    cJSON *ti = cJSON_GetObjectItem(j, "titulo");
    cJSON *ac = cJSON_GetObjectItem(j, "acento");
    cJSON *or_ = cJSON_GetObjectItem(j, "orden");
    cJSON *ttl = cJSON_GetObjectItem(j, "ttl");
    cJSON *filas = cJSON_GetObjectItem(j, "filas");

    if (!cJSON_IsString(id) || !id->valuestring[0]) return;   // sin id no hay clave
    v.activa = true;
    snprintf(v.id, sizeof(v.id), "%s", id->valuestring);
    // Dos llamadas separadas, no una con ternario: con "cJSON_IsString(ti) ?
    // ti->valuestring : v.id" en una sola snprintf, GCC ve que la rama falsa
    // (v.id, 16 bytes) puede ser mas larga que el destino (11) y lo trata
    // como truncamiento seguro -Werror=format-truncation. Con precision
    // explicita en la rama de v.id, el compilador puede probar que nunca
    // se escribe mas de sizeof(v.titulo)-1.
    if (cJSON_IsString(ti)) snprintf(v.titulo, sizeof(v.titulo), "%s", ti->valuestring);
    else                    snprintf(v.titulo, sizeof(v.titulo), "%.*s", (int)sizeof(v.titulo) - 1, v.id);
    snprintf(v.acento, sizeof(v.acento), "%s", cJSON_IsString(ac) ? ac->valuestring : "cyan");
    v.orden = cJSON_IsNumber(or_) ? or_->valueint : 99;

    if (cJSON_IsArray(filas)) {
        cJSON *f;
        cJSON_ArrayForEach(f, filas) {
            if (v.n_filas >= VISTA_FILAS_MAX) break;
            vista_fila_t *fila = &v.filas[v.n_filas];
            if (cJSON_IsString(f)) {
                snprintf(fila->txt, sizeof(fila->txt), "%s", f->valuestring);
                snprintf(fila->color, sizeof(fila->color), "white");
            } else if (cJSON_IsObject(f)) {
                cJSON *txt = cJSON_GetObjectItem(f, "txt");
                cJSON *col = cJSON_GetObjectItem(f, "color");
                cJSON *bad = cJSON_GetObjectItem(f, "badge");
                snprintf(fila->txt, sizeof(fila->txt), "%s", cJSON_IsString(txt) ? txt->valuestring : "");
                snprintf(fila->color, sizeof(fila->color), "%s", cJSON_IsString(col) ? col->valuestring : "white");
                if (cJSON_IsString(bad) && bad->valuestring[0]) {
                    snprintf(fila->badge, sizeof(fila->badge), "%s", bad->valuestring);
                    fila->con_badge = true;
                }
            } else continue;
            v.n_filas++;
        }
    }
    if (cJSON_IsNumber(ttl) && ttl->valueint > 0)
        v.vence_ms = esp_timer_get_time() / 1000 + (int64_t)ttl->valueint * 1000;

    vista_inserta_ordenada(v);
}

// ---- Pregunta ----
bool voice_pregunta_activa(void)
{
    if (s_pregunta.activa && esp_timer_get_time() / 1000 >= s_pregunta.vence_ms) {
        voice_pregunta_responde(-1);   // vencio el plazo: como dice el protocolo,
                                       // el dispositivo responde -1, nunca se cuelga
        return false;
    }
    return s_pregunta.activa;
}
const char *voice_pregunta_txt(void) { return s_pregunta.txt; }
int voice_pregunta_num_opciones(void) { return s_pregunta.n_opciones; }
const char *voice_pregunta_opcion(int i)
{
    return (i >= 0 && i < s_pregunta.n_opciones) ? s_pregunta.opciones[i] : "";
}
int voice_pregunta_segundos_restantes(void)
{
    int64_t r = (s_pregunta.vence_ms - esp_timer_get_time() / 1000) / 1000;
    return r < 0 ? 0 : (int)r;
}

void voice_pregunta_responde(int opcion)
{
    if (!s_pregunta.activa) return;
    char msg[64];
    snprintf(msg, sizeof(msg), "{\"t\":\"respuesta\",\"qid\":\"%s\",\"opcion\":%d}",
             s_pregunta.qid, opcion);
    if (s_conn) esp_websocket_client_send_text(s_ws, msg, strlen(msg), pdMS_TO_TICKS(200));
    s_pregunta.activa = false;
}

static void parsea_pregunta(cJSON *j)
{
    cJSON *qid = cJSON_GetObjectItem(j, "qid");
    cJSON *txt = cJSON_GetObjectItem(j, "txt");
    cJSON *ops = cJSON_GetObjectItem(j, "opciones");
    cJSON *to  = cJSON_GetObjectItem(j, "timeout");
    if (!cJSON_IsString(qid) || !cJSON_IsString(txt)) return;

    memset(&s_pregunta, 0, sizeof(s_pregunta));
    snprintf(s_pregunta.qid, sizeof(s_pregunta.qid), "%s", qid->valuestring);
    snprintf(s_pregunta.txt, sizeof(s_pregunta.txt), "%s", txt->valuestring);
    if (cJSON_IsArray(ops)) {
        cJSON *o;
        cJSON_ArrayForEach(o, ops) {
            if (s_pregunta.n_opciones >= 3) break;
            if (cJSON_IsString(o))
                snprintf(s_pregunta.opciones[s_pregunta.n_opciones++],
                         sizeof(s_pregunta.opciones[0]), "%s", o->valuestring);
        }
    }
    if (s_pregunta.n_opciones == 0) {
        snprintf(s_pregunta.opciones[0], sizeof(s_pregunta.opciones[0]), "SI");
        snprintf(s_pregunta.opciones[1], sizeof(s_pregunta.opciones[1]), "NO");
        s_pregunta.n_opciones = 2;
    }
    int timeout = cJSON_IsNumber(to) ? to->valueint : 30;
    s_pregunta.vence_ms = esp_timer_get_time() / 1000 + (int64_t)timeout * 1000;
    s_pregunta.activa = true;
}

// ---- Notifica ----
bool voice_notifica_activa(void)
{
    if (s_notifica.activa && esp_timer_get_time() / 1000 >= s_notifica.oculta_ms) {
        s_notifica.activa = false;
    }
    return s_notifica.activa;
}
const char *voice_notifica_txt(void) { return s_notifica.txt; }
const char *voice_notifica_nivel(void) { return s_notifica.nivel; }

static void parsea_notifica(cJSON *j)
{
    cJSON *txt = cJSON_GetObjectItem(j, "txt");
    cJSON *niv = cJSON_GetObjectItem(j, "nivel");
    cJSON *bp  = cJSON_GetObjectItem(j, "beep");
    if (!cJSON_IsString(txt)) return;
    snprintf(s_notifica.txt, sizeof(s_notifica.txt), "%s", txt->valuestring);
    snprintf(s_notifica.nivel, sizeof(s_notifica.nivel), "%s", cJSON_IsString(niv) ? niv->valuestring : "info");
    s_notifica.beep = cJSON_IsBool(bp) && cJSON_IsTrue(bp);
    s_notifica.activa = true;
    s_notifica.oculta_ms = esp_timer_get_time() / 1000 + 4000;   // 4 s, ver PROTOCOLO.md
}

static bool s_v2 = false;
bool voice_v2_activo(void) { return s_v2; }

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
    case WEBSOCKET_EVENT_CONNECTED: {
        s_conn = true;  ESP_LOGI(TAG, "conectado");
        // Handshake v2: el servidor solo activa el protocolo declarativo si
        // recibe esto. Sin 'hola' se degrada solo a los tres canales fijos
        // (ver servidor/nucleo/canal.py, Canal.v2) y este firmware seguiria
        // funcionando igual, pero sin vistas/pregunta/notifica.
        char hola[96];
        int n = snprintf(hola, sizeof(hola),
                          "{\"t\":\"hola\",\"fw\":\"%s\",\"vistas_max\":%d,"
                          "\"filas_max\":%d,\"ancho\":%d}",
                          FW_VERSION, VISTA_MAX, VISTA_FILAS_MAX, VOZ_ANCHO - 1);
        esp_websocket_client_send_text(s_ws, hola, n, pdMS_TO_TICKS(200));
        s_v2 = true;
        break;
    }
    case WEBSOCKET_EVENT_DISCONNECTED:
        s_conn = false; ESP_LOGW(TAG, "desconectado"); break;
    case WEBSOCKET_EVENT_DATA:
        if (e->op_code == 0x02) {                 // binario: audio de vuelta
            audio_play_pcm(e->data_ptr, e->data_len);
        } else if (e->op_code == 0x01 && e->data_len > 2) {
            cJSON *j = cJSON_ParseWithLength(e->data_ptr, e->data_len);
            if (j) {
                cJSON *t = cJSON_GetObjectItem(j, "t");
                const char *tipo = cJSON_IsString(t) ? t->valuestring : "";
                // Protocolo v2: mensajes con forma propia, no {"t","v"}.
                if (!strcmp(tipo, "vista")) {
                    parsea_vista(j);
                } else if (!strcmp(tipo, "vista_borra")) {
                    cJSON *id = cJSON_GetObjectItem(j, "id");
                    if (cJSON_IsString(id)) vista_borra_por_id(id->valuestring);
                } else if (!strcmp(tipo, "pregunta")) {
                    parsea_pregunta(j);
                } else if (!strcmp(tipo, "notifica")) {
                    parsea_notifica(j);
                } else {
                cJSON *v = cJSON_GetObjectItem(j, "v");
                if (cJSON_IsString(t) && cJSON_IsString(v)) {
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
    // OJO: iba con la longitud a mano puesta a 10, un byte corto de los 11
    // reales ("{\"t\":\"fin\"}") -- mandaba "{"t":"fin"" sin cerrar. El
    // servidor no podia parsear ese JSON truncado y tumbaba la conexion en
    // cuanto alguien soltaba el boton de hablar. strlen() en vez de un
    // numero fijo: no puede volver a desincronizarse del literal.
    static const char msg[] = "{\"t\":\"fin\"}";
    esp_websocket_client_send_text(s_ws, msg, strlen(msg), portMAX_DELAY);

    // Pitido corto de confirmacion: "ya se envio, esperando respuesta".
    // Sin esto no habia ninguna senal audible de que soltar el boton hizo
    // algo -- el usuario no sabe si el mensaje se mando o si el gesto fallo,
    // hasta que (si acaso) llega la respuesta varios segundos despues.
    audio_beep(1200, 90);
}
