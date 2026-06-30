# Descarga de datos satelitales Sentinel-5P (TROPOMI)

Proceso automatizado y optimizado para la descarga masiva de datos satelitales, enfocado en la columna troposférica de NO₂ del instrumento TROPOMI a bordo de Sentinel-5P (Copernicus, ESA). 

*Este trabajo ha sido desarrollado como parte de una tesis de magíster y ha sido posible gracias al proyecto FONDECYT N°1241477.*

---

## Configuración (Inputs)

Antes de ejecutar el script, es necesario configurar las variables globales ubicadas al inicio del código. Estos son los **inputs** que definen qué, cuándo y dónde se descargará la información:

1. **Credenciales de Copernicus:**
   Debes estar registrado en el [Copernicus Data Space Ecosystem](https://dataspace.copernicus.eu/).
   ```python
   USERNAME = 'tu_correo@ejemplo.com'
   PASSWORD = 'tu_contraseña'
   ```
2. **Rango Temporal:**
   Define la ventana de tiempo de tu estudio en formato YYYY-MM-DD.
   ```python
   FECHA_INICIO = "2023-08-21"
   FECHA_FIN = "2023-12-31"
   ```
3. **Dominio espacial (Bounding Box):**
   Ajusta los límites geográficos ([min, max]) para recortar la órbita. Por defecto, está configurado para Chile continental.
   ```python
   LAT_LIMITS = [-56.5, -16.5]
   LON_LIMITS = [-76.5, -65.5]
   ```
4. **Directorio de salida:**
   Nombre de la carpeta donde se almacenarán los archivos procesados (se creará automáticamente si no existe).
   ```python
   CARPETA_DESTINO = "Input"
   ```

## Resultados esperados (Outputs)
El script no descarga las pesadas órbitas crudas de manera definitiva. En su lugar, el pipeline extrae inteligentemente solo los píxeles de interés y genera archivos altamente optimizados.

- **Ubicación**: Todos los archivos se guardarán en el directorio definido en ```CARPETA_DESTINO```.
- **Nomenclatura**: ```<Nombre_Original_de_la_Orbita>_reduced_chile.nc```
- **Reducción de tamaño**: Pasan de ser archivos crudos de **~500 MB** a archivos recortados de apenas unos pocos Megabytes.
- **Estructura Interna**: El archivo NetCDF4 de salida preserva intacta la estricta jerarquía de grupos de la ESA (revisar manual de descarga. Contiene exactamente 4 grupos:
    - ```/PRODUCT```
    - ```/PRODUCT/SUPPORT_DATA/GEOLOCATIONS```
    - ```/PRODUCT/SUPPORT_DATA/INPUT_DATA```
    - ```/PRODUCT/SUPPORT_DATA/DETAILED_RESULTS```

## Arquitectura del pipeline
Este flujo de trabajo automatizado resuelve los principales cuellos de botella del manejo de datos satelitales espaciales a través de 4 fases lógicas:

### Fase 1: Autenticación dinámica (```get_token```)
- La API de Copernicus expira los tokens de acceso cada 10 minutos (600 segundos), provocando caídas en descargas masivas.
- El script almacena el token de forma global y calcula su tiempo de expiración. Antes de cada solicitud, verifica si el token actual tiene al menos 2 minutos de vida restante. Si es así, lo reutiliza; de lo contrario, solicita uno nuevo. Esto garantiza ejecuciones ininterrumpidas durante días.

### Fase 2: Consulta API y Tolerancia a Versiones (```buscar_productos_dia```)
- Se utiliza la API OData de Copernicus mediante un filtro geográfico (```OData.CSC.Intersects```) y temporal estricto (00:00 a 23:59 UTC).
- Incorpora la condición ```(contains(Name,'OFFL') or contains(Name,'RPRO'))```. Esto hace que, si la Agencia Espacial Europea (ESA) aún no ha reprocesado los datos de un año reciente (2023-2024), descargará la versión Offline (```OFFL```). Si es un año antiguo (2018-2021), descargará la Reprocessed (```RPRO```).

### Fase 3: Inteligencia Espacial y Selección de Órbita Maestra (```__main__```)
- Se aíslan las pasadas del satélite correspondientes a las horas de sobrepaso en Chile (bloques T17, T18, T19).
- TROPOMI suele superponer dos órbitas marginales sobre Chile en un mismo día. Para evitar promediar bordes de órbitas deformados, el script lee el ```Footprint``` (huella en WKT) del archivo directamente desde los metadatos de la API, lo convierte a un polígono con la librería ```shapely``` y calcula su área de intersección real con la caja de estudio (BBOX). 
- Ordena los candidatos de mayor a menor cobertura. Descarga la órbita con más superposición y, una vez recortada con éxito, ejecuta un ```break``` para ignorar las órbitas marginales del mismo día, ahorrando ancho de banda, tiempo y almacenamiento.

### Fase 4: Descarga Temporal y Recorte Estructural (```procesar_orbita``` y ```recortar_nc```)
- Baja el archivo crudo (~500 MB) en fragmentos (```chunk_size=8192```) a un archivo temporal.
- Utilizando ```xarray```, encuentra los índices exactos del BBOX. Luego, en lugar de aplanar el archivo NetCDF original (lo que destruiría los metadatos), abre meticulosamente los 4 sub-grupos exigidos por los modelos atmosféricos, los recorta espacialmente y los reensambla en el nuevo archivo NetCDF de bajo peso (```mode='a'```).
- Finalmente, elimina de manera automática el archivo crudo temporal para no saturar el disco duro.

## Dependencias Requeridas
Asegúrate de instalar las siguientes librerías antes de ejecutar el script:
```python
   pip install requests xarray numpy netCDF4 shapely
```

## Diagrama de flujo del proceso de descarga
```mermaid
graph TD
    %% Definición de Estilos
    classDef inicio_fin fill:#2c3e50,stroke:#34495e,stroke-width:2px,color:#fff,font-weight:bold;
    classDef proceso fill:#ecf0f1,stroke:#bdc3c7,stroke-width:2px,color:#2c3e50;
    classDef decision fill:#f39c12,stroke:#e67e22,stroke-width:2px,color:#fff;
    classDef espacial fill:#3498db,stroke:#2980b9,stroke-width:2px,color:#fff;
    classDef archivo fill:#27ae60,stroke:#2ecc71,stroke-width:2px,color:#fff;

    A([Inicio: Definir Fechas y BBOX Chile Central]):::inicio_fin --> B{¿Día dentro <br>del rango?}:::decision
    
    B -- Sí --> C[Gestión de Token: API Copernicus]:::proceso
    C --> D[Consulta OData OData.CSC.Intersects <br> Tolerancia a versiones: OFFL o RPRO]:::proceso
    
    D --> E{¿Existen <br>pasadas hoy?}:::decision
    E -- No --> F[Avanzar al día siguiente]:::proceso --> B
    
    E -- Sí --> G[Evaluación Geométrica: <br>Extraer huellas WKT de la API]:::espacial
    G --> H[Calcular área de intersección matemática <br> Footprint vs BBOX]:::espacial
    H --> I[Ordenar órbitas por mayor cobertura]:::espacial
    I --> J[Seleccionar Órbita Maestra]:::archivo
    
    J --> K[Descarga temporal en Streaming chunks]:::proceso
    
    K --> L{¿Archivo > <br> Umbral MB?}:::decision
    L -- No --> M[Descartar archivo corrupto]:::proceso --> F
    
    L -- Sí --> N[Geoprocesamiento en Memoria xarray]:::espacial
    N --> O[Enmascaramiento y Extracción de sub-grupos:<br>- PRODUCT<br>- GEOLOCATIONS<br>- INPUT_DATA<br>- DETAILED_RESULTS]:::espacial
    
    O --> P[(Guardar NetCDF4 Reducido)]:::archivo
    P --> Q[Eliminar NetCDF temporal pesado]:::proceso
    Q --> F
    
    B -- No --> R([Fin: Base de datos lista para FDA]):::inicio_fin
```
