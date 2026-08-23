idf_component_register(
    SRCS "voice.c"
    INCLUDE_DIRS "include"
    REQUIRES board audio ajustes bateria net esp_system
             espressif__esp_websocket_client espressif__cjson
    # esp_timer: vencimiento de vistas y preguntas (ya se usaba).
    # heap: heap_caps_malloc + esp_ptr_external_ram, para colocar el colchon
    # de audio y el pre-roll en PSRAM con fallback a RAM interna.
    PRIV_REQUIRES esp_timer heap
)
