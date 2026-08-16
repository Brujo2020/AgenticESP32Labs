idf_component_register(
    SRCS "bateria.c"
    INCLUDE_DIRS "include"
    REQUIRES board esp_adc driver esp_timer
)
