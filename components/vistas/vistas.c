#include "vistas.h"
#include "display.h"
#include <string.h>
#include <stdio.h>
#include "cJSON.h"
#include "esp_log.h"
#include "esp_timer.h"

static const char *TAG = "vistas";

static vista_t s_v[VISTA_MAX];
static vistas_emisor_t s_emisor = NULL;

// ---- pregunta en curso ----
static bool s_preg = false;
static char s_preg_txt[VISTA_ANCHO * 2];
static char s_preg_qid[12];
static char s_preg_op[PREG_OPCIONES][12];
static int  s_preg_n = 0;
static int64_t s_preg_vence = 0;

// ---- notificacion ----
static char     s_noti[VISTA_ANCHO * 2];
static uint16_t s_noti_col = 0;
static int64_t  s_noti_hasta = 0;

static int64_t ahora(void) { return esp_timer_get_time() / 1000; }

void vistas_set_emisor(vistas_emisor_t fn) { s_emisor = fn; }

void vistas_init(void)
{
    memset(s_v, 0, sizeof(s_v));
    s_preg = false;
    s_noti_hasta = 0;
}

uint16_t vistas_color(const char *n, uint16_t d)
{
    if (!n) return d;
    if (!strcmp(n, "cyan"))    return C_CYAN;
    if (!strcmp(n, "magenta")) return C_MAGENTA;
    if (!strcmp(n, "lime"))    return C_LIME;
    if (!strcmp(n, "amber"))   return C_AMBER;
    if (!strcmp(n, "ice"))     return C_ICE;
    if (!strcmp(n, "blood"))   return C_BLOOD;
    if (!strcmp(n, "grey"))    return C_GREY;
    if (!strcmp(n, "white"))   return C_WHITE;
    return d;
}

// ============================================================
//  Carrusel
// ============================================================
static vista_t *busca(const char *id)
{
    for (int i = 0; i < VISTA_MAX; i++)
        if (s_v[i].usada && !strcmp(s_v[i].id, id)) return &s_v[i];
    return NULL;
}

static vista_t *hueco(void)
{
    for (int i = 0; i < VISTA_MAX; i++) if (!s_v[i].usada) return &s_v[i];
    return NULL;
}

void vistas_borra(const char *id)
{
    vista_t *v = busca(id);
    if (v) { v->usada = false; ESP_LOGI(TAG, "borrada '%s'", id); }
}

void vistas_purga(void)
{
    int64_t t = ahora();
    for (int i = 0; i < VISTA_MAX; i++)
        if (s_v[i].usada && s_v[i].expira_ms && t > s_v[i].expira_ms)
            s_v[i].usada = false;
}

int vistas_num(void)
{
    int n = 0;
    for (int i = 0; i < VISTA_MAX; i++) if (s_v[i].usada) n++;
    return n;
}

// Devuelve la i-esima viva ordenada por 'orden'. Insercion sobre un
// indice local: con 8 elementos es mas barato que mantener orden global,
// y sobre todo no muta el estado (una version anterior lo hacia y eso
// rompia si alguien iteraba dos veces en el mismo fotograma).
vista_t *vistas_get(int i)
{
    int idx[VISTA_MAX], n = 0;
    for (int k = 0; k < VISTA_MAX; k++) {
        if (!s_v[k].usada) continue;
        int p = n++;
        while (p > 0 && s_v[idx[p - 1]].orden > s_v[k].orden) {
            idx[p] = idx[p - 1]; p--;
        }
        idx[p] = k;
    }
    return (i >= 0 && i < n) ? &s_v[idx[i]] : NULL;
}

void vistas_toca(int indice, int fila)
{
    vista_t *v = vistas_get(indice);
    if (!v || !s_emisor) return;
    // 96 y no 64: la plantilla son 47 caracteres, mas 15 de id y los digitos
    // de fila. A 64 el margen era de un byte y dependia de que fila fuese
    // siempre de un digito. Un truncado aqui produce JSON invalido.
    char buf[96];
    snprintf(buf, sizeof(buf),
             "{\"t\":\"evento\",\"id\":\"%s\",\"accion\":\"toque\",\"fila\":%d}",
             v->id, fila);
    s_emisor(buf);
}

// ============================================================
//  Pregunta
// ============================================================
bool        preg_activa(void)       { return s_preg; }
const char *preg_texto(void)        { return s_preg_txt; }
int         preg_num_opciones(void) { return s_preg_n; }
const char *preg_opcion(int i)      { return (i >= 0 && i < s_preg_n) ? s_preg_op[i] : ""; }

int preg_segundos(void)
{
    if (!s_preg) return 0;
    int64_t r = (s_preg_vence - ahora()) / 1000;
    return r < 0 ? 0 : (int)r;
}

void preg_responde(int indice)
{
    if (!s_preg) return;
    if (s_emisor) {
        char buf[64];
        snprintf(buf, sizeof(buf), "{\"t\":\"respuesta\",\"qid\":\"%s\",\"opcion\":%d}",
                 s_preg_qid, indice);
        s_emisor(buf);
    }
    ESP_LOGI(TAG, "pregunta '%s' respondida con %d", s_preg_qid, indice);
    s_preg = false;
}

// El plazo lo vigila tambien el firmware: si se pierde el enlace, la
// pantalla no puede quedarse bloqueada esperando para siempre.
void preg_tick(void)
{
    if (s_preg && ahora() > s_preg_vence) preg_responde(-1);
    if (s_noti_hasta && ahora() > s_noti_hasta) s_noti_hasta = 0;
}

bool        noti_activa(void) { return s_noti_hasta != 0; }
const char *noti_texto(void)  { return s_noti; }
uint16_t    noti_color(void)  { return s_noti_col; }

// ============================================================
//  Entrada desde el servidor
// ============================================================
static void copia(char *dst, size_t n, const cJSON *j)
{
    if (cJSON_IsString(j) && j->valuestring) snprintf(dst, n, "%s", j->valuestring);
    else dst[0] = 0;
}

bool vistas_maneja(const char *tipo, const void *raiz)
{
    const cJSON *j = (const cJSON *)raiz;

    // -------- vista --------
    if (!strcmp(tipo, "vista")) {
        char id[VISTA_ID];
        copia(id, sizeof(id), cJSON_GetObjectItem(j, "id"));
        if (!id[0]) return true;

        vista_t *v = busca(id);
        if (!v) v = hueco();
        if (!v) { ESP_LOGW(TAG, "sin hueco para '%s'", id); return true; }

        memset(v, 0, sizeof(*v));
        v->usada = true;
        snprintf(v->id, sizeof(v->id), "%s", id);
        copia(v->titulo, sizeof(v->titulo), cJSON_GetObjectItem(j, "titulo"));
        const cJSON *ac = cJSON_GetObjectItem(j, "acento");
        v->acento = vistas_color(cJSON_IsString(ac) ? ac->valuestring : NULL, C_CYAN);
        // 'or' es palabra alternativa en C++ y macro de iso646: se evita
        const cJSON *ord = cJSON_GetObjectItem(j, "orden");
        v->orden = cJSON_IsNumber(ord) ? ord->valueint : 99;
        const cJSON *ttl = cJSON_GetObjectItem(j, "ttl");
        v->expira_ms = (cJSON_IsNumber(ttl) && ttl->valueint > 0)
                     ? ahora() + (int64_t)ttl->valueint * 1000 : 0;

        const cJSON *filas = cJSON_GetObjectItem(j, "filas");
        if (cJSON_IsArray(filas)) {
            const cJSON *f;
            cJSON_ArrayForEach(f, filas) {
                if (v->n_filas >= VISTA_FILAS) break;
                vista_fila_t *fl = &v->filas[v->n_filas++];
                if (cJSON_IsString(f)) {
                    snprintf(fl->txt, VISTA_ANCHO, "%s", f->valuestring);
                    fl->color = C_WHITE;
                } else {
                    copia(fl->txt, VISTA_ANCHO, cJSON_GetObjectItem(f, "txt"));
                    copia(fl->badge, sizeof(fl->badge), cJSON_GetObjectItem(f, "badge"));
                    const cJSON *c = cJSON_GetObjectItem(f, "color");
                    fl->color = vistas_color(cJSON_IsString(c) ? c->valuestring : NULL,
                                             C_WHITE);
                }
            }
        }
        ESP_LOGI(TAG, "vista '%s' (%s) con %d filas", v->id, v->titulo, v->n_filas);
        return true;
    }

    // -------- vista_borra --------
    if (!strcmp(tipo, "vista_borra")) {
        char id[VISTA_ID];
        copia(id, sizeof(id), cJSON_GetObjectItem(j, "id"));
        if (id[0]) vistas_borra(id);
        return true;
    }

    // -------- pregunta --------
    if (!strcmp(tipo, "pregunta")) {
        copia(s_preg_qid, sizeof(s_preg_qid), cJSON_GetObjectItem(j, "qid"));
        copia(s_preg_txt, sizeof(s_preg_txt), cJSON_GetObjectItem(j, "txt"));
        s_preg_n = 0;
        const cJSON *ops = cJSON_GetObjectItem(j, "opciones");
        if (cJSON_IsArray(ops)) {
            const cJSON *o;
            cJSON_ArrayForEach(o, ops) {
                if (s_preg_n >= PREG_OPCIONES) break;
                if (cJSON_IsString(o))
                    snprintf(s_preg_op[s_preg_n++], 12, "%s", o->valuestring);
            }
        }
        if (s_preg_n == 0) { snprintf(s_preg_op[0], 12, "OK"); s_preg_n = 1; }
        const cJSON *to = cJSON_GetObjectItem(j, "timeout");
        int seg = cJSON_IsNumber(to) ? to->valueint : 30;
        s_preg_vence = ahora() + (int64_t)seg * 1000;
        s_preg = true;
        ESP_LOGI(TAG, "pregunta '%s': %s (%d opciones, %ds)",
                 s_preg_qid, s_preg_txt, s_preg_n, seg);
        return true;
    }

    // -------- notifica --------
    if (!strcmp(tipo, "notifica")) {
        copia(s_noti, sizeof(s_noti), cJSON_GetObjectItem(j, "txt"));
        const cJSON *n = cJSON_GetObjectItem(j, "nivel");
        const char *niv = cJSON_IsString(n) ? n->valuestring : "info";
        s_noti_col = !strcmp(niv, "ok")    ? C_LIME
                   : !strcmp(niv, "warn")  ? C_AMBER
                   : !strcmp(niv, "error") ? C_BLOOD : C_CYAN;
        s_noti_hasta = ahora() + 4000;
        return true;
    }

    return false;
}
