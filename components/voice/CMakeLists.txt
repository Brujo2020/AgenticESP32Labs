idf_component_register(
    SRCS "voice.c"
    INCLUDE_DIRS "include"
    REQUIRES board audio ajustes espressif__esp_websocket_client espressif__cjson
)
