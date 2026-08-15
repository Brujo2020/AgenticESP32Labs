idf_component_register(
    SRCS "main.c"
    INCLUDE_DIRS "."
    REQUIRES display touch audio net hud voice ajustes vistas nvs_flash esp_timer
)
