idf_component_register(
    SRCS "net.c" "wifi_redes.c"
    INCLUDE_DIRS "include"
    REQUIRES esp_wifi esp_netif esp_event nvs_flash esp_http_client lwip espressif__cjson espressif__mdns
)

# Credenciales de siembra para una placa recien flasheada, opcionales y fuera
# del codigo fuente. Se usan solo si NVS esta vacio; despues mandan las redes
# guardadas en el dispositivo.
#
#   idf.py build -DWIFI_SSID_DEFECTO='"MiRed"' -DWIFI_PASS_DEFECTO='"clave"'
#
# Se pasan asi, y no escritas en un fichero, para que la contrasena del WiFi
# no acabe versionada en el repositorio como estaba antes.
if(DEFINED WIFI_SSID_DEFECTO)
    target_compile_definitions(${COMPONENT_LIB} PRIVATE
        WIFI_SSID_DEFECTO=${WIFI_SSID_DEFECTO})
endif()
if(DEFINED WIFI_PASS_DEFECTO)
    target_compile_definitions(${COMPONENT_LIB} PRIVATE
        WIFI_PASS_DEFECTO=${WIFI_PASS_DEFECTO})
endif()
