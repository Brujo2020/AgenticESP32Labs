// ============================================================
//  Voz: cliente WebSocket contra el puente del Mac.
//  Pulsar para hablar: mientras el dedo esta en la pantalla VOZ
//  el microfono se transmite en crudo (PCM 16-bit mono 24 kHz).
//  El servidor devuelve estado, texto y el audio de la respuesta.
// ============================================================
#include "voice.h"
#include "audio.h"
#include "board_pins.h"
#include "ajustes.h"
#include "version.h"
#include "bateria.h"
#include "net.h"
#include "esp_heap_caps.h"
#include <string.h>
#include <stdio.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/stream_buffer.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_system.h"
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

// ============================================================
//  Reproduccion de audio: la red NO toca el I2S directamente
// ============================================================
// Antes on_ws() llamaba a audio_play_pcm() dentro del propio manejador de
// eventos del websocket. i2s_channel_write bloquea hasta que el DMA acepta
// las muestras (~42 ms por cada trozo de 2048 bytes a 24 kHz), y durante ese
// rato la tarea del websocket no lee del socket. Entre trozo y trozo el
// buffer DMA se vaciaba -> underrun -> un chasquido por trozo. En el altavoz
// se oia como un "ti-ti-ti-ti" rapido encima de la voz.
//
// Ahora el manejador solo copia a un stream buffer (no bloquea) y una tarea
// aparte alimenta el I2S de forma continua. El colchon absorbe el jitter de
// la red, que es justo lo que faltaba.
#define AUDIO_BUF_BYTES   (16 * 1024)   // ~340 ms a 24 kHz 16-bit mono
#define AUDIO_PREBUFFER   (4 * 1024)    // no empieza hasta tener este colchon
#define AUDIO_TROZO       2048

static StreamBufferHandle_t s_audio_sb = NULL;
static volatile bool s_audio_desborde = false;
// Lo pone aplica_estado() al recibir "idle": el servidor ya no manda mas
// audio, asi que en cuanto se vacie el colchon hay que callar el DMA sin
// esperar al timeout. Ver tarea_audio.
static volatile bool s_fin_audio = false;

static void tarea_audio(void *arg)
{
    static uint8_t buf[AUDIO_TROZO];
    bool sonando = false;

    while (1) {
        if (!sonando) {
            // Arranque de racha: esperar un colchon antes del primer trozo.
            // Empezar con el buffer casi vacio garantiza un underrun en la
            // primera decima de segundo de cada respuesta.
            //
            // El colchon se espera a mano y no con el trigger level del
            // stream buffer: el trigger se aplica a TODAS las lecturas, y
            // entonces los ultimos bytes de una respuesta (casi siempre
            // menos de AUDIO_PREBUFFER) se quedarian atascados hasta la
            // siguiente -- el final de una frase se oiria pegado al
            // principio de la siguiente.
            int espera = 0;
            while (xStreamBufferBytesAvailable(s_audio_sb) < AUDIO_PREBUFFER
                   && espera < 25) {                    // 250 ms como mucho
                vTaskDelay(pdMS_TO_TICKS(10));
                espera++;
            }
            sonando = true;
        }

        // 40 ms, no 150: mientras se espera, el DMA sigue repitiendo en bucle
        // el ultimo trozo, y eso se oye como un zumbido al final de cada
        // frase. Con el colchon de 340 ms por delante, 40 ms de hueco ya
        // significan "se acabo" sin riesgo de cortar por un jitter de red.
        size_t n = xStreamBufferReceive(s_audio_sb, buf, sizeof(buf),
                                        pdMS_TO_TICKS(40));
        if (n) {
            audio_play_pcm(buf, n);
        } else if (sonando) {
            // Fin de la racha: callar el DMA. Sin esto se queda repitiendo en
            // bucle el final de la frase (ver audio_silencio en audio.c).
            audio_silencio();
            sonando = false;
        }

        // El servidor avisa con estado "idle" cuando ya no queda audio por
        // mandar. Si ademas el colchon esta vacio, se puede callar YA sin
        // esperar ningun timeout: es la via rapida para el caso normal.
        if (s_fin_audio && !xStreamBufferBytesAvailable(s_audio_sb)) {
            s_fin_audio = false;
            if (sonando) { audio_silencio(); sonando = false; }
        }

        if (s_audio_desborde) {
            s_audio_desborde = false;
            ESP_LOGW(TAG, "audio: se descarto un trozo (llega mas rapido de lo "
                          "que el altavoz puede reproducir)");
        }
    }
}

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

// FW_VERSION vive ahora en board/include/version.h, junto a la fecha y hora
// de compilacion: la usan el handshake de aqui y la pantalla de AJUSTES, y
// con la definicion duplicada era cuestion de tiempo que dijeran cosas
// distintas.

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
    // "speaking" -> empieza a llegar audio; "idle"/"error" -> ya no llega mas.
    // Saberlo permite callar el altavoz en cuanto se vacie el colchon, en vez
    // de esperar un timeout con el DMA repitiendo el final de la frase.
    if (!strcmp(v, "speaking")) s_fin_audio = false;
    else                        s_fin_audio = true;

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
        // Estado real de la placa nada mas conectar: asi el panel muestra lo
        // que el aparato tiene de verdad desde el primer segundo, sin esperar
        // al reporte periodico.
        voice_reporta_estado();
        break;
    }
    case WEBSOCKET_EVENT_DISCONNECTED:
        s_conn = false; ESP_LOGW(TAG, "desconectado"); break;
    case WEBSOCKET_EVENT_DATA:
        if (e->op_code == 0x02 || e->op_code == 0x00) {
            // Binario (0x02) o continuacion de un binario fragmentado (0x00):
            // audio de vuelta. Solo se encola -- reproducir aqui bloquearia
            // la lectura del socket y provocaria los chasquidos.
            if (s_audio_sb && e->data_len > 0) {
                size_t puesto = xStreamBufferSend(s_audio_sb, e->data_ptr,
                                                  e->data_len, 0);
                if (puesto < (size_t)e->data_len) s_audio_desborde = true;
            }
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
                } else if (!strcmp(tipo, "config")) {
                    // Ajustes remotos desde el panel web (servidor/panel_web).
                    // Campos opcionales: solo se toca lo que venga presente.
                    ajustes_t *a = ajustes();
                    cJSON *br = cJSON_GetObjectItem(j, "brillo");
                    cJSON *vo = cJSON_GetObjectItem(j, "volumen");
                    cJSON *te = cJSON_GetObjectItem(j, "tema");
                    cJSON *ef = cJSON_GetObjectItem(j, "efectos");
                    if (cJSON_IsNumber(br)) a->brillo  = br->valueint < 0 ? 0 : (br->valueint > 100 ? 100 : br->valueint);
                    if (cJSON_IsNumber(vo)) a->volumen = vo->valueint < 0 ? 0 : (vo->valueint > 100 ? 100 : vo->valueint);
                    if (cJSON_IsNumber(te)) a->tema    = te->valueint < 0 ? 0 : (te->valueint > 7 ? 7 : te->valueint);
                    if (cJSON_IsBool(ef))   a->efectos = cJSON_IsTrue(ef);
                    ajustes_aplicar();
                    ajustes_guardar();
                    ESP_LOGI(TAG, "config remota aplicada: brillo=%d volumen=%d tema=%d efectos=%d",
                             a->brillo, a->volumen, a->tema, a->efectos);
                } else if (!strcmp(tipo, "pantallas")) {
                    // Carrusel configurado desde el panel web:
                    //   {"t":"pantallas","activas":[0,1,3,...],"orden":[...]}
                    // 'activas' son los indices de pantalla fija visibles;
                    // 'orden' la secuencia de recorrido. Se persiste en NVS,
                    // asi que sobrevive al reinicio del ESP32.
                    ajustes_t *a = ajustes();
                    cJSON *act = cJSON_GetObjectItem(j, "activas");
                    cJSON *ord = cJSON_GetObjectItem(j, "orden");
                    if (cJSON_IsArray(act)) {
                        unsigned short m = 0;
                        cJSON *it = NULL;
                        cJSON_ArrayForEach(it, act) {
                            if (cJSON_IsNumber(it) && it->valueint >= 0 &&
                                it->valueint < AJ_PANTALLAS_FIJAS)
                                m |= (unsigned short)(1u << it->valueint);
                        }
                        a->mascara = m;
                    }
                    if (cJSON_IsArray(ord)) {
                        signed char nuevo[AJ_PANTALLAS_FIJAS];
                        bool visto[AJ_PANTALLAS_FIJAS] = {0};
                        int n = 0;
                        cJSON *it = NULL;
                        cJSON_ArrayForEach(it, ord) {
                            int v = cJSON_IsNumber(it) ? it->valueint : -1;
                            if (v < 0 || v >= AJ_PANTALLAS_FIJAS || visto[v]) continue;
                            visto[v] = true;
                            nuevo[n++] = (signed char)v;
                        }
                        // Lo que el panel no haya listado se conserva al final:
                        // un orden parcial no puede hacer desaparecer pantallas.
                        for (int v = 0; v < AJ_PANTALLAS_FIJAS && n < AJ_PANTALLAS_FIJAS; v++)
                            if (!visto[v]) nuevo[n++] = (signed char)v;
                        memcpy(a->orden, nuevo, sizeof(a->orden));
                    }
                    ajustes_guardar();
                    ESP_LOGI(TAG, "carrusel actualizado: mascara=0x%03X", a->mascara);
                } else if (!strcmp(tipo, "reiniciar")) {
                    ESP_LOGW(TAG, "reinicio remoto pedido desde el panel");
                    cJSON_Delete(j);
                    vTaskDelay(pdMS_TO_TICKS(300));   // deja salir el log
                    esp_restart();
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
// Publica el estado REAL de la placa hacia el servidor.
//
// Sin esto el panel web solo sabia lo ultimo que el habia mandado, no lo que
// el aparato tiene de verdad: si alguien cambiaba el brillo en la pantalla de
// AJUSTES, o la placa se reiniciaba y cargaba otros valores de NVS, el panel
// seguia mostrando lo suyo. Con este mensaje la sincronizacion es en los dos
// sentidos y el panel puede mostrar el estado autentico.
void voice_reporta_estado(void)
{
    if (!s_conn || !s_ws) return;
    ajustes_t *a = ajustes();
    char m[240];
    int n = snprintf(m, sizeof(m),
        "{\"t\":\"estado_disp\",\"fw\":\"%s\",\"compilado\":\"%s %s\","
        "\"brillo\":%d,\"volumen\":%d,\"tema\":%d,\"efectos\":%s,"
        "\"bateria\":%d,\"cargando\":%s,\"heap\":%u,\"rssi\":%d}",
        FW_VERSION, FW_FECHA, FW_HORA,
        a->brillo, a->volumen, a->tema, a->efectos ? "true" : "false",
        bateria_disponible() ? bateria_pct() : -1,
        bateria_cargando() ? "true" : "false",
        (unsigned)esp_get_free_heap_size(), net_rssi());
    if (n > 0 && n < (int)sizeof(m))
        esp_websocket_client_send_text(s_ws, m, n, pdMS_TO_TICKS(200));
}

static void tarea_mic(void *arg)
{
    static int16_t buf[512];
    int ciclos = 0;
    while (1) {
        if (s_talking && s_conn) {
            size_t got = audio_mic_read(buf, sizeof(buf), 100);
            if (got) esp_websocket_client_send_bin(s_ws, (const char *)buf, got, portMAX_DELAY);
        } else {
            vTaskDelay(pdMS_TO_TICKS(20));
            // Cada ~4 s se publica el estado. Se aprovecha esta tarea en vez
            // de crear otra: ya esta despierta y ociosa cuando no se habla.
            if (++ciclos >= 200) { ciclos = 0; voice_reporta_estado(); }
        }
    }
}

esp_err_t voice_init(const char *host, int port)
{
    static char uri[64];
    snprintf(uri, sizeof(uri), "ws://%s:%d", host, port);
    esp_websocket_client_config_t cfg = {
        .uri = uri,
        // Reconexion agresiva: 2 s en vez de 5. Si el enlace se cae, lo que
        // se nota es el tiempo que la placa tarda en volver.
        .reconnect_timeout_ms = 2000,
        .network_timeout_ms = 8000,
        // PING/PONG del propio protocolo websocket. Es lo que evita las
        // conexiones zombi: cuando el enlace se corta de forma sucia (WiFi
        // que se va, NAT que caduca la sesion, servidor reiniciado) el TCP
        // puede quedarse "abierto" para el ESP32 durante minutos, y el
        // firmware cree estar conectado mientras el servidor ya no lo ve.
        // Ese es exactamente el estado en el que el panel dice "SIN
        // DISPOSITIVO" aunque la placa parezca estar bien.
        // Con esto, si en 15 s no llega el PONG, el cliente da la conexion
        // por muerta y reconecta solo.
        .ping_interval_sec = 10,
        .pingpong_timeout_sec = 15,
        .disable_auto_reconnect = false,
        // El bridge manda el audio en trozos de 2048 bytes. Con el buffer por
        // defecto (1024) cada trozo llegaba partido en dos eventos, doblando
        // el numero de despertares por segundo de audio sin necesidad.
        .buffer_size = 4096,
    };
    s_ws = esp_websocket_client_init(&cfg);
    if (!s_ws) return ESP_FAIL;

    // El colchon de audio y su tarea, antes de arrancar el cliente: si el
    // servidor empieza a mandar audio de inmediato, tienen que existir ya.
    // Trigger level 1: se lee en cuanto haya algo. El colchon de arranque lo
    // gestiona tarea_audio (ver alli por que no se usa el trigger level).
    s_audio_sb = xStreamBufferCreate(AUDIO_BUF_BYTES, 1);
    if (!s_audio_sb) {
        ESP_LOGE(TAG, "sin memoria para el buffer de audio");
        return ESP_ERR_NO_MEM;
    }
    xTaskCreate(tarea_audio, "audio_ws", 3072, NULL, 6, NULL);

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
    if (ajustes()->efectos) audio_beep(1200, 90);
}
