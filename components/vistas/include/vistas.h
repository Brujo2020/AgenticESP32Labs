// ============================================================
//  Vistas declarativas + preguntas + notificaciones
//
//  El firmware deja de saber QUE pantallas existen: sabe pintar
//  cualquier vista que le describan. Anadir una funcion pasa a ser
//  un .py en el servidor, no un ciclo de compilar y flashear.
//
//  Todos los buffers son estaticos y estan topeados a proposito.
//  En un MCU no se reserva memoria por mensaje entrante: el ancho
//  y los maximos se anuncian en el handshake y el servidor los
//  respeta. Coste total ~1.4 KB, sobre los 115 KB del framebuffer.
// ============================================================
#pragma once
#include <stdint.h>
#include <stdbool.h>

#define VISTA_MAX     8      // vistas simultaneas en el carrusel
#define VISTA_FILAS   6      // filas por vista
#define VISTA_ANCHO   27     // 26 caracteres visibles + NUL
#define VISTA_TITULO  11
#define VISTA_ID      16
#define PREG_OPCIONES 3

typedef struct {
    char     txt[VISTA_ANCHO];
    char     badge[4];
    uint16_t color;
} vista_fila_t;

typedef struct {
    char         id[VISTA_ID];
    char         titulo[VISTA_TITULO];
    uint16_t     acento;
    int          orden;
    vista_fila_t filas[VISTA_FILAS];
    int          n_filas;
    int64_t      expira_ms;      // 0 = permanente
    bool         usada;
} vista_t;

// El componente no conoce el WebSocket: quien lo tenga registra aqui
// como enviar texto de vuelta. Evita que vistas dependa de voice.
typedef void (*vistas_emisor_t)(const char *json);
void vistas_set_emisor(vistas_emisor_t fn);

void vistas_init(void);

// ---- Entrada: un mensaje JSON ya parseado del servidor ----
// Devuelve true si el mensaje era suyo y lo consumio.
bool vistas_maneja(const char *tipo, const void *json_raiz);

// ---- Carrusel ----
void      vistas_purga(void);        // caduca las vistas con ttl vencido
int       vistas_num(void);          // vistas vivas, ordenadas por 'orden'
vista_t  *vistas_get(int i);
void      vistas_borra(const char *id);
void      vistas_toca(int indice_vista, int fila);   // emite {"t":"evento"}

// ---- Pregunta bloqueante (human-in-the-loop) ----
bool        preg_activa(void);
const char *preg_texto(void);
int         preg_num_opciones(void);
const char *preg_opcion(int i);
int         preg_segundos(void);     // restantes; 0 = vencida
void        preg_responde(int indice);   // -1 = sin respuesta
void        preg_tick(void);         // llamar cada fotograma: vence sola

// ---- Notificacion efimera ----
bool        noti_activa(void);
const char *noti_texto(void);
uint16_t    noti_color(void);

// Traduce "cyan"/"amber"/... al RGB565 de la paleta
uint16_t vistas_color(const char *nombre, uint16_t por_defecto);
