#pragma once

// Version del firmware, en un solo sitio.
//
// Antes vivia suelta en components/voice/voice.c como FW_VERSION, que es un
// sitio raro para buscarla: la usa el handshake del protocolo v2, pero
// tambien la pantalla de AJUSTES y cualquiera que quiera saber que hay
// flasheado. Tenerla aqui evita que dos ficheros digan versiones distintas.
#define FW_VERSION "0.8.0"

// Fecha y hora de COMPILACION, puestas por el preprocesador.
// __DATE__ da "Aug 16 2026" (siempre en ingles y con el dia alineado a dos
// caracteres) y __TIME__ da "18:42:05". Sirven para responder de un vistazo
// "¿lo que tengo flasheado es lo que acabo de compilar?", que es justo lo que
// no se podia saber mirando la placa.
#define FW_FECHA __DATE__
#define FW_HORA  __TIME__
