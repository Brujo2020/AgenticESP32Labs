idf_component_register(
    SRCS "main.c"
    INCLUDE_DIRS "."
    REQUIRES display touch audio net hud voice ajustes bateria nvs_flash esp_timer heap
)
